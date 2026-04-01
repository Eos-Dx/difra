"""Higher-level session lifecycle workflows shared by GUI mixins."""

from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatch
import logging
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict, Iterable, List, Optional
import zipfile

import h5py

from difra.gui.matador_upload_api import (
    MatadorCreateSessionRequest,
    MatadorFindOrCreateSessionRequest,
    MatadorRegisterFileRequest,
    MatadorUploadContainerRequest,
    build_matador_upload_api,
    sha256_file,
)
from difra.gui.main_window_ext.technical.helpers import _get_difra_base_folder
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
    archived_complete: int = 0
    archived_not_complete: int = 0


@dataclass(frozen=True)
class UploadStubResult:
    """Upload response payload for Matador workflow."""

    success: bool
    upload_session_id: str
    message: str
    bytes_uploaded: int
    local_checksum_sha256: str
    response_checksum_sha256: str
    remote_container_id: str
    zip_file_id: str = ""
    zip_upload_status: str = ""
    zip_processing_status: str = ""
    zip_checksum_sha256: str = ""
    zip_size_bytes: int = 0
    zip_path: str = ""
    h5_file_id: str = ""
    h5_upload_status: str = ""
    h5_processing_status: str = ""


class SessionLifecycleActions:
    """Shared lifecycle actions used by session-related GUI flows."""

    SESSION_STATE_ATTR = "session_state"
    SESSION_STATE_REASON_ATTR = "session_state_reason"
    SESSION_STATE_UPDATED_ATTR = "session_state_updated_at"
    TRANSFER_STATUS_ATTR = "transfer_status"
    TRANSFER_STATUS_NOT_COMPLETE = "not_complete"
    TRANSFER_STATUS_UNSENT = "unsent"
    COMPLETION_STATUS_ATTR = "session_completion_status"
    COMPLETION_STATUS_COMPLETE = "complete"
    COMPLETION_STATUS_NOT_COMPLETE = "not_complete"

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

    @staticmethod
    def _notify_progress(
        progress_callback: Optional[Any],
        *,
        message: str,
        current: Optional[int] = None,
        total: Optional[int] = None,
        kind: str = "",
        container_path: Optional[Path] = None,
    ) -> None:
        if not callable(progress_callback):
            return
        try:
            progress_callback(
                {
                    "message": str(message or "").strip(),
                    "current": current,
                    "total": total,
                    "kind": str(kind or "").strip(),
                    "container_path": str(container_path) if container_path else "",
                }
            )
        except Exception:
            logger.debug("Suppressed session send progress callback exception", exc_info=True)

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

    @staticmethod
    def _resolve_old_format_archive_root(
        config: Optional[Dict[str, Any]] = None,
        archive_folder: Optional[Path] = None,
    ) -> Path:
        cfg = config or {}
        configured = cfg.get("old_format_archive_folder")
        if configured:
            return Path(configured)
        return SessionOldFormatExporter.resolve_old_format_root(
            config=config,
            archive_folder=archive_folder,
        )

    @classmethod
    def resolve_matador_logs_root(cls, config: Optional[Dict[str, Any]] = None) -> Path:
        cfg = config or {}
        configured = str(cfg.get("matador_logs_folder") or "").strip()
        if configured:
            path = Path(configured)
        else:
            difra_base = Path(_get_difra_base_folder(cfg))
            path = difra_base.parent / "matador_logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def _zip_directory(cls, source_dir: Path, output_zip: Path) -> Path:
        source_dir = Path(source_dir)
        output_zip = Path(output_zip)
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(source_dir.rglob("*")):
                if not file_path.is_file():
                    continue
                arcname = file_path.relative_to(source_dir.parent)
                zf.write(file_path, arcname=str(arcname))
        return output_zip

    @classmethod
    def _prepare_old_format_payload(
        cls,
        session_path: Path,
        *,
        archive_folder: Path,
        config: Optional[Dict[str, Any]] = None,
    ):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        temp_root = Path(archive_folder) / ".matador_old_format_tmp" / (
            f"{cls._safe_token(Path(session_path).stem, 'session')}_{stamp}"
        )
        temp_root.mkdir(parents=True, exist_ok=True)
        summary = SessionOldFormatExporter.export_from_session_container(
            session_path,
            config=config,
            archive_folder=archive_folder,
            target_root=temp_root,
        )
        export_dir = Path(summary.export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)
        zip_path = cls._zip_directory(export_dir, export_dir.with_suffix(".zip"))

        archive_root = cls._resolve_old_format_archive_root(
            config=config,
            archive_folder=archive_folder,
        )
        archive_root.mkdir(parents=True, exist_ok=True)
        archived_export_dir = archive_root / (
            f"{export_dir.name}_{cls._safe_token(Path(session_path).stem, 'session')}_{stamp}"
        )
        suffix = 1
        while archived_export_dir.exists():
            suffix += 1
            archived_export_dir = archive_root / (
                f"{export_dir.name}_{cls._safe_token(Path(session_path).stem, 'session')}_{stamp}_{suffix}"
            )
        shutil.move(str(export_dir), str(archived_export_dir))
        return summary, archived_export_dir, zip_path

    @classmethod
    def _read_matador_session_metadata(
        cls,
        session_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
        uploader_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = config or {}
        metadata: Dict[str, Any] = {
            "specimen_id": None,
            "study_id": cfg.get("matador_study_id", 100),
            "machine_id": cfg.get("matador_machine_id", 100),
            "distance_mm": None,
            "exposure_time_sec": None,
            "detector_scope": "PRIMARY",
            "initiated_by": str(
                cfg.get("matador_initiated_by")
                or uploader_id
                or cfg.get("machine_name")
                or cfg.get("setup_name")
                or "difra"
            ),
        }

        try:
            with h5py.File(session_path, "r") as h5f:
                specimen = h5f.attrs.get("specimenId", h5f.attrs.get("sample_id"))
                if specimen not in (None, ""):
                    try:
                        metadata["specimen_id"] = int(specimen)
                    except Exception:
                        metadata["specimen_id"] = None

                study = h5f.attrs.get("matadorStudyId", metadata["study_id"])
                machine = h5f.attrs.get("matadorMachineId", metadata["machine_id"])
                if study not in (None, ""):
                    metadata["study_id"] = int(study)
                if machine not in (None, ""):
                    metadata["machine_id"] = int(machine)

                distance_cm = h5f.attrs.get("distance_cm", h5f.attrs.get("distanceCm"))
                if distance_cm not in (None, ""):
                    metadata["distance_mm"] = int(round(float(distance_cm) * 10.0))

                exposure_values: List[float] = []
                measurements_group = h5f.get("/entry/measurements")
                aliases = set()
                if measurements_group is not None:
                    for point_group in measurements_group.values():
                        for measurement_group in point_group.values():
                            for detector_key, detector_group in measurement_group.items():
                                aliases.add(str(detector_key).strip().upper())
                                try:
                                    integration_ms = detector_group.attrs.get(
                                        "integration_time_ms"
                                    )
                                except Exception:
                                    integration_ms = None
                                if integration_ms not in (None, ""):
                                    exposure_values.append(
                                        round(float(integration_ms) / 1000.0, 6)
                                    )
                if exposure_values:
                    metadata["exposure_time_sec"] = Counter(exposure_values).most_common(1)[0][0]

                aliases.discard("")
                if len(aliases) > 1:
                    metadata["detector_scope"] = "ALL"
                elif aliases == {"SECONDARY"}:
                    metadata["detector_scope"] = "SECONDARY"
                elif aliases:
                    metadata["detector_scope"] = "PRIMARY"
        except Exception:
            logger.warning(
                "Failed to resolve Matador session metadata from H5 for %s; using config fallbacks",
                str(session_path),
                exc_info=True,
            )

        if metadata["distance_mm"] is None:
            fallback_distance_cm = cfg.get("default_session_distance_cm") or cfg.get(
                "default_technical_distance_cm"
            )
            if fallback_distance_cm not in (None, ""):
                metadata["distance_mm"] = int(round(float(fallback_distance_cm) * 10.0))
        if metadata["exposure_time_sec"] is None:
            fallback_exposure = cfg.get("default_exposure_time_sec")
            if fallback_exposure not in (None, ""):
                metadata["exposure_time_sec"] = float(fallback_exposure)
        if metadata["distance_mm"] is None:
            metadata["distance_mm"] = 170
        if metadata["exposure_time_sec"] is None:
            metadata["exposure_time_sec"] = 0.5
        return metadata

    @staticmethod
    def _poll_until_hash_verified(
        upload_api: Any,
        *,
        file_id: int,
        attempts: int = 6,
        delay_sec: float = 2.0,
        progress_callback: Optional[Any] = None,
        status_label: str = "",
        current: Optional[int] = None,
        total: Optional[int] = None,
        container_path: Optional[Path] = None,
    ):
        last_status = None
        for index in range(max(int(attempts), 1)):
            status = upload_api.get_file_status(int(file_id))
            last_status = status
            upload_status = str(status.upload_status or "").upper()
            SessionLifecycleActions._notify_progress(
                progress_callback,
                message=(
                    f"{status_label} verification attempt {index + 1}/{max(int(attempts), 1)}: "
                    f"{upload_status or 'PENDING'}"
                ).strip(),
                current=current,
                total=total,
                kind="file_status",
                container_path=container_path,
            )
            if upload_status == "HASH_VERIFIED":
                return status
            if upload_status == "FAILED":
                return status
            if index < max(int(attempts), 1) - 1:
                time.sleep(max(float(delay_sec), 0.0))
        return last_status

    @classmethod
    def _execute_matador_upload(
        cls,
        archived_path: Path,
        *,
        old_format_zip_path: Path,
        uploader_id: Optional[str] = None,
        upload_api: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
        simulate_failure: bool = False,
        failure_message: Optional[str] = None,
        progress_callback: Optional[Any] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
    ) -> UploadStubResult:
        cls._notify_progress(
            progress_callback,
            message=f"{Path(archived_path).name}: Starting Matador upload...",
            current=current,
            total=total,
            kind="upload_started",
            container_path=Path(archived_path),
        )
        if simulate_failure:
            local_checksum = sha256_file(Path(archived_path))
            return UploadStubResult(
                success=False,
                upload_session_id="",
                message=str(failure_message or "Matador upload failed"),
                bytes_uploaded=int(Path(archived_path).stat().st_size),
                local_checksum_sha256=local_checksum,
                response_checksum_sha256="",
                remote_container_id="",
                zip_checksum_sha256=sha256_file(Path(old_format_zip_path)),
                zip_size_bytes=int(Path(old_format_zip_path).stat().st_size),
                zip_path=str(old_format_zip_path),
            )

        upload_backend = upload_api or build_matador_upload_api(config=config)
        session_metadata = cls._read_matador_session_metadata(
            archived_path,
            config=config,
            uploader_id=uploader_id,
        )

        cls._notify_progress(
            progress_callback,
            message=f"{Path(archived_path).name}: Now creating/finding Matador ingest session...",
            current=current,
            total=total,
            kind="create_session",
            container_path=Path(archived_path),
        )
        ingest_session = upload_backend.find_or_create_session(
            MatadorFindOrCreateSessionRequest(
                study_id=int(session_metadata["study_id"]),
                machine_id=int(session_metadata["machine_id"]),
                distance_in_mm=int(session_metadata["distance_mm"]),
                exposure_time_sec=float(session_metadata["exposure_time_sec"]),
                initiated_by=str(session_metadata["initiated_by"]),
            )
        )

        zip_checksum = sha256_file(Path(old_format_zip_path))
        cls._notify_progress(
            progress_callback,
            message=f"{Path(archived_path).name}: Now registering ZIP...",
            current=current,
            total=total,
            kind="register_zip",
            container_path=Path(archived_path),
        )
        zip_registered = upload_backend.register_file(
            MatadorRegisterFileRequest(
                ingest_session_id=int(ingest_session.id),
                file_name=Path(old_format_zip_path).name,
                file_type="ZIP_PAYLOAD",
                ingest_kind="MEASUREMENT",
                detector_scope=str(session_metadata["detector_scope"]),
                specimen_id=int(session_metadata["specimen_id"])
                if session_metadata["specimen_id"] is not None
                else None,
                expected_sha256=zip_checksum,
                expected_size_bytes=int(Path(old_format_zip_path).stat().st_size),
            )
        )
        cls._notify_progress(
            progress_callback,
            message=f"{Path(archived_path).name}: Now uploading ZIP...",
            current=current,
            total=total,
            kind="upload_zip",
            container_path=Path(archived_path),
        )
        upload_backend.upload_file_bytes(zip_registered.presigned_url, Path(old_format_zip_path))
        zip_status = cls._poll_until_hash_verified(
            upload_backend,
            file_id=int(zip_registered.id),
            attempts=int((config or {}).get("matador_poll_attempts", 6)),
            delay_sec=float((config or {}).get("matador_poll_delay_sec", 2.0)),
            progress_callback=progress_callback,
            status_label=f"{Path(archived_path).name}: ZIP",
            current=current,
            total=total,
            container_path=Path(archived_path),
        )

        h5_checksum = sha256_file(Path(archived_path))
        cls._notify_progress(
            progress_callback,
            message=f"{Path(archived_path).name}: Now registering H5 container...",
            current=current,
            total=total,
            kind="register_h5",
            container_path=Path(archived_path),
        )
        h5_registered = upload_backend.register_file(
            MatadorRegisterFileRequest(
                ingest_session_id=int(ingest_session.id),
                file_name=Path(archived_path).name,
                file_type="HDF5_CONTAINER",
                ingest_kind="MEASUREMENT",
                detector_scope=str(session_metadata["detector_scope"]),
                specimen_id=int(session_metadata["specimen_id"])
                if session_metadata["specimen_id"] is not None
                else None,
                paired_file_id=int(zip_registered.id),
                expected_sha256=h5_checksum,
                expected_size_bytes=int(Path(archived_path).stat().st_size),
            )
        )
        cls._notify_progress(
            progress_callback,
            message=f"{Path(archived_path).name}: Now uploading H5 container...",
            current=current,
            total=total,
            kind="upload_h5",
            container_path=Path(archived_path),
        )
        upload_backend.upload_file_bytes(h5_registered.presigned_url, Path(archived_path))
        h5_status = cls._poll_until_hash_verified(
            upload_backend,
            file_id=int(h5_registered.id),
            attempts=int((config or {}).get("matador_poll_attempts", 6)),
            delay_sec=float((config or {}).get("matador_poll_delay_sec", 2.0)),
            progress_callback=progress_callback,
            status_label=f"{Path(archived_path).name}: H5",
            current=current,
            total=total,
            container_path=Path(archived_path),
        )

        zip_ok = (
            zip_status is not None
            and str(zip_status.upload_status or "").upper() == "HASH_VERIFIED"
        )
        h5_ok = (
            h5_status is not None
            and str(h5_status.upload_status or "").upper() == "HASH_VERIFIED"
        )
        success = bool(zip_ok and h5_ok)
        if success:
            message = (
                f"Matador upload complete: session={ingest_session.id} "
                f"zip={zip_registered.id} h5={h5_registered.id}"
            )
        else:
            zip_state = "" if zip_status is None else str(zip_status.upload_status or "")
            h5_state = "" if h5_status is None else str(h5_status.upload_status or "")
            message = (
                "Matador upload incomplete: "
                f"zip={zip_state or 'unknown'} h5={h5_state or 'unknown'}"
            )
        cls._notify_progress(
            progress_callback,
            message=(
                f"{Path(archived_path).name}: Final upload status: "
                f"{'SUCCESS' if success else 'FAILED'} | {message}"
            ),
            current=current,
            total=total,
            kind="upload_finished",
            container_path=Path(archived_path),
        )

        session_marker = str(ingest_session.id)
        if hasattr(ingest_session, "session_token") and str(ingest_session.session_token or "").strip():
            token_text = str(ingest_session.session_token)
            if token_text.startswith("upload_"):
                session_marker = token_text
        return UploadStubResult(
            success=success,
            upload_session_id=session_marker,
            message=message,
            bytes_uploaded=int(Path(archived_path).stat().st_size),
            local_checksum_sha256=h5_checksum,
            response_checksum_sha256=h5_checksum if h5_ok else "",
            remote_container_id=f"matador://ingest-session/{ingest_session.id}",
            zip_file_id=str(zip_registered.id),
            zip_upload_status="" if zip_status is None else str(zip_status.upload_status),
            zip_processing_status="" if zip_status is None else str(zip_status.processing_status),
            zip_checksum_sha256=zip_checksum,
            zip_size_bytes=int(Path(old_format_zip_path).stat().st_size),
            zip_path=str(old_format_zip_path),
            h5_file_id=str(h5_registered.id),
            h5_upload_status="" if h5_status is None else str(h5_status.upload_status),
            h5_processing_status="" if h5_status is None else str(h5_status.processing_status),
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
        send_status = "successful" if upload_result.success else "unsuccessful"
        send_reason = "" if upload_result.success else str(upload_result.message)
        return cls._write_container_attrs(
            container_path,
            {
                "upload_session_id": str(upload_result.upload_session_id),
                "upload_status": "success" if upload_result.success else "failed",
                "upload_result_message": str(upload_result.message),
                "matador_send_status": send_status,
                "matador_send_reason": send_reason,
                "matador_send_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "upload_bytes": int(upload_result.bytes_uploaded),
                "upload_local_checksum_sha256": str(
                    upload_result.local_checksum_sha256
                ),
                "upload_response_checksum_sha256": str(
                    upload_result.response_checksum_sha256
                ),
                "upload_remote_container_id": str(upload_result.remote_container_id),
                "matador_zip_file_id": str(upload_result.zip_file_id),
                "matador_zip_upload_status": str(upload_result.zip_upload_status),
                "matador_zip_processing_status": str(upload_result.zip_processing_status),
                "matador_zip_checksum_sha256": str(upload_result.zip_checksum_sha256),
                "matador_zip_size_bytes": int(upload_result.zip_size_bytes),
                "matador_zip_path": str(upload_result.zip_path),
                "matador_h5_file_id": str(upload_result.h5_file_id),
                "matador_h5_upload_status": str(upload_result.h5_upload_status),
                "matador_h5_processing_status": str(upload_result.h5_processing_status),
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

    @staticmethod
    def _decode_attr(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

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

    @classmethod
    def archive_session_containers(
        cls,
        container_paths: Iterable[Path],
        *,
        container_manager: Any,
        archive_folder: Path,
        active_session_path: Optional[Path] = None,
        lock_user: Optional[str] = None,
        uploader_id: Optional[str] = None,
        session_ids: Optional[Dict[str, str]] = None,
        force_not_complete: bool = False,
        reason_message: Optional[str] = None,
    ) -> SendArchiveResult:
        """Archive selected session containers without Matador send.

        Containers with no sample image or no completed measurements are archived as
        NOT_COMPLETE and later blocked from send. Complete containers are archived
        as UNSENT so the operator can send them another time.
        """
        result = SendArchiveResult()
        active_resolved = (
            Path(active_session_path).resolve()
            if active_session_path is not None
            else None
        )
        cleanup_folders = set()
        session_id_by_path = session_ids or {}
        resolved_uploader_id = cls._resolve_uploader_id(
            explicit_uploader_id=uploader_id,
            lock_user=lock_user,
        )

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

                completeness = cls.inspect_session_completeness(candidate)
                mark_not_complete = bool(force_not_complete or not completeness["is_complete"])

                try:
                    cls.finalize_session_container(
                        session_path=candidate,
                        container_manager=container_manager,
                        lock_user=lock_user,
                    )
                except Exception as exc:
                    result.failed.append(
                        f"{candidate.name}: lock/validation skipped ({type(exc).__name__}: {exc})"
                    )

                explicit_session_id = session_id_by_path.get(str(candidate))
                archived_path = SessionLifecycleService.archive_session_container(
                    session_path=candidate,
                    session_id=explicit_session_id,
                    archive_folder=archive_folder,
                )

                cls._archive_measurement_artifacts(
                    measurements_folder=candidate.parent,
                    destination_folder=archived_path.parent,
                )
                try:
                    cleanup_folders.add(str(candidate.parent.resolve()))
                except Exception:
                    cleanup_folders.add(str(candidate.parent))

                attrs: Dict[str, Any] = {
                    cls.SESSION_STATE_ATTR: "archived",
                    cls.SESSION_STATE_UPDATED_ATTR: time.strftime("%Y-%m-%d %H:%M:%S"),
                    "uploaded_by": resolved_uploader_id,
                }
                if mark_not_complete:
                    reasons = list(completeness.get("reasons") or [])
                    reason_text = ", ".join(reasons) if reasons else "container incomplete"
                    attrs.update(
                        {
                            cls.SESSION_STATE_REASON_ATTR: "archived_not_complete",
                            cls.TRANSFER_STATUS_ATTR: cls.TRANSFER_STATUS_NOT_COMPLETE,
                            cls.COMPLETION_STATUS_ATTR: cls.COMPLETION_STATUS_NOT_COMPLETE,
                            "upload_status": cls.TRANSFER_STATUS_NOT_COMPLETE,
                            "upload_result_message": (
                                str(reason_message or "").strip()
                                or "Session archived as NOT_COMPLETE; Matador send is blocked."
                            )
                            + f" Reasons: {reason_text}.",
                        }
                    )
                    result.archived_not_complete += 1
                else:
                    attrs.update(
                        {
                            cls.SESSION_STATE_REASON_ATTR: "archived_without_send",
                            cls.TRANSFER_STATUS_ATTR: cls.TRANSFER_STATUS_UNSENT,
                            cls.COMPLETION_STATUS_ATTR: cls.COMPLETION_STATUS_COMPLETE,
                            "upload_status": cls.TRANSFER_STATUS_UNSENT,
                            "upload_result_message": (
                                str(reason_message or "").strip()
                                or "Session archived without Matador send."
                            ),
                        }
                    )
                    result.archived_complete += 1

                cls._write_container_attrs(Path(archived_path), attrs)
                result.archived_paths.append(archived_path)
                result.moved += 1

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

    @classmethod
    def archive_not_complete_session_containers(
        cls,
        container_paths: Iterable[Path],
        *,
        container_manager: Any,
        archive_folder: Path,
        active_session_path: Optional[Path] = None,
        lock_user: Optional[str] = None,
        uploader_id: Optional[str] = None,
        session_ids: Optional[Dict[str, str]] = None,
        reason_message: Optional[str] = None,
    ) -> SendArchiveResult:
        """Compatibility wrapper for explicit NOT_COMPLETE archival flows."""
        return cls.archive_session_containers(
            container_paths=container_paths,
            container_manager=container_manager,
            archive_folder=archive_folder,
            active_session_path=active_session_path,
            lock_user=lock_user,
            uploader_id=uploader_id,
            session_ids=session_ids,
            force_not_complete=True,
            reason_message=reason_message,
        )

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
        progress_callback: Optional[Any] = None,
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
        use_stub_h5_only = (
            not export_old_format
            and upload_api.__class__.__name__ == "StubMatadorUploadApi"
        )
        active_resolved = (
            Path(active_session_path).resolve()
            if active_session_path is not None
            else None
        )

        session_id_by_path = session_ids or {}
        cleanup_folders = set()

        queued_paths = [Path(path) for path in container_paths]
        total_containers = len(queued_paths)

        for item_index, container_path in enumerate(queued_paths, start=1):
            candidate = Path(container_path)
            try:
                if not candidate.exists():
                    continue

                cls._notify_progress(
                    progress_callback,
                    message=f"[{item_index}/{total_containers}] {candidate.name}: Starting send+archive workflow...",
                    current=item_index,
                    total=total_containers,
                    kind="container_started",
                    container_path=candidate,
                )

                was_active = False
                if active_resolved is not None:
                    try:
                        was_active = candidate.resolve() == active_resolved
                    except Exception:
                        was_active = False

                try:
                    cls._notify_progress(
                        progress_callback,
                        message=f"[{item_index}/{total_containers}] {candidate.name}: Finalizing session container...",
                        current=item_index,
                        total=total_containers,
                        kind="finalize_container",
                        container_path=candidate,
                    )
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

                old_format_zip_path = None
                if not use_stub_h5_only:
                    try:
                        cls._notify_progress(
                            progress_callback,
                            message=f"[{item_index}/{total_containers}] {candidate.name}: Building old-format folder and ZIP...",
                            current=item_index,
                            total=total_containers,
                            kind="prepare_old_format",
                            container_path=candidate,
                        )
                        _summary, archived_old_format_dir, old_format_zip_path = (
                            cls._prepare_old_format_payload(
                                candidate,
                                archive_folder=archive_folder,
                                config=config,
                            )
                        )
                        result.old_format_paths.append(archived_old_format_dir)
                        cls._notify_progress(
                            progress_callback,
                            message=f"[{item_index}/{total_containers}] {candidate.name}: ZIP folder with old-format data is ready.",
                            current=item_index,
                            total=total_containers,
                            kind="old_format_ready",
                            container_path=candidate,
                        )
                    except Exception as exc:
                        result.old_format_failed.append(f"{candidate.name}: {exc}")
                        old_format_zip_path = None

                explicit_session_id = session_id_by_path.get(str(candidate))
                try:
                    cls._notify_progress(
                        progress_callback,
                        message=f"[{item_index}/{total_containers}] {candidate.name}: Archiving H5 container...",
                        current=item_index,
                        total=total_containers,
                        kind="archive_container",
                        container_path=candidate,
                    )
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

                if use_stub_h5_only:
                    upload_result = cls.execute_upload_stub(
                        Path(archived_path),
                        uploader_id=resolved_uploader_id,
                        upload_session_id=str(upload_session_id or "").strip()
                        or cls.create_upload_session_id(uploader_id=resolved_uploader_id),
                        upload_api=upload_api,
                        simulate_failure=simulate_upload_failure,
                        failure_message=(
                            "Matador upload failed (simulated)"
                            if simulate_upload_failure
                            else None
                        ),
                    )
                elif old_format_zip_path is None:
                    upload_result = UploadStubResult(
                        success=False,
                        upload_session_id="",
                        message="Old-format ZIP payload was not generated",
                        bytes_uploaded=int(Path(archived_path).stat().st_size)
                        if Path(archived_path).exists()
                        else 0,
                        local_checksum_sha256=sha256_file(Path(archived_path))
                        if Path(archived_path).exists()
                        else "",
                        response_checksum_sha256="",
                        remote_container_id="",
                    )
                else:
                    upload_result = cls._execute_matador_upload(
                        Path(archived_path),
                        old_format_zip_path=Path(old_format_zip_path),
                        uploader_id=resolved_uploader_id,
                        upload_api=upload_api,
                        config=config,
                        simulate_failure=simulate_upload_failure,
                        failure_message=(
                            "Matador upload failed (simulated)"
                            if simulate_upload_failure
                            else None
                        ),
                        progress_callback=progress_callback,
                        current=item_index,
                        total=total_containers,
                    )
                if upload_result.upload_session_id and not result.upload_session_id:
                    result.upload_session_id = str(upload_result.upload_session_id)
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
                    cls._notify_progress(
                        progress_callback,
                        message=f"[{item_index}/{total_containers}] {candidate.name}: SUCCESS - ZIP and H5 container uploaded and verified.",
                        current=item_index,
                        total=total_containers,
                        kind="container_done",
                        container_path=Path(archived_path),
                    )
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
                    cls._notify_progress(
                        progress_callback,
                        message=f"[{item_index}/{total_containers}] {candidate.name}: FAILED - {upload_result.message}",
                        current=item_index,
                        total=total_containers,
                        kind="container_failed",
                        container_path=Path(archived_path),
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
                cls._notify_progress(
                    progress_callback,
                    message=f"[{item_index}/{total_containers}] {candidate.name}: FAILED - unexpected error ({exc})",
                    current=item_index,
                    total=total_containers,
                    kind="container_failed",
                    container_path=candidate,
                )

        for folder_str in sorted(cleanup_folders):
            try:
                result.cleaned_artifacts += cls._cleanup_measurement_artifacts(
                    Path(folder_str)
                )
            except Exception as exc:
                result.failed.append(f"cleanup {folder_str}: {exc}")

        return result
