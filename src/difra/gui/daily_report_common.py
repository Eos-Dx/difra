"""Shared helpers for daily valid-container reports."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import h5py


DEFAULT_REPORT_RECIPIENT = "sdenisov@matur.co.uk"
DEFAULT_REPORT_SENDER = "difra-upload@company.co.uk"
DEFAULT_POINTS = 100
DEFAULT_DPI = 200
DEFAULT_KEYCHAIN_SERVICE = "difra_daily_report_smtp_password"
DEFAULT_EMAIL_SETUP_PASSWORD = "Ulster2025!"
DEFAULT_ENCRYPTED_PASSWORD_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "secrets"
    / "daily_report_smtp_password.enc.json"
)
DEFAULT_EMAIL_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "config"
    / "daily_report_email.json"
)
DEFAULT_REPORT_STATE_FILENAME = "daily_report_state.json"
SAXS_RANGE = (1.0, 3.0)
WAXS_RANGE = (2.0, 21.0)
SAXS_DISTANCE_THRESHOLD_CM = 10.0


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    text = str(value).strip()
    return text if text else default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _as_email_recipients(value: Any, default: str) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        text = _as_text(value, default)
        raw_items = str(text or "").replace(";", ",").split(",")
    recipients = []
    for item in raw_items:
        text = _as_text(item, "").strip()
        if text and text not in recipients:
            recipients.append(text)
    return recipients or [default]


def _config_value(
    config: Optional[Dict[str, Any]],
    key: str,
    env_key: str,
    default: Any,
    *,
    fallback_key: str = "",
    fallback_env_key: str = "",
) -> Any:
    if env_key in os.environ:
        return os.environ.get(env_key)
    if fallback_env_key and fallback_env_key in os.environ:
        return os.environ.get(fallback_env_key)
    if isinstance(config, dict) and key in config:
        return config.get(key)
    if fallback_key and isinstance(config, dict) and fallback_key in config:
        return config.get(fallback_key)
    return default


def _load_json_config(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


def load_report_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    candidates = []
    if config_path is not None:
        candidates.append(Path(config_path))
    root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            root / "resources" / "config" / "global.json",
            root / "resources" / "config" / "main.json",
        ]
    )
    config: Dict[str, Any] = {}
    base_path: Optional[Path] = None
    for candidate in candidates:
        loaded = _load_json_config(candidate)
        if loaded:
            config.update(loaded)
            base_path = Path(candidate)
            break

    overlay_candidates = []
    env_overlay = os.environ.get("DIFRA_DAILY_REPORT_EMAIL_CONFIG")
    if env_overlay:
        overlay_candidates.append(Path(env_overlay))
    if base_path is not None:
        overlay_candidates.append(base_path.parent / "daily_report_email.json")
    overlay_candidates.append(DEFAULT_EMAIL_CONFIG_PATH)

    seen = set()
    for candidate in overlay_candidates:
        resolved = str(Path(candidate).expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        config.update(_load_json_config(Path(candidate).expanduser()))
    return config


def _parse_report_datetime(value: Any) -> Optional[datetime]:
    text = _as_text(value, "")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _report_state_path(config: Optional[Dict[str, Any]], output_dir: Path) -> Path:
    configured = _as_text(
        _config_value(
            config,
            "daily_report_state_path",
            "DIFRA_DAILY_REPORT_STATE_PATH",
            "",
        )
    )
    if configured:
        return Path(configured).expanduser()
    return Path(output_dir) / DEFAULT_REPORT_STATE_FILENAME


def _load_report_state(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_report_state(path: Path, state: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _append_report_attempt(
    state: Dict[str, Any],
    *,
    result: Any,
    manifest: Dict[str, Any],
    email_result: Dict[str, Any],
    period_start: datetime,
    period_end: datetime,
) -> None:
    attempts = state.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempts.append(
        {
            "attemptedAt": datetime.now().isoformat(timespec="seconds"),
            "periodStart": period_start.isoformat(timespec="seconds"),
            "periodEnd": period_end.isoformat(timespec="seconds"),
            "sent": bool(email_result.get("sent")),
            "skipped": bool(email_result.get("skipped")),
            "message": _as_text(email_result.get("message"), ""),
            "zipPath": str(result.zip_path or ""),
            "scanned": int(result.scanned),
            "validContainers": int(result.valid_containers),
            "imageCount": len(result.images),
            "projectIds": manifest.get("projectIds", []),
            "matadorUploaded": int(manifest.get("matadorUploaded", 0) or 0),
        }
    )
    state["attempts"] = attempts[-200:]


def _safe_token(value: str, fallback: str = "unknown") -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    token = "_".join(part for part in token.split("_") if part)
    return token or fallback


def _candidate_containers(roots: Iterable[Path], *, since: Optional[datetime]) -> List[Path]:
    paths: List[Path] = []
    min_mtime = since.timestamp() if since is not None else None
    for root in roots:
        folder = Path(root)
        if not folder.exists():
            continue
        for path in folder.rglob("*.h5"):
            name_upper = path.name.upper()
            if "H5OLD" in name_upper:
                continue
            if not (path.name.endswith(".nxs.h5") or path.name.endswith(".h5")):
                continue
            if min_mtime is not None and path.stat().st_mtime < min_mtime:
                continue
            paths.append(path)
    return sorted(set(paths))


def _container_report_datetime(path: Path) -> Optional[datetime]:
    try:
        with h5py.File(path, "r") as h5f:
            for attr_name in (
                "acquisition_date",
                "creation_timestamp",
                "created_at",
                "timestamp_start",
                "lock_timestamp",
                "archived_timestamp",
            ):
                text = _as_text(h5f.attrs.get(attr_name), "").strip()
                if not text:
                    continue
                for candidate in (
                    text,
                    text.replace("Z", ""),
                    text.replace(" ", "T"),
                ):
                    try:
                        return datetime.fromisoformat(candidate)
                    except Exception:
                        continue
    except Exception:
        return None
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime)
    except Exception:
        return None


def _filter_containers_for_date(container_paths: Iterable[Path], report_date) -> List[Path]:
    target = report_date.isoformat()
    selected: List[Path] = []
    for path in container_paths:
        stamp = _container_report_datetime(Path(path))
        if stamp is not None and stamp.date().isoformat() == target:
            selected.append(Path(path))
    return sorted(set(selected))
