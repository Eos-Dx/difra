from __future__ import annotations

from fnmatch import fnmatch
import logging
from pathlib import Path
import shutil
import time
from typing import Any, Dict, List, Optional

import h5py

logger = logging.getLogger(__name__)


def _actions_module():
    from difra.gui import session_lifecycle_actions as actions

    return actions


def _session_lifecycle_service():
    return _actions_module().SessionLifecycleService


class SessionLifecycleArchiveSupportMixin:
    @classmethod
    def _archive_measurement_artifacts(
        cls,
        measurements_folder: Path,
        destination_folder: Path,
    ) -> int:
        """Move raw measurement artifacts into the same archive folder as session H5."""
        source = Path(measurements_folder)
        destination = Path(destination_folder)
        if not source.exists() or not source.is_dir():
            return 0

        moved = 0
        patterns = cls.DEFAULT_MEASUREMENT_CLEANUP_PATTERNS
        destination.mkdir(parents=True, exist_ok=True)

        for file_path in sorted(source.rglob("*")):
            if not file_path.is_file():
                continue

            rel = file_path.relative_to(source)
            rel_posix = rel.as_posix()
            if rel_posix.startswith("archive/"):
                continue

            if not any(
                fnmatch(file_path.name, pattern) or fnmatch(rel_posix, pattern)
                for pattern in patterns
            ):
                continue

            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)

            if target.exists():
                stem = target.stem
                suffix = target.suffix
                idx = 2
                while True:
                    alt = target.with_name(f"{stem}_{idx}{suffix}")
                    if not alt.exists():
                        target = alt
                        break
                    idx += 1

            try:
                shutil.move(str(file_path), str(target))
                moved += 1
            except Exception as exc:
                logger.warning(
                    "Failed to archive measurement artifact: src=%s dst=%s error=%s",
                    str(file_path),
                    str(target),
                    exc,
                    exc_info=True,
                )

        return moved

    @classmethod
    def _cleanup_measurement_artifacts(
        cls,
        measurements_folder: Path,
    ) -> int:
        """Remove transient measurement artifacts after successful archive."""
        folder = Path(measurements_folder)
        if not folder.exists() or not folder.is_dir():
            return 0

        removed = 0
        patterns = cls.DEFAULT_MEASUREMENT_CLEANUP_PATTERNS
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(folder).as_posix()
            if rel.startswith("archive/"):
                continue
            if any(fnmatch(file_path.name, p) or fnmatch(rel, p) for p in patterns):
                try:
                    file_path.unlink()
                    removed += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to cleanup measurement artifact: path=%s error=%s",
                        str(file_path),
                        exc,
                        exc_info=True,
                    )

        grpc_folder = folder / "grpc_exposures"
        if grpc_folder.exists() and grpc_folder.is_dir():
            try:
                shutil.rmtree(grpc_folder)
            except Exception as exc:
                logger.warning(
                    "Failed to remove grpc_exposures folder: path=%s error=%s",
                    str(grpc_folder),
                    exc,
                    exc_info=True,
                )

        dirs = sorted(
            [d for d in folder.rglob("*") if d.is_dir()],
            key=lambda d: len(d.parts),
            reverse=True,
        )
        for dir_path in dirs:
            if dir_path == folder:
                continue
            try:
                dir_path.rmdir()
            except OSError:
                continue

        return removed

    @classmethod
    def finalize_session_container(
        cls,
        session_path: Path,
        container_manager: Any,
        lock_user: Optional[str] = None,
    ) -> bool:
        """Ensure session container is locked and ready for archive/upload."""
        changed = _session_lifecycle_service().lock_container_if_needed(
            container_path=Path(session_path),
            container_manager=container_manager,
            user_id=lock_user,
        )
        mark_transferred = getattr(container_manager, "mark_container_transferred", None)
        if callable(mark_transferred):
            mark_transferred(Path(session_path), sent=False)
        cls._write_container_attrs(
            Path(session_path),
            {
                cls.SESSION_STATE_ATTR: "locked",
                cls.SESSION_STATE_REASON_ATTR: "finalized_ready_for_send",
                cls.SESSION_STATE_UPDATED_ATTR: time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        return changed

    @staticmethod
    def _decode_attr(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    @classmethod
    def _coerce_optional_int(cls, value: Any) -> Optional[int]:
        """Return a Matador specimen integer from plain or composite specimen text."""
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return int(value)
        text = cls._decode_attr(value).strip()
        if not text:
            return None
        candidate = text
        if "__" in candidate:
            candidate = candidate.split("__", 1)[0].strip()
        if candidate.startswith(("+", "-")):
            digits = candidate[1:]
            if digits.isdigit():
                return int(candidate)
            return None
        if candidate.isdigit():
            return int(candidate)
        return None

    @classmethod
    def _current_transfer_status(cls, container_path: Path, *, container_manager: Any) -> str:
        try:
            with h5py.File(container_path, "r") as h5f:
                root_status = str(
                    h5f.attrs.get("transfer_status", "") or ""
                ).strip().lower()
            if root_status in {
                cls.TRANSFER_STATUS_NOT_COMPLETE,
                cls.TRANSFER_STATUS_REQ_RESEND,
            }:
                return root_status
        except Exception:
            pass
        get_transfer_status = getattr(container_manager, "get_transfer_status", None)
        if callable(get_transfer_status):
            try:
                return str(get_transfer_status(Path(container_path)) or "").strip().lower()
            except Exception:
                return ""
        try:
            with h5py.File(container_path, "r") as h5f:
                return str(h5f.attrs.get("transfer_status", "") or "").strip().lower()
        except Exception:
            return ""

    @classmethod
    def inspect_session_completeness(cls, session_path: Path) -> Dict[str, Any]:
        """Inspect archived/pending session content and decide if Matador send is allowed."""
        summary: Dict[str, Any] = {
            "is_complete": False,
            "has_sample_image": False,
            "completed_measurements": 0,
            "total_measurements": 0,
            "reasons": [],
        }

        with h5py.File(session_path, "r") as h5f:
            images_group = h5f.get("/entry/images")
            if images_group is not None:
                for image_name in images_group.keys():
                    if not str(image_name).startswith("img_"):
                        continue
                    image_group = images_group[image_name]
                    image_type = cls._decode_attr(
                        image_group.attrs.get("image_type", "")
                    ).strip().lower()
                    if image_type == "sample":
                        summary["has_sample_image"] = True
                        break

            measurements_group = h5f.get("/entry/measurements")
            if measurements_group is not None:
                for point_group in measurements_group.values():
                    for measurement_group in point_group.values():
                        summary["total_measurements"] += 1
                        measurement_status = cls._decode_attr(
                            measurement_group.attrs.get("measurement_status", "")
                        ).strip().lower()
                        if measurement_status == "completed":
                            summary["completed_measurements"] += 1

        reasons: List[str] = []
        if not summary["has_sample_image"]:
            reasons.append("missing sample image")
        if int(summary["completed_measurements"]) <= 0:
            reasons.append("no completed measurements")
        summary["reasons"] = reasons
        summary["is_complete"] = not reasons
        return summary
