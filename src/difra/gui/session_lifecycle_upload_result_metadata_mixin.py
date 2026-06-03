from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
import time

import h5py

from difra.gui.session_lifecycle_common import UploadStubResult

logger = logging.getLogger(__name__)


class SessionLifecycleUploadResultMetadataMixin:
    """Write Matador upload attempt/result metadata back into session containers."""

    @classmethod
    def append_upload_attempt_log(
        cls,
        container_path: Path,
        *,
        operator_id: str,
        upload_result: UploadStubResult,
    ) -> bool:
        path = Path(container_path)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        status = (
            cls.UPLOAD_STATUS_PENDING_VERIFICATION
            if bool(getattr(upload_result, "verification_pending", False))
            else "success"
            if upload_result.success
            else "failed"
        )
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
        lines = lines[-200:]

        return cls._write_container_attrs(
            path,
            {
                "upload_attempts_log": "\n".join(lines),
                "upload_attempt_count": int(len(lines)),
                "last_upload_error": ""
                if upload_result.success
                else upload_result.message,
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
        *,
        specimen_id: Optional[int] = None,
    ) -> bool:
        verification_pending = bool(
            getattr(upload_result, "verification_pending", False)
        )
        if verification_pending:
            upload_status = cls.UPLOAD_STATUS_PENDING_VERIFICATION
            send_status = cls.UPLOAD_STATUS_PENDING_VERIFICATION
            send_reason = str(upload_result.message)
        else:
            upload_status = "success" if upload_result.success else "failed"
            send_status = "successful" if upload_result.success else "unsuccessful"
            send_reason = "" if upload_result.success else str(upload_result.message)
        resolved_specimen_id = specimen_id
        if resolved_specimen_id is None:
            result_specimen_id = getattr(
                upload_result, "resolved_matador_specimen_id", None
            )
            if result_specimen_id is not None:
                resolved_specimen_id = int(result_specimen_id)
        if resolved_specimen_id is None:
            try:
                metadata = cls._read_matador_session_metadata(Path(container_path))
                if metadata.get("specimen_id") is not None:
                    resolved_specimen_id = int(metadata["specimen_id"])
            except Exception:
                logger.debug(
                    "Failed to derive Matador specimen id for upload metadata write",
                    exc_info=True,
                )

        attrs = {
            "upload_session_id": str(upload_result.upload_session_id),
            "upload_status": upload_status,
            "upload_result_message": str(upload_result.message),
            "matador_send_status": send_status,
            "matador_send_reason": send_reason,
            "matador_send_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "matador_verification_pending": bool(verification_pending),
            "upload_bytes": int(upload_result.bytes_uploaded),
            "upload_local_checksum_sha256": str(upload_result.local_checksum_sha256),
            "upload_response_checksum_sha256": str(
                upload_result.response_checksum_sha256
            ),
            "upload_remote_container_id": str(upload_result.remote_container_id),
            "matador_zip_file_id": str(upload_result.zip_file_id),
            "matador_zip_upload_status": str(upload_result.zip_upload_status),
            "matador_zip_processing_status": str(
                upload_result.zip_processing_status
            ),
            "matador_zip_checksum_sha256": str(upload_result.zip_checksum_sha256),
            "matador_zip_size_bytes": int(upload_result.zip_size_bytes),
            "matador_zip_path": str(upload_result.zip_path),
            "matador_h5_file_id": str(upload_result.h5_file_id),
            "matador_h5_upload_status": str(upload_result.h5_upload_status),
            "matador_h5_processing_status": str(
                upload_result.h5_processing_status
            ),
            "upload_finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if resolved_specimen_id is not None:
            attrs["matadorSpecimenId"] = int(resolved_specimen_id)
        resolution_message = str(
            getattr(upload_result, "specimen_resolution_message", "") or ""
        ).strip()
        if resolution_message:
            attrs["matador_specimen_resolution"] = resolution_message
        return cls._write_container_attrs(container_path, attrs)
