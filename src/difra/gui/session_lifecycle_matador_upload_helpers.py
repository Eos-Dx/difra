from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from difra.gui.matador_upload_api import MatadorRegisterFileRequest, sha256_file
from difra.gui.session_lifecycle_common import UploadStubResult


class SessionLifecycleMatadorUploadHelpersMixin:
    @classmethod
    def _matador_blocked_upload_result(
        cls,
        *,
        archived_path: Path,
        old_format_zip_path: Path,
        message: str,
        progress_callback: Optional[Any],
        current: Optional[int],
        total: Optional[int],
    ) -> UploadStubResult:
        cls._notify_progress(
            progress_callback,
            message=f"{Path(archived_path).name}: {message}",
            current=current,
            total=total,
            kind="upload_blocked",
            container_path=Path(archived_path),
        )
        return UploadStubResult(
            success=False,
            upload_session_id="",
            message=message,
            bytes_uploaded=int(Path(archived_path).stat().st_size),
            local_checksum_sha256=sha256_file(Path(archived_path)),
            response_checksum_sha256="",
            remote_container_id="",
            zip_checksum_sha256=sha256_file(Path(old_format_zip_path)),
            zip_size_bytes=int(Path(old_format_zip_path).stat().st_size),
            zip_path=str(old_format_zip_path),
        )

    @classmethod
    def _matador_upload_calibrations(
        cls,
        *,
        archived_path: Path,
        old_format_zip_path: Path,
        upload_backend: Any,
        ingest_session: Any,
        session_metadata: Dict[str, Any],
        calibration_zip_paths: List[Path],
        calibration_manifest_key: str,
        batch_calibration_uploaded: Optional[Set[str]],
        matador_manifest_path: Optional[Path],
        config: Optional[Dict[str, Any]],
        progress_callback: Optional[Any],
        current: Optional[int],
        total: Optional[int],
    ) -> Optional[UploadStubResult]:
        manifest_payload = cls._load_matador_uploaded_manifest(matador_manifest_path)
        upload_calibration = True
        if (
            calibration_manifest_key
            and batch_calibration_uploaded is not None
            and calibration_manifest_key in batch_calibration_uploaded
        ):
            upload_calibration = False
            cls._notify_progress(
                progress_callback,
                message=(
                    f"{Path(archived_path).name}: Calibration already uploaded for "
                    "this Matador calibration key."
                ),
                current=current,
                total=total,
                kind="skip_calibration",
                container_path=Path(archived_path),
            )
        elif cls._matador_manifest_has_verified_calibration(
            manifest_payload,
            distance_mm=int(session_metadata["distance_mm"]),
            calibration_key=calibration_manifest_key,
        ):
            upload_calibration = False
            if batch_calibration_uploaded is not None:
                batch_calibration_uploaded.add(calibration_manifest_key)
            cls._notify_progress(
                progress_callback,
                message=(
                    f"{Path(archived_path).name}: Calibration already recorded in "
                    ".matador_uploaded for this Matador calibration key."
                ),
                current=current,
                total=total,
                kind="skip_calibration_manifest",
                container_path=Path(archived_path),
            )

        calibration_zip_paths_to_upload: List[Path] = []
        if upload_calibration:
            for calibration_zip_path in calibration_zip_paths:
                calibration_zip_path = Path(calibration_zip_path)
                reusable_status = cls._matador_find_reusable_file(
                    upload_backend,
                    ingest_session_id=int(ingest_session.id),
                    file_name=calibration_zip_path.name,
                )
                if reusable_status is not None:
                    remote_sha256 = (
                        str(getattr(reusable_status, "actual_sha256", "") or "").strip()
                        or str(getattr(reusable_status, "expected_sha256", "") or "").strip()
                        or sha256_file(calibration_zip_path)
                    )
                    cls._record_matador_uploaded_file(
                        manifest_path=matador_manifest_path,
                        session_metadata=session_metadata,
                        session_id=int(ingest_session.id),
                        file_path=calibration_zip_path,
                        file_id=int(getattr(reusable_status, "id")),
                        file_type=str(
                            getattr(reusable_status, "file_type", "") or "ZIP_PAYLOAD"
                        ),
                        ingest_kind="CALIBRATION",
                        sha256=remote_sha256,
                        size_bytes=int(calibration_zip_path.stat().st_size),
                        upload_status=str(getattr(reusable_status, "upload_status", "")),
                        processing_status=str(
                            getattr(reusable_status, "processing_status", "") or ""
                        ),
                        calibration_key=calibration_manifest_key,
                    )
                    if batch_calibration_uploaded is not None:
                        batch_calibration_uploaded.add(calibration_manifest_key)
                    cls._notify_progress(
                        progress_callback,
                        message=(
                            f"{Path(archived_path).name}: Calibration already present "
                            f"in Matador session {ingest_session.id}; reusing file "
                            f"{int(getattr(reusable_status, 'id'))}."
                        ),
                        current=current,
                        total=total,
                        kind="skip_calibration_remote",
                        container_path=Path(archived_path),
                    )
                    continue
                calibration_zip_paths_to_upload.append(calibration_zip_path)

        for calibration_zip_path in calibration_zip_paths_to_upload:
            calibration_checksum = sha256_file(Path(calibration_zip_path))
            cls._notify_progress(
                progress_callback,
                message=(
                    f"{Path(archived_path).name}: Now registering calibration ZIP "
                    f"{calibration_zip_path.name}..."
                ),
                current=current,
                total=total,
                kind="register_calibration_zip",
                container_path=Path(archived_path),
            )
            calibration_registered = upload_backend.register_file(
                MatadorRegisterFileRequest(
                    ingest_session_id=int(ingest_session.id),
                    file_name=Path(calibration_zip_path).name,
                    file_type="ZIP_PAYLOAD",
                    ingest_kind="CALIBRATION",
                    detector_scope=str(session_metadata["detector_scope"]),
                    expected_sha256=calibration_checksum,
                    expected_size_bytes=int(Path(calibration_zip_path).stat().st_size),
                )
            )
            cls._notify_progress(
                progress_callback,
                message=(
                    f"{Path(archived_path).name}: Now uploading calibration ZIP "
                    f"{calibration_zip_path.name}..."
                ),
                current=current,
                total=total,
                kind="upload_calibration_zip",
                container_path=Path(archived_path),
            )
            upload_backend.upload_file_bytes(
                calibration_registered.presigned_url,
                Path(calibration_zip_path),
            )
            calibration_status = cls._poll_until_hash_verified(
                upload_backend,
                file_id=int(calibration_registered.id),
                attempts=int(
                    (config or {}).get(
                        "matador_poll_attempts",
                        cls.DEFAULT_MATADOR_POLL_ATTEMPTS,
                    )
                ),
                delay_sec=float(
                    (config or {}).get(
                        "matador_poll_delay_sec",
                        cls.DEFAULT_MATADOR_POLL_DELAY_SEC,
                    )
                ),
                progress_callback=progress_callback,
                status_label=f"{Path(archived_path).name}: CALIBRATION",
                current=current,
                total=total,
                container_path=Path(archived_path),
            )
            if (
                calibration_status is None
                or str(calibration_status.upload_status or "").upper() != "HASH_VERIFIED"
            ):
                calibration_state = (
                    ""
                    if calibration_status is None
                    else str(calibration_status.upload_status or "")
                )
                return UploadStubResult(
                    success=False,
                    upload_session_id=str(ingest_session.id),
                    message=(
                        "Matador upload incomplete: "
                        f"calibration={calibration_state or 'unknown'}"
                    ),
                    bytes_uploaded=int(Path(archived_path).stat().st_size),
                    local_checksum_sha256=sha256_file(Path(archived_path)),
                    response_checksum_sha256="",
                    remote_container_id=f"matador://ingest-session/{ingest_session.id}",
                    zip_checksum_sha256=sha256_file(Path(old_format_zip_path)),
                    zip_size_bytes=int(Path(old_format_zip_path).stat().st_size),
                    zip_path=str(old_format_zip_path),
                )
            cls._record_matador_uploaded_file(
                manifest_path=matador_manifest_path,
                session_metadata=session_metadata,
                session_id=int(ingest_session.id),
                file_path=Path(calibration_zip_path),
                file_id=int(calibration_registered.id),
                file_type="ZIP_PAYLOAD",
                ingest_kind="CALIBRATION",
                sha256=calibration_checksum,
                size_bytes=int(Path(calibration_zip_path).stat().st_size),
                upload_status=str(calibration_status.upload_status),
                processing_status=str(calibration_status.processing_status),
                calibration_key=calibration_manifest_key,
            )

        if (
            upload_calibration
            and calibration_zip_paths
            and calibration_manifest_key
            and batch_calibration_uploaded is not None
        ):
            batch_calibration_uploaded.add(calibration_manifest_key)

        return None

    @classmethod
    def _matador_upload_measurement_payload(
        cls,
        *,
        archived_path: Path,
        old_format_zip_path: Path,
        upload_backend: Any,
        ingest_session: Any,
        session_metadata: Dict[str, Any],
        defer_measurement_verification: bool,
        matador_manifest_path: Optional[Path],
        config: Optional[Dict[str, Any]],
        progress_callback: Optional[Any],
        current: Optional[int],
        total: Optional[int],
    ) -> UploadStubResult:
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
        if defer_measurement_verification:
            try:
                zip_status = upload_backend.get_file_status(int(zip_registered.id))
            except Exception:
                zip_status = None
        else:
            zip_status = cls._poll_until_hash_verified(
                upload_backend,
                file_id=int(zip_registered.id),
                attempts=int(
                    (config or {}).get(
                        "matador_poll_attempts",
                        cls.DEFAULT_MATADOR_POLL_ATTEMPTS,
                    )
                ),
                delay_sec=float(
                    (config or {}).get(
                        "matador_poll_delay_sec",
                        cls.DEFAULT_MATADOR_POLL_DELAY_SEC,
                    )
                ),
                progress_callback=progress_callback,
                status_label=f"{Path(archived_path).name}: ZIP",
                current=current,
                total=total,
                container_path=Path(archived_path),
            )
            if (
                zip_status is None
                or str(zip_status.upload_status or "").upper() != "HASH_VERIFIED"
            ):
                zip_state = "" if zip_status is None else str(zip_status.upload_status or "")
                return UploadStubResult(
                    success=False,
                    upload_session_id=str(ingest_session.id),
                    message=f"Matador upload incomplete: zip={zip_state or 'unknown'}",
                    bytes_uploaded=int(Path(archived_path).stat().st_size),
                    local_checksum_sha256=sha256_file(Path(archived_path)),
                    response_checksum_sha256="",
                    remote_container_id=f"matador://ingest-session/{ingest_session.id}",
                    zip_file_id=str(zip_registered.id),
                    zip_upload_status=""
                    if zip_status is None
                    else str(zip_status.upload_status),
                    zip_processing_status=""
                    if zip_status is None
                    else str(zip_status.processing_status),
                    zip_checksum_sha256=zip_checksum,
                    zip_size_bytes=int(Path(old_format_zip_path).stat().st_size),
                    zip_path=str(old_format_zip_path),
                )
            cls._record_matador_uploaded_file(
                manifest_path=matador_manifest_path,
                session_metadata=session_metadata,
                session_id=int(ingest_session.id),
                file_path=Path(old_format_zip_path),
                file_id=int(zip_registered.id),
                file_type="ZIP_PAYLOAD",
                ingest_kind="MEASUREMENT",
                sha256=zip_checksum,
                size_bytes=int(Path(old_format_zip_path).stat().st_size),
                upload_status=str(zip_status.upload_status),
                processing_status=str(zip_status.processing_status),
            )

        h5_checksum = sha256_file(Path(archived_path))
        h5_register_name = f"{Path(old_format_zip_path).stem}.h5"
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
                file_name=h5_register_name,
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
        if defer_measurement_verification:
            try:
                h5_status = upload_backend.get_file_status(int(h5_registered.id))
            except Exception:
                h5_status = None
        else:
            h5_status = cls._poll_until_hash_verified(
                upload_backend,
                file_id=int(h5_registered.id),
                attempts=int(
                    (config or {}).get(
                        "matador_poll_attempts",
                        cls.DEFAULT_MATADOR_POLL_ATTEMPTS,
                    )
                ),
                delay_sec=float(
                    (config or {}).get(
                        "matador_poll_delay_sec",
                        cls.DEFAULT_MATADOR_POLL_DELAY_SEC,
                    )
                ),
                progress_callback=progress_callback,
                status_label=f"{Path(archived_path).name}: H5",
                current=current,
                total=total,
                container_path=Path(archived_path),
            )
        if h5_status is not None and str(h5_status.upload_status or "").upper() == "HASH_VERIFIED":
            cls._record_matador_uploaded_file(
                manifest_path=matador_manifest_path,
                session_metadata=session_metadata,
                session_id=int(ingest_session.id),
                file_path=Path(archived_path),
                file_id=int(h5_registered.id),
                file_type="HDF5_CONTAINER",
                ingest_kind="MEASUREMENT",
                sha256=h5_checksum,
                size_bytes=int(Path(archived_path).stat().st_size),
                upload_status=str(h5_status.upload_status),
                processing_status=str(h5_status.processing_status),
                paired_file_id=int(zip_registered.id),
            )

        zip_ok = (
            zip_status is not None
            and str(zip_status.upload_status or "").upper() == "HASH_VERIFIED"
        )
        h5_ok = (
            h5_status is not None
            and str(h5_status.upload_status or "").upper() == "HASH_VERIFIED"
        )
        verification_pending = bool(
            defer_measurement_verification
            and not (zip_ok and h5_ok)
            and (zip_status is None or str(zip_status.upload_status or "").upper() != "FAILED")
            and (h5_status is None or str(h5_status.upload_status or "").upper() != "FAILED")
        )
        success = bool((zip_ok and h5_ok) or verification_pending)
        if zip_ok and h5_ok:
            message = (
                f"Matador upload complete: session={ingest_session.id} "
                f"zip={zip_registered.id} h5={h5_registered.id}"
            )
        elif verification_pending:
            zip_state = "" if zip_status is None else str(zip_status.upload_status or "")
            h5_state = "" if h5_status is None else str(h5_status.upload_status or "")
            message = (
                "Matador binary upload accepted; checksum verification pending: "
                f"session={ingest_session.id} zip={zip_registered.id}"
                f"({zip_state or 'unknown'}) h5={h5_registered.id}"
                f"({h5_state or 'unknown'})"
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
                f"{'PENDING' if verification_pending else 'SUCCESS' if success else 'FAILED'} | {message}"
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
            verification_pending=verification_pending,
            resolved_matador_specimen_id=(
                int(session_metadata["specimen_id"])
                if session_metadata.get("specimen_id") is not None
                else None
            ),
            specimen_resolution_message=str(
                session_metadata.get("specimen_resolution_message") or ""
            ),
        )
