"""Shared helpers for session container lifecycle actions.

This module centralizes lock/archive behavior so UI mixins can delegate
domain actions instead of duplicating file-management logic.
"""

import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

import h5py


class SessionLifecycleService:
    """Utility methods for lock/archive workflow of session containers."""

    _ARCHIVE_DAY_TOKEN_RE = re.compile(r"(20\d{6})")

    @staticmethod
    def _decode_attr(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _safe_token(value: str, fallback: str = "unknown") -> str:
        token = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (value or ""))
        token = token.strip("_")
        return token or fallback

    @classmethod
    def _resolve_operator_id(
        cls,
        session_path: Path,
        explicit_operator_id: Optional[str] = None,
    ) -> str:
        if explicit_operator_id:
            return cls._decode_attr(explicit_operator_id) or "unknown"

        try:
            with h5py.File(session_path, "r") as h5f:
                root_operator = cls._decode_attr(h5f.attrs.get("operator_id"))
                if root_operator:
                    return root_operator

                user_group = h5f.get("/entry/user")
                if user_group is not None:
                    group_operator = cls._decode_attr(user_group.attrs.get("operator_id"))
                    if group_operator:
                        return group_operator

                lock_operator = cls._decode_attr(h5f.attrs.get("locked_by"))
                if lock_operator:
                    return lock_operator
        except Exception:
            pass

        return "unknown"

    @classmethod
    def _resolve_archive_metadata(
        cls,
        session_path: Path,
        explicit_session_id: Optional[str] = None,
        explicit_operator_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Resolve session/operator/sample/project tokens for archive naming."""
        data = {
            "session_id": cls._decode_attr(explicit_session_id) or "",
            "operator_id": cls._decode_attr(explicit_operator_id) or "",
            "sample_id": "",
            "project_id": "",
            "study_name": "",
        }

        try:
            with h5py.File(session_path, "r") as h5f:
                if not data["session_id"]:
                    data["session_id"] = cls._decode_attr(h5f.attrs.get("session_id"))
                if not data["operator_id"]:
                    data["operator_id"] = cls._decode_attr(h5f.attrs.get("operator_id"))
                specimen = h5f.attrs.get("specimenId")
                if specimen is None:
                    specimen = h5f.attrs.get("sample_id")
                data["sample_id"] = cls._decode_attr(specimen)
                data["project_id"] = cls._decode_attr(h5f.attrs.get("project_id"))
                data["study_name"] = cls._decode_attr(h5f.attrs.get("study_name"))

                if not data["operator_id"]:
                    user_group = h5f.get("/entry/user")
                    if user_group is not None:
                        data["operator_id"] = cls._decode_attr(
                            user_group.attrs.get("operator_id")
                        )
                if not data["sample_id"]:
                    sample_group = h5f.get("/entry/sample")
                    if sample_group is not None:
                        specimen = sample_group.attrs.get("specimenId")
                        if specimen is None:
                            specimen = sample_group.attrs.get("sample_id")
                        data["sample_id"] = cls._decode_attr(specimen)
                if not data["project_id"]:
                    if data["study_name"]:
                        data["project_id"] = data["study_name"]
                    else:
                        sample_group = h5f.get("/entry/sample")
                        if sample_group is not None:
                            data["project_id"] = cls._decode_attr(
                                sample_group.attrs.get("project_id")
                            )
                if not data["operator_id"]:
                    data["operator_id"] = cls._decode_attr(h5f.attrs.get("locked_by"))
        except Exception:
            pass

        if not data["session_id"]:
            data["session_id"] = str(session_path.stem)
        if not data["operator_id"]:
            data["operator_id"] = "unknown"
        if not data["sample_id"]:
            data["sample_id"] = "UNKNOWN"
        if not data["project_id"]:
            data["project_id"] = "UNSPECIFIED"

        return data

    @staticmethod
    def resolve_archive_folder(
        config: Optional[Dict[str, Any]] = None,
        measurements_folder: Optional[Path] = None,
        session_path: Optional[Path] = None,
    ) -> Path:
        """Resolve archive folder using config-first, deterministic fallbacks."""
        cfg = config or {}
        configured = cfg.get("measurements_archive_folder") or cfg.get(
            "session_archive_folder"
        )
        if configured:
            return Path(configured)

        if measurements_folder is not None:
            return Path(measurements_folder).parent / "archive" / "measurements"

        if session_path is not None:
            sp = Path(session_path)
            return sp.parent.parent / "archive" / "measurements"

        return Path.home() / "difra_measurements" / "archive"

    @staticmethod
    def resolve_archive_mirror_folder(
        config: Optional[Dict[str, Any]] = None,
        *,
        archive_kind: str = "measurements",
    ) -> Optional[Path]:
        """Resolve optional secondary archive root used for mirrored copies."""
        cfg = config or {}
        kind = str(archive_kind or "").strip().lower()
        if kind == "technical":
            configured = (
                cfg.get("technical_archive_mirror_folder")
                or cfg.get("measurements_archive_mirror_folder")
                or cfg.get("session_archive_mirror_folder")
            )
        else:
            configured = (
                cfg.get("measurements_archive_mirror_folder")
                or cfg.get("session_archive_mirror_folder")
            )
        if not configured:
            return None
        return Path(configured)

    @classmethod
    def _resolve_archive_mirror_day_token(cls, source_path: Path) -> str:
        source = Path(source_path)
        try:
            from difra.gui.session_old_format_exporter import SessionOldFormatExporter

            def _from_h5(file_path: Path) -> str:
                with h5py.File(file_path, "r") as h5f:
                    acquisition_date = SessionOldFormatExporter._as_text(
                        h5f.attrs.get("acquisition_date"),
                        "",
                    )
                    fallback_timestamps = SessionOldFormatExporter._collect_fallback_timestamps(
                        h5f
                    )
                    creation_timestamp = SessionOldFormatExporter._as_text(
                        h5f.attrs.get("creation_timestamp"),
                        "",
                    )
                    if creation_timestamp:
                        fallback_timestamps.append(creation_timestamp)
                    return SessionOldFormatExporter._resolve_day_token(
                        acquisition_date=acquisition_date,
                        fallback_timestamps=fallback_timestamps,
                    )

            if source.is_file() and source.suffix.lower() in {".h5", ".nxs.h5", ".zip"}:
                sibling_dir = source.with_suffix("")
                if source.suffix.lower() == ".zip" and sibling_dir.exists() and sibling_dir.is_dir():
                    return cls._resolve_archive_mirror_day_token(sibling_dir)
                if source.suffix.lower() != ".zip":
                    return _from_h5(source)

            if source.is_dir():
                raw_candidates = []
                for pattern in ("*.npy", "*.txt", "*.dsc", "*.poni", "*.t3pa"):
                    raw_candidates.extend(sorted(source.rglob(pattern)))
                for candidate in raw_candidates:
                    match = cls._ARCHIVE_DAY_TOKEN_RE.search(str(candidate.name or ""))
                    if match:
                        return match.group(1)

                h5_candidates = sorted(source.rglob("*.nxs.h5")) + sorted(source.rglob("*.h5"))
                for candidate in h5_candidates:
                    try:
                        return _from_h5(candidate)
                    except Exception:
                        continue
        except Exception:
            pass

        for candidate in (source.name, source.stem, source.parent.name):
            match = cls._ARCHIVE_DAY_TOKEN_RE.search(str(candidate or ""))
            if match:
                return match.group(1)
        return time.strftime("%Y%m%d")

    @classmethod
    def copy_archive_item_to_mirror(
        cls,
        source_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
        archive_kind: str = "measurements",
        day_token: Optional[str] = None,
    ) -> Optional[Path]:
        """Copy one archived file/folder into the optional secondary archive root."""
        source = Path(source_path)
        if not source.exists():
            return None

        mirror_root = cls.resolve_archive_mirror_folder(
            config=config,
            archive_kind=archive_kind,
        )
        if mirror_root is None:
            return None

        destination_root = (
            mirror_root
            / "Archive"
            / str(archive_kind or "measurements").strip().lower()
        )
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / source.name

        if source.is_dir():
            shutil.copytree(str(source), str(destination), dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(destination))

        return destination

    @staticmethod
    def lock_container_if_needed(
        container_path: Path,
        container_manager: Any,
        user_id: Optional[str] = None,
    ) -> bool:
        """Lock container only when it is still unlocked.

        Returns True when lock was applied during this call.
        """
        path = Path(container_path)
        if container_manager.is_container_locked(path):
            return False
        container_manager.lock_container(path, user_id=user_id)
        return True

    @classmethod
    def archive_session_container(
        cls,
        session_path: Path,
        session_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        archive_folder: Optional[Path] = None,
        config: Optional[Dict[str, Any]] = None,
        measurements_folder: Optional[Path] = None,
        timestamp: Optional[str] = None,
    ) -> Path:
        """Move a session container into the archive tree and return destination."""
        source = Path(session_path)
        resolved_archive = (
            Path(archive_folder)
            if archive_folder is not None
            else cls.resolve_archive_folder(
                config=config,
                measurements_folder=measurements_folder,
                session_path=source,
            )
        )
        resolved_archive.mkdir(parents=True, exist_ok=True)

        archive_stamp = timestamp or time.strftime("%Y%m%d_%H%M%S")
        metadata = cls._resolve_archive_metadata(
            source,
            explicit_session_id=session_id,
            explicit_operator_id=operator_id,
        )
        sid_token = cls._safe_token(metadata.get("session_id"), fallback="session")
        operator_token = cls._safe_token(
            metadata.get("operator_id"), fallback="unknown"
        )
        sample_token = cls._safe_token(metadata.get("sample_id"), fallback="UNKNOWN")
        project_token = cls._safe_token(
            metadata.get("project_id"), fallback="UNSPECIFIED"
        )
        target_dir = resolved_archive / (
            f"{sid_token}_{operator_token}_{sample_token}_{project_token}_{archive_stamp}"
        )
        suffix = 1
        while target_dir.exists():
            suffix += 1
            target_dir = resolved_archive / (
                f"{sid_token}_{operator_token}_{sample_token}_{project_token}_{archive_stamp}_{suffix}"
            )
        target_dir.mkdir(parents=True, exist_ok=False)

        destination = target_dir / source.name
        shutil.move(str(source), str(destination))
        return destination
