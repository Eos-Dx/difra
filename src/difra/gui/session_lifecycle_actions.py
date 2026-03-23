"""Higher-level session lifecycle workflows shared by GUI mixins."""

from dataclasses import dataclass, field
from fnmatch import fnmatch
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict, Iterable, List, Optional

import h5py

from difra.gui.matador_upload_api import (
    MatadorCreateSessionRequest,
    MatadorUploadContainerRequest,
    build_matador_upload_api,
    sha256_file,
)
from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.gui.session_old_format_exporter import SessionOldFormatExporter

logger = logging.getLogger(__name__)


@dataclass
class SendArchiveResult:
    """Result summary for batch send+archive workflow."""

    moved: int = 0
    failed: List[str] = field(default_factory=list)
    archived_paths: List[Path] = field(default_factory=list)
    archived_active_session: bool = False
    old_format_paths: List[Path] = field(default_factory=list)
    old_format_failed: List[str] = field(default_factory=list)
    cleaned_artifacts: int = 0
    upload_session_id: str = ""
    upload_success: int = 0
    upload_failed: int = 0


@dataclass(frozen=True)
class UploadStubResult:
    """Stub response payload for Matador upload workflow."""

    success: bool
    upload_session_id: str
    message: str
    bytes_uploaded: int
    local_checksum_sha256: str
    response_checksum_sha256: str
    remote_container_id: str


class SessionLifecycleActions:
    """Shared lifecycle actions used by session-related GUI flows."""

    SESSION_STATE_ATTR = "session_state"
    SESSION_STATE_REASON_ATTR = "session_state_reason"
    SESSION_STATE_UPDATED_ATTR = "session_state_updated_at"

    DEFAULT_MEASUREMENT_CLEANUP_PATTERNS = [
        "*.txt",
        "*.dsc",
        "*.npy",
        "*.t3pa",
        "*.poni",
        "*_state.json",
    ]

    @staticmethod
    def _safe_token(value: Optional[str], fallback: str = "unknown") -> str:
        token = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in str(value or "")
        ).strip("_")
        return token or fallback

    @staticmethod
    def _resolve_uploader_id(
        explicit_uploader_id: Optional[str] = None,
        lock_user: Optional[str] = None,
    ) -> str:
        for value in (explicit_uploader_id, lock_user):
            text = str(value or "").strip()
            if text:
                return text
        return "unknown"

    @classmethod
    def _write_container_attrs(cls, container_path: Path, attrs: Dict[str, Any]) -> bool:
        """Write attrs to a possibly locked HDF5 container (best effort)."""
        path = Path(container_path)
        original_mode: Optional[int] = None
        try:
            try:
                original_mode = path.stat().st_mode
                if not os.access(path, os.W_OK):
                    os.chmod(path, original_mode | 0o200)
            except Exception:
                original_mode = None

            with h5py.File(path, "a") as h5f:
                for key, value in attrs.items():
                    h5f.attrs[str(key)] = value
            return True
        except Exception as exc:
            logger.warning(
                "Failed to write container attrs: path=%s keys=%s error=%s",
                str(path),
                ",".join(sorted(str(key) for key in attrs.keys())),
                exc,
                exc_info=True,
            )
            return False
        finally:
            if original_mode is not None:
                try:
                    os.chmod(path, original_mode)
                except Exception as exc:
                    logger.warning(
                        "Failed to restore container file mode: path=%s error=%s",
                        str(path),
                        exc,
                        exc_info=True,
                    )

    @classmethod
    def create_upload_session_id(
        cls,
        *,
        uploader_id: Optional[str] = None,
        lock_user: Optional[str] = None,
    ) -> str:
        """Create deterministic upload-session identifier for Matador stub flow."""
        resolved_uploader = cls._resolve_uploader_id(
            explicit_uploader_id=uploader_id,
            lock_user=lock_user,
        )
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return f"upload_{cls._safe_token(resolved_uploader)}_{stamp}"

    @classmethod
    def execute_upload_stub(
        cls,
        container_path: Path,
        *,
        uploader_id: Optional[str] = None,
        lock_user: Optional[str] = None,
        upload_session_id: Optional[str] = None,
        upload_api: Optional[Any] = None,
        simulate_failure: bool = False,
        failure_message: Optional[str] = None,
    ) -> UploadStubResult:
        """Simulate Matador upload and return response payload."""
        path = Path(container_path)
        resolved_uploader = cls._resolve_uploader_id(
            explicit_uploader_id=uploader_id,
            lock_user=lock_user,
        )
        resolved_session_id = upload_session_id or cls.create_upload_session_id(
            uploader_id=resolved_uploader
        )
        local_checksum = sha256_file(path)
        bytes_uploaded = int(path.stat().st_size) if path.exists() else 0
        upload_backend = upload_api or build_matador_upload_api()
        upload_request = MatadorUploadContainerRequest(
            upload_session_id=resolved_session_id,
            operator_id=resolved_uploader,
            local_container_id=cls._safe_token(path.stem, "container"),
            file_name=path.name,
            file_size_bytes=bytes_uploaded,
            file_sha256=local_checksum,
        )
        backend_response = upload_backend.upload_container(
            upload_request,
            container_path=path,
        )
        success = bool(backend_response.success) and not bool(simulate_failure)
        message = (
            str(backend_response.message or "Matador upload accepted (stub)")
            if not simulate_failure
            else str(failure_message or "Matador upload failed (stub)")
        )
        response_checksum = (
            str(backend_response.received_sha256 or local_checksum) if success else ""
        )
        remote_container_id = str(backend_response.remote_container_id or "") if success else ""
        return UploadStubResult(
            success=success,
            upload_session_id=resolved_session_id,
            message=message,
            bytes_uploaded=bytes_uploaded,
            local_checksum_sha256=local_checksum,
            response_checksum_sha256=response_checksum,
            remote_container_id=remote_container_id,
        )

    @classmethod
    def append_upload_attempt_log(
        cls,
        container_path: Path,
        *,
        operator_id: str,
        upload_result: UploadStubResult,
    ) -> bool:
        """Append human-readable upload attempt line into container attrs."""
        path = Path(container_path)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        status = "success" if upload_result.success else "failed"
        line = (
            f"{timestamp} | operator={operator_id} | "
            f"upload_session={upload_result.upload_session_id} | "
            f"status={status} | message={upload_result.message}"
        )

        previous_text = ""
        try:
            with h5py.File(path, "r") as h5f:
                raw = h5f.attrs.get("upload_attempts_log", "")
                if isinstance(raw, bytes):
                    previous_text = raw.decode("utf-8", errors="replace")
                else:
                    previous_text = str(raw or "")
        except Exception:
            previous_text = ""

        lines = [item for item in previous_text.splitlines() if item.strip()]
        lines.append(line)
        # Keep bounded log size in container attrs.
        lines = lines[-200:]

        return cls._write_container_attrs(
            path,
            {
                "upload_attempts_log": "\n".join(lines),
                "upload_attempt_count": int(len(lines)),
                "last_upload_error": "" if upload_result.success else upload_result.message,
            },
        )

    @classmethod
    def write_upload_metadata(
        cls,
        container_path: Path,
        *,
        uploader_id: Optional[str] = None,
        lock_user: Optional[str] = None,
        upload_timestamp: Optional[str] = None,
    ) -> bool:
        """Persist uploader identity/timestamp in session container metadata."""
        resolved_uploader = cls._resolve_uploader_id(
            explicit_uploader_id=uploader_id,
            lock_user=lock_user,
        )
        resolved_timestamp = upload_timestamp or time.strftime("%Y-%m-%d %H:%M:%S")
        return cls._write_container_attrs(
            container_path,
            {
                "uploaded_by": resolved_uploader,
                "upload_timestamp": resolved_timestamp,
            },
        )

    @classmethod
    def write_upload_result_metadata(
        cls,
        container_path: Path,
        upload_result: UploadStubResult,
    ) -> bool:
        """Persist upload session/result payload in session container metadata."""
        return cls._write_container_attrs(
            container_path,
            {
                "upload_session_id": str(upload_result.upload_session_id),
                "upload_status": "success" if upload_result.success else "failed",
                "upload_result_message": str(upload_result.message),
                "upload_bytes": int(upload_result.bytes_uploaded),
                "upload_local_checksum_sha256": str(
                    upload_result.local_checksum_sha256
                ),
                "upload_response_checksum_sha256": str(
                    upload_result.response_checksum_sha256
                ),
                "upload_remote_container_id": str(upload_result.remote_container_id),
                "upload_finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

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

        # Legacy path was temporary only; remove it entirely when present.
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

        # Best-effort cleanup of now-empty nested directories.
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

    @staticmethod
    def finalize_session_container(
        session_path: Path,
        container_manager: Any,
        lock_user: Optional[str] = None,
    ) -> bool:
        """Ensure session container is locked and ready for archive/upload."""
        changed = SessionLifecycleService.lock_container_if_needed(
            container_path=Path(session_path),
            container_manager=container_manager,
            user_id=lock_user,
        )
        mark_transferred = getattr(container_manager, "mark_container_transferred", None)
        if callable(mark_transferred):
            mark_transferred(Path(session_path), sent=False)
        SessionLifecycleActions._write_container_attrs(
            Path(session_path),
            {
                SessionLifecycleActions.SESSION_STATE_ATTR: "locked",
                SessionLifecycleActions.SESSION_STATE_REASON_ATTR: "finalized_ready_for_send",
                SessionLifecycleActions.SESSION_STATE_UPDATED_ATTR: time.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            },
        )
        return changed

    @classmethod
    def send_and_archive_session_containers(
        cls,
        container_paths: Iterable[Path],
        *,
        container_manager: Any,
        archive_folder: Path,
        active_session_path: Optional[Path] = None,
        lock_user: Optional[str] = None,
        uploader_id: Optional[str] = None,
        upload_username: Optional[str] = None,
        upload_password: Optional[str] = None,
        upload_session_id: Optional[str] = None,
        simulate_upload_failure: bool = False,
        session_ids: Optional[Dict[str, str]] = None,
        config: Optional[Dict[str, Any]] = None,
        export_old_format: Optional[bool] = None,
    ) -> SendArchiveResult:
        """Lock (if needed) and archive selected session containers."""
        result = SendArchiveResult()
        if export_old_format is None:
            export_old_format = bool(
                (config or {}).get("enable_old_format_export", True)
            )
        resolved_uploader_id = cls._resolve_uploader_id(
            explicit_uploader_id=uploader_id,
            lock_user=lock_user,
        )
        upload_api = build_matador_upload_api(config=config)
        resolved_upload_session_id = str(upload_session_id or "").strip()
        if not resolved_upload_session_id:
            create_session_request = MatadorCreateSessionRequest(
                username=str(upload_username or resolved_uploader_id),
                password=str(upload_password or ""),
                operator_id=resolved_uploader_id,
                workstation_id=str(
                    (config or {}).get("machine_name")
                    or (config or {}).get("setup_name")
                    or "DIFRA"
                ),
                client_version=str(
                    (config or {}).get("producer_version")
                    or (config or {}).get("container_version")
                    or "unknown"
                ),
            )
            create_session_response = upload_api.create_session(create_session_request)
            if create_session_response.success and create_session_response.upload_session_id:
                resolved_upload_session_id = create_session_response.upload_session_id
            else:
                # Fallback to local identifier if backend session creation is unavailable.
                resolved_upload_session_id = cls.create_upload_session_id(
                    uploader_id=resolved_uploader_id
                )
                result.failed.append(
                    "Upload session creation failed; using local fallback session id."
                )
        result.upload_session_id = resolved_upload_session_id
        active_resolved = (
            Path(active_session_path).resolve()
            if active_session_path is not None
            else None
        )

        session_id_by_path = session_ids or {}
        cleanup_folders = set()

        for container_path in container_paths:
            candidate = Path(container_path)
            try:
                if not candidate.exists():
                    continue

                was_active = False
                if active_resolved is not None:
                    try:
                        was_active = candidate.resolve() == active_resolved
                    except Exception:
                        was_active = False

                try:
                    cls.finalize_session_container(
                        session_path=candidate,
                        container_manager=container_manager,
                        lock_user=lock_user,
                    )
                except Exception as exc:
                    # Upload path must remain non-blocking even for invalid/broken containers.
                    result.failed.append(
                        f"{candidate.name}: lock/validation skipped ({type(exc).__name__}: {exc})"
                    )

                if export_old_format:
                    try:
                        # Legacy bundle is reconstructed from locked session H5 before archival move.
                        summary = SessionOldFormatExporter.export_from_session_container(
                            candidate,
                            config=config,
                            archive_folder=archive_folder,
                        )
                        result.old_format_paths.append(summary.export_dir)
                    except Exception as exc:
                        result.old_format_failed.append(
                            f"{candidate.name}: {exc}"
                        )

                explicit_session_id = session_id_by_path.get(str(candidate))
                try:
                    archived_path = SessionLifecycleService.archive_session_container(
                        session_path=candidate,
                        session_id=explicit_session_id,
                        archive_folder=archive_folder,
                    )
                except Exception as exc:
                    # Fallback archive strategy: still move container to archive tree.
                    result.failed.append(
                        f"{candidate.name}: primary archive failed ({type(exc).__name__}: {exc})"
                    )
                    archive_stamp = time.strftime("%Y%m%d_%H%M%S")
                    fallback_dir = Path(archive_folder) / (
                        f"fallback_{cls._safe_token(candidate.stem, 'session')}_{archive_stamp}"
                    )
                    suffix = 1
                    while fallback_dir.exists():
                        suffix += 1
                        fallback_dir = Path(archive_folder) / (
                            f"fallback_{cls._safe_token(candidate.stem, 'session')}_{archive_stamp}_{suffix}"
                        )
                    fallback_dir.mkdir(parents=True, exist_ok=False)
                    archived_path = fallback_dir / candidate.name
                    shutil.move(str(candidate), str(archived_path))

                upload_result = cls.execute_upload_stub(
                    Path(archived_path),
                    uploader_id=resolved_uploader_id,
                    upload_session_id=resolved_upload_session_id,
                    upload_api=upload_api,
                    simulate_failure=simulate_upload_failure,
                )
                wrote_upload_meta = cls.write_upload_metadata(
                    Path(archived_path),
                    uploader_id=resolved_uploader_id,
                    lock_user=lock_user,
                )
                wrote_upload_result = cls.write_upload_result_metadata(
                    Path(archived_path),
                    upload_result=upload_result,
                )
                wrote_upload_log = cls.append_upload_attempt_log(
                    Path(archived_path),
                    operator_id=resolved_uploader_id,
                    upload_result=upload_result,
                )
                metadata_write_ok = (
                    bool(wrote_upload_meta)
                    and bool(wrote_upload_result)
                    and bool(wrote_upload_log)
                )
                if not metadata_write_ok:
                    logger.warning(
                        "Upload metadata write failed for archived session: path=%s "
                        "meta=%s result=%s attempts=%s",
                        str(archived_path),
                        wrote_upload_meta,
                        wrote_upload_result,
                        wrote_upload_log,
                    )

                effective_upload_success = bool(upload_result.success and metadata_write_ok)
                mark_transferred = getattr(container_manager, "mark_container_transferred", None)
                if callable(mark_transferred):
                    mark_transferred(Path(archived_path), sent=effective_upload_success)

                if effective_upload_success:
                    result.upload_success += 1
                else:
                    result.upload_failed += 1
                    if upload_result.success and not metadata_write_ok:
                        result.failed.append(
                            f"{candidate.name}: upload metadata write failed"
                        )
                    else:
                        result.failed.append(
                            f"{candidate.name}: upload failed ({upload_result.message})"
                        )
                result.archived_paths.append(archived_path)
                result.moved += 1
                cls._write_container_attrs(
                    Path(archived_path),
                    {
                        cls.SESSION_STATE_ATTR: "archived",
                        cls.SESSION_STATE_REASON_ATTR: "archived_after_send_queue",
                        cls.SESSION_STATE_UPDATED_ATTR: time.strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    },
                )
                cls._archive_measurement_artifacts(
                    measurements_folder=candidate.parent,
                    destination_folder=archived_path.parent,
                )
                try:
                    cleanup_folders.add(str(candidate.parent.resolve()))
                except Exception:
                    cleanup_folders.add(str(candidate.parent))

                if was_active:
                    result.archived_active_session = True
            except Exception as exc:
                result.failed.append(f"{candidate.name}: {exc}")

        for folder_str in sorted(cleanup_folders):
            try:
                result.cleaned_artifacts += cls._cleanup_measurement_artifacts(
                    Path(folder_str)
                )
            except Exception as exc:
                result.failed.append(f"cleanup {folder_str}: {exc}")

        return result
