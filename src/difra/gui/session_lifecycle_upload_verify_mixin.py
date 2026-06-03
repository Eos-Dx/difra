from __future__ import annotations

from collections import Counter
from fnmatch import fnmatch
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Set
import zipfile

import h5py

from difra.gui.matador_upload_api import (
    MatadorFindOrCreateSessionRequest,
    MatadorRegisterFileRequest,
    MatadorUploadContainerRequest,
    build_matador_upload_api,
    sha256_file,
)
from difra.gui.main_window_ext.technical.helpers import _get_difra_base_folder
from difra.gui.matador_zip_bundle_exporter import MatadorZipBundleExporter
from difra.gui.session_lifecycle_common import (
    SendArchiveResult,
    UploadStubResult,
    _normalize_iso_date,
)
from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.gui.session_old_format_exporter import SessionOldFormatExporter

logger = logging.getLogger(__name__)
SessionLifecycleActions = None


def _actions_module():
    from difra.gui import session_lifecycle_actions as actions

    return actions


def _build_matador_upload_api(*args, **kwargs):
    return _actions_module().build_matador_upload_api(*args, **kwargs)


def _session_old_format_exporter():
    return _actions_module().SessionOldFormatExporter


def _session_lifecycle_service():
    return _actions_module().SessionLifecycleService


class SessionLifecycleUploadVerifyMixin:
    @classmethod
    def verify_pending_matador_uploads(
        cls,
        container_paths: Iterable[Path],
        *,
        container_manager: Any = None,
        config: Optional[Dict[str, Any]] = None,
        operator_id: Optional[str] = None,
        progress_callback: Optional[Any] = None,
    ) -> SendArchiveResult:
        """Refresh Matador status for containers awaiting asynchronous verification."""
        result = SendArchiveResult()
        upload_api = _build_matador_upload_api(config=config)
        resolved_operator = cls._resolve_uploader_id(
            explicit_uploader_id=operator_id,
            lock_user=None,
        )

        queued_paths = [Path(path) for path in container_paths]
        total = len(queued_paths)
        for index, container_path in enumerate(queued_paths, start=1):
            path = Path(container_path)
            if not path.exists():
                result.upload_failed += 1
                result.failed.append(f"{path.name}: container not found")
                continue

            try:
                with h5py.File(path, "r") as h5f:
                    upload_status = str(h5f.attrs.get("upload_status", "") or "")
                    zip_file_id = str(h5f.attrs.get("matador_zip_file_id", "") or "")
                    h5_file_id = str(h5f.attrs.get("matador_h5_file_id", "") or "")
                    upload_session_id = str(h5f.attrs.get("upload_session_id", "") or "")
                    remote_container_id = str(
                        h5f.attrs.get("upload_remote_container_id", "") or ""
                    )
                    local_checksum = str(
                        h5f.attrs.get("upload_local_checksum_sha256", "") or ""
                    )
                    bytes_uploaded = int(h5f.attrs.get("upload_bytes", 0) or 0)
                    zip_checksum = str(
                        h5f.attrs.get("matador_zip_checksum_sha256", "") or ""
                    )
                    zip_size = int(h5f.attrs.get("matador_zip_size_bytes", 0) or 0)
                    zip_path = str(h5f.attrs.get("matador_zip_path", "") or "")
            except Exception as exc:
                result.upload_failed += 1
                result.failed.append(f"{path.name}: failed to read container ({exc})")
                continue

            if upload_status != cls.UPLOAD_STATUS_PENDING_VERIFICATION:
                continue

            cls._notify_progress(
                progress_callback,
                message=f"[{index}/{total}] {path.name}: Checking Matador verification...",
                current=index,
                total=total,
                kind="verification_started",
                container_path=path,
            )

            if not zip_file_id or not h5_file_id:
                upload_result = UploadStubResult(
                    success=False,
                    upload_session_id=upload_session_id,
                    message="Matador verification failed: missing saved file IDs",
                    bytes_uploaded=bytes_uploaded,
                    local_checksum_sha256=local_checksum,
                    response_checksum_sha256="",
                    remote_container_id=remote_container_id,
                    zip_file_id=zip_file_id,
                    h5_file_id=h5_file_id,
                    zip_checksum_sha256=zip_checksum,
                    zip_size_bytes=zip_size,
                    zip_path=zip_path,
                )
            else:
                zip_status = upload_api.get_file_status(int(zip_file_id))
                h5_status = upload_api.get_file_status(int(h5_file_id))
                zip_state = str(zip_status.upload_status or "")
                h5_state = str(h5_status.upload_status or "")
                zip_ok = zip_state.upper() == "HASH_VERIFIED"
                h5_ok = h5_state.upper() == "HASH_VERIFIED"
                failed = zip_state.upper() == "FAILED" or h5_state.upper() == "FAILED"
                if zip_ok and h5_ok:
                    upload_result = UploadStubResult(
                        success=True,
                        upload_session_id=upload_session_id,
                        message=(
                            "Matador upload verified: "
                            f"zip={zip_file_id} h5={h5_file_id}"
                        ),
                        bytes_uploaded=bytes_uploaded,
                        local_checksum_sha256=local_checksum,
                        response_checksum_sha256=local_checksum,
                        remote_container_id=remote_container_id,
                        zip_file_id=zip_file_id,
                        zip_upload_status=zip_state,
                        zip_processing_status=str(zip_status.processing_status or ""),
                        zip_checksum_sha256=(
                            str(zip_status.actual_sha256 or "")
                            or str(zip_status.expected_sha256 or "")
                            or zip_checksum
                        ),
                        zip_size_bytes=zip_size,
                        zip_path=zip_path,
                        h5_file_id=h5_file_id,
                        h5_upload_status=h5_state,
                        h5_processing_status=str(h5_status.processing_status or ""),
                    )
                elif failed:
                    upload_result = UploadStubResult(
                        success=False,
                        upload_session_id=upload_session_id,
                        message=(
                            "Matador verification failed: "
                            f"zip={zip_state or 'unknown'} h5={h5_state or 'unknown'}"
                        ),
                        bytes_uploaded=bytes_uploaded,
                        local_checksum_sha256=local_checksum,
                        response_checksum_sha256="",
                        remote_container_id=remote_container_id,
                        zip_file_id=zip_file_id,
                        zip_upload_status=zip_state,
                        zip_processing_status=str(zip_status.processing_status or ""),
                        zip_checksum_sha256=zip_checksum,
                        zip_size_bytes=zip_size,
                        zip_path=zip_path,
                        h5_file_id=h5_file_id,
                        h5_upload_status=h5_state,
                        h5_processing_status=str(h5_status.processing_status or ""),
                    )
                else:
                    result.upload_pending += 1
                    cls._write_container_attrs(
                        path,
                        {
                            "matador_zip_upload_status": zip_state,
                            "matador_zip_processing_status": str(
                                zip_status.processing_status or ""
                            ),
                            "matador_h5_upload_status": h5_state,
                            "matador_h5_processing_status": str(
                                h5_status.processing_status or ""
                            ),
                            "matador_verification_checked_at": time.strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                        },
                    )
                    cls._notify_progress(
                        progress_callback,
                        message=(
                            f"[{index}/{total}] {path.name}: PENDING - "
                            f"zip={zip_state or 'unknown'} h5={h5_state or 'unknown'}"
                        ),
                        current=index,
                        total=total,
                        kind="verification_pending",
                        container_path=path,
                    )
                    continue

            wrote_result = cls.write_upload_result_metadata(path, upload_result)
            wrote_log = cls.append_upload_attempt_log(
                path,
                operator_id=resolved_operator,
                upload_result=upload_result,
            )
            ok = bool(upload_result.success and wrote_result and wrote_log)
            mark_transferred = getattr(container_manager, "mark_container_transferred", None)
            if callable(mark_transferred):
                mark_transferred(path, sent=ok)
            if ok:
                result.upload_success += 1
                cls._notify_progress(
                    progress_callback,
                    message=f"[{index}/{total}] {path.name}: SUCCESS - Matador verification complete.",
                    current=index,
                    total=total,
                    kind="verification_done",
                    container_path=path,
                )
            else:
                result.upload_failed += 1
                result.failed.append(f"{path.name}: {upload_result.message}")
                cls._notify_progress(
                    progress_callback,
                    message=f"[{index}/{total}] {path.name}: FAILED - {upload_result.message}",
                    current=index,
                    total=total,
                    kind="verification_failed",
                    container_path=path,
                )

        return result
