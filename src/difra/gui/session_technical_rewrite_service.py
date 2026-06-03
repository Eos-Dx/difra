from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import time
from typing import Any, Dict, Optional

import h5py


def _decode_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _next_h5old_path(path: Path) -> Path:
    source = Path(path)
    name = source.name
    if name.endswith(".h5"):
        base = source.with_name(name[:-3])
    else:
        base = source
    first = base.with_name(base.name + ".h5old")
    if not first.exists():
        return first
    index = 2
    while True:
        candidate = base.with_name(base.name + f".h5old{index}")
        if not candidate.exists():
            return candidate
        index += 1


def _technical_container_id(path: Path) -> str:
    with h5py.File(path, "r") as h5f:
        for attr_name in ("container_id", "technical_container_id", "source_container_id"):
            value = _decode_text(h5f.attrs.get(attr_name)).strip()
            if value:
                return value
        technical = h5f.get("/entry/technical")
        if technical is not None:
            for attr_name in ("container_id", "technical_container_id", "source_container_id"):
                value = _decode_text(technical.attrs.get(attr_name)).strip()
                if value:
                    return value
    return ""


def _session_technical_container_id(path: Path) -> str:
    with h5py.File(path, "r") as h5f:
        for snapshot_path in ("/entry/calibration_snapshot", "/entry/technical"):
            snapshot = h5f.get(snapshot_path)
            if snapshot is None:
                continue
            for attr_name in (
                "source_container_id",
                "technical_container_id",
                "container_id",
            ):
                value = _decode_text(snapshot.attrs.get(attr_name)).strip()
                if value:
                    return value
        for attr_name in ("technical_container_id", "source_container_id"):
            value = _decode_text(h5f.attrs.get(attr_name)).strip()
            if value:
                return value
    return ""


def _update_state_payload(payload: Dict[str, Any], technical_id: str) -> Dict[str, Any]:
    updated = dict(payload or {})
    if technical_id:
        updated["CALIBRATION_GROUP_HASH"] = technical_id
        updated["technical_container_id"] = technical_id
        updated["source_container_id"] = technical_id
    updated["technical_rewrite_required_resend"] = True
    updated["technical_rewrite_timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return updated


@dataclass(frozen=True)
class SessionTechnicalRewriteResult:
    session_path: Path
    technical_path: Path
    backup_path: Path
    technical_container_id: str
    state_json_updated: bool
    sidecar_state_json_updated: bool


class SessionTechnicalRewriteService:
    TRANSFER_STATUS_REQ_RESEND = "req_resend"

    def __init__(self, *, writer_module: Any = None):
        if writer_module is None:
            from container.v0_2 import writer as writer_module

        self.writer = writer_module

    def rewrite_session_technical_section(
        self,
        *,
        session_path: Path | str,
        technical_path: Path | str,
        reason: str = "technical calibration section replaced",
        update_sidecar_state_json: bool = True,
    ) -> SessionTechnicalRewriteResult:
        session = Path(session_path)
        technical = Path(technical_path)
        if not session.exists():
            raise FileNotFoundError(f"Session container not found: {session}")
        if not technical.exists():
            raise FileNotFoundError(f"Technical container not found: {technical}")

        technical_id = _technical_container_id(technical)
        backup = _next_h5old_path(session)
        shutil.copy2(session, backup)

        self.writer.copy_technical_to_session(technical, session)
        state_json_updated = self._mark_session_req_resend(
            session,
            technical_id=technical_id,
            technical_path=technical,
            reason=reason,
        )
        sidecar_updated = False
        if update_sidecar_state_json:
            sidecar_updated = self._update_sidecar_state_json(
                session,
                technical_id=technical_id,
            )

        return SessionTechnicalRewriteResult(
            session_path=session,
            technical_path=technical,
            backup_path=backup,
            technical_container_id=technical_id,
            state_json_updated=state_json_updated,
            sidecar_state_json_updated=sidecar_updated,
        )

    def rewrite_sessions_by_technical_id(
        self,
        *,
        session_paths: list[Path | str],
        technical_paths: list[Path | str],
        reason: str = "technical calibration section replaced",
    ) -> list[SessionTechnicalRewriteResult]:
        technical_by_id: Dict[str, Path] = {}
        for technical_path in technical_paths:
            technical = Path(technical_path)
            technical_id = _technical_container_id(technical)
            if technical_id:
                technical_by_id[technical_id] = technical

        results: list[SessionTechnicalRewriteResult] = []
        for session_path in session_paths:
            session = Path(session_path)
            session_technical_id = _session_technical_container_id(session)
            technical = technical_by_id.get(session_technical_id)
            if technical is None:
                continue
            results.append(
                self.rewrite_session_technical_section(
                    session_path=session,
                    technical_path=technical,
                    reason=reason,
                )
            )
        return results

    def _mark_session_req_resend(
        self,
        session: Path,
        *,
        technical_id: str,
        technical_path: Path,
        reason: str,
    ) -> bool:
        state_json_updated = False
        with h5py.File(session, "a") as h5f:
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            h5f.attrs["transfer_status"] = self.TRANSFER_STATUS_REQ_RESEND
            h5f.attrs["technical_rewrite_required_resend"] = True
            h5f.attrs["technical_rewrite_reason"] = str(reason or "")
            h5f.attrs["technical_rewrite_timestamp"] = now
            h5f.attrs["technical_rewrite_source_file"] = str(technical_path)
            if technical_id:
                h5f.attrs["technical_container_id"] = technical_id
                h5f.attrs["source_container_id"] = technical_id

            snapshot = h5f.get("/entry/calibration_snapshot") or h5f.get("/entry/technical")
            if snapshot is not None:
                snapshot.attrs["technical_rewrite_timestamp"] = now
                snapshot.attrs["technical_rewrite_reason"] = str(reason or "")
                if technical_id:
                    snapshot.attrs["source_container_id"] = technical_id
                    snapshot.attrs["technical_container_id"] = technical_id

            raw_state = h5f.attrs.get("meta_json")
            if raw_state is not None:
                try:
                    payload = json.loads(_decode_text(raw_state) or "{}")
                    if isinstance(payload, dict):
                        h5f.attrs["meta_json"] = json.dumps(
                            _update_state_payload(payload, technical_id),
                            indent=2,
                        )
                        state_json_updated = True
                except Exception:
                    state_json_updated = False
        return state_json_updated

    def _update_sidecar_state_json(self, session: Path, *, technical_id: str) -> bool:
        candidates = [
            session.with_name(f"{session.stem}_state.json"),
            session.with_name(f"{session.name}_state.json"),
        ]
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8") or "{}")
                if not isinstance(payload, dict):
                    payload = {}
                candidate.write_text(
                    json.dumps(_update_state_payload(payload, technical_id), indent=2),
                    encoding="utf-8",
                )
                return True
            except Exception:
                return False
        return False
