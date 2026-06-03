from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from difra.gui.matador_upload_api import (
    MatadorFindOrCreateSessionRequest,
    sha256_file,
)
from difra.gui.session_lifecycle_common import (
    UploadStubResult,
    _normalize_iso_date,
)
from difra.gui.session_lifecycle_matador_upload_helpers import (
    SessionLifecycleMatadorUploadHelpersMixin,
)
from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.gui.session_old_format_exporter import SessionOldFormatExporter
from difra.gui.session_lifecycle_upload_result_metadata_mixin import (
    SessionLifecycleUploadResultMetadataMixin,
)

SessionLifecycleActions = None


def _actions_module():
    from difra.gui import session_lifecycle_actions as actions

    return actions


def _build_matador_upload_api(*args, **kwargs):
    return _actions_module().build_matador_upload_api(*args, **kwargs)


class SessionLifecycleUploadExecuteMixin(
    SessionLifecycleMatadorUploadHelpersMixin,
    SessionLifecycleUploadResultMetadataMixin,
):
    @classmethod
    def _execute_matador_upload(
        cls,
        archived_path: Path,
        *,
        old_format_zip_path: Path,
        calibration_zip_paths: Optional[List[Path]] = None,
        uploader_id: Optional[str] = None,
        upload_api: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
        simulate_failure: bool = False,
        failure_message: Optional[str] = None,
        progress_callback: Optional[Any] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        batch_session_cache: Optional[Dict[str, Any]] = None,
        batch_calibration_uploaded: Optional[Set[str]] = None,
        matador_manifest_path: Optional[Path] = None,
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

        upload_backend = upload_api or _build_matador_upload_api(config=config)
        session_metadata = cls._read_matador_session_metadata(
            archived_path,
            config=config,
            uploader_id=uploader_id,
        )
        strict_matador_contract = cls._requires_strict_matador_contract(upload_backend)
        defer_measurement_verification = bool(
            (config or {}).get(
                "matador_defer_measurement_verification",
                strict_matador_contract,
            )
        )
        calibration_zip_paths = [
            Path(path) for path in (calibration_zip_paths or []) if Path(path).exists()
        ]
        calibration_group_hash = cls._read_calibration_group_hash_from_h5(
            Path(archived_path),
            config=config,
        )
        batch_group_key = cls._matador_session_bucket_key_from_metadata(session_metadata)
        if not calibration_group_hash:
            calibration_group_hash = cls._safe_token(Path(archived_path).stem, "unknown")
        session_metadata["calibration_group_hash"] = calibration_group_hash

        session_date = _normalize_iso_date(session_metadata.get("session_date"))
        session_metadata["session_date"] = session_date
        if strict_matador_contract and not session_date:
            return cls._matador_blocked_upload_result(
                archived_path=Path(archived_path),
                old_format_zip_path=Path(old_format_zip_path),
                message=(
                    "Matador sessionDate is required for real uploads, "
                    "but acquisition_date is missing or invalid."
                ),
                progress_callback=progress_callback,
                current=current,
                total=total,
            )

        specimen_text = str(session_metadata.get("specimen_text") or "").strip()
        if strict_matador_contract:
            resolved_specimen_id, specimen_resolution_message = (
                cls._resolve_matador_specimen_id_for_upload(
                    upload_api=upload_backend,
                    session_metadata=session_metadata,
                )
            )
            if resolved_specimen_id is None:
                if specimen_text:
                    return cls._matador_blocked_upload_result(
                        archived_path=Path(archived_path),
                        old_format_zip_path=Path(old_format_zip_path),
                        message=(
                            "Matador specimen ID is required for measurement uploads, "
                            f"but container stores '{specimen_text}'. "
                            f"{specimen_resolution_message}"
                        ),
                        progress_callback=progress_callback,
                        current=current,
                        total=total,
                    )
                return cls._matador_blocked_upload_result(
                    archived_path=Path(archived_path),
                    old_format_zip_path=Path(old_format_zip_path),
                    message=(
                        "Matador specimen ID is required for measurement uploads, "
                        f"but none is stored in the container. {specimen_resolution_message}"
                    ),
                    progress_callback=progress_callback,
                    current=current,
                    total=total,
                )
            session_metadata["specimen_id"] = int(resolved_specimen_id)
            session_metadata["specimen_resolution_message"] = specimen_resolution_message

        ingest_session = None
        if batch_group_key and batch_session_cache is not None:
            ingest_session = batch_session_cache.get(batch_group_key)

        if ingest_session is None:
            session_request = MatadorFindOrCreateSessionRequest(
                study_id=int(session_metadata["study_id"]),
                machine_id=int(session_metadata["machine_id"]),
                distance_in_mm=int(session_metadata["distance_mm"]),
                exposure_time_sec=float(session_metadata["exposure_time_sec"]),
                initiated_by=str(session_metadata["initiated_by"]),
                session_date=str(session_metadata.get("session_date") or ""),
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
                session_request
            )
            if batch_group_key and batch_session_cache is not None:
                batch_session_cache[batch_group_key] = ingest_session
        else:
            cls._notify_progress(
                progress_callback,
                message=(
                    f"{Path(archived_path).name}: Reusing Matador ingest session "
                    f"{ingest_session.id} for calibration group."
                ),
                current=current,
                total=total,
                kind="reuse_session",
                container_path=Path(archived_path),
            )

        calibration_manifest_key = cls._matador_calibration_manifest_key(
            session_id=int(ingest_session.id),
            distance_mm=int(session_metadata["distance_mm"]),
            detector_scope=str(session_metadata["detector_scope"]),
            calibration_group_hash=str(calibration_group_hash),
        )
        calibration_result = cls._matador_upload_calibrations(
            archived_path=Path(archived_path),
            old_format_zip_path=Path(old_format_zip_path),
            upload_backend=upload_backend,
            ingest_session=ingest_session,
            session_metadata=session_metadata,
            calibration_zip_paths=calibration_zip_paths,
            calibration_manifest_key=calibration_manifest_key,
            batch_calibration_uploaded=batch_calibration_uploaded,
            matador_manifest_path=matador_manifest_path,
            config=config,
            progress_callback=progress_callback,
            current=current,
            total=total,
        )
        if calibration_result is not None:
            return calibration_result

        return cls._matador_upload_measurement_payload(
            archived_path=Path(archived_path),
            old_format_zip_path=Path(old_format_zip_path),
            upload_backend=upload_backend,
            ingest_session=ingest_session,
            session_metadata=session_metadata,
            defer_measurement_verification=defer_measurement_verification,
            matador_manifest_path=matador_manifest_path,
            config=config,
            progress_callback=progress_callback,
            current=current,
            total=total,
        )
