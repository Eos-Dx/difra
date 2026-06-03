from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from difra.gui.matador_upload_api import sha256_file
from difra.gui.session_lifecycle_common import (
    SendArchiveResult,
    UploadStubResult,
)

logger = logging.getLogger(__name__)


class SessionReuploadService:
    def __init__(self, *, actions_cls: Any, build_upload_api: Any):
        self.actions_cls = actions_cls
        self.build_upload_api = build_upload_api

    def reupload_archived_session_containers(
        self,
        container_paths: Iterable[Path],
        *,
        container_manager: Any,
        uploader_id: Optional[str] = None,
        lock_user: Optional[str] = None,
        simulate_upload_failure: bool = False,
        config: Optional[Dict[str, Any]] = None,
        export_old_format: Optional[bool] = None,
        progress_callback: Optional[Any] = None,
        specimen_overrides: Optional[Dict[str, int]] = None,
    ) -> SendArchiveResult:
        """Upload archived session containers again without moving them."""
        actions = self.actions_cls
        result = SendArchiveResult()
        if export_old_format is None:
            export_old_format = bool(
                (config or {}).get("enable_old_format_export", True)
            )
        persist_old_format = bool((config or {}).get("persist_old_format_export", False))

        resolved_uploader_id = actions._resolve_uploader_id(
            explicit_uploader_id=uploader_id,
            lock_user=lock_user,
        )
        upload_api = self.build_upload_api(config=config)
        use_stub_h5_only = (
            not export_old_format
            and upload_api.__class__.__name__ == "StubMatadorUploadApi"
        )
        overrides = {
            str(Path(path)): int(value)
            for path, value in (specimen_overrides or {}).items()
            if value not in (None, "")
        }
        queued_paths = [Path(path) for path in container_paths]
        if len(queued_paths) > 1:
            queued_paths = actions._order_paths_by_matador_group(
                queued_paths,
                config=config,
                uploader_id=resolved_uploader_id,
            )
        total_containers = len(queued_paths)
        batch_session_cache: Optional[Dict[str, Any]] = (
            {} if total_containers > 1 else None
        )
        batch_calibration_uploaded: Optional[Set[str]] = (
            set() if total_containers > 1 else None
        )

        for item_index, container_path in enumerate(queued_paths, start=1):
            candidate = Path(container_path)
            old_format_zip_path = None
            try:
                if not candidate.exists():
                    result.upload_failed += 1
                    result.failed.append(f"{candidate.name}: container not found")
                    continue

                prior_transfer_status = actions._current_transfer_status(
                    candidate,
                    container_manager=container_manager,
                )
                if prior_transfer_status == actions.TRANSFER_STATUS_NOT_COMPLETE:
                    message = (
                        "Container is marked NOT_COMPLETE and cannot be sent to Matador."
                    )
                    result.upload_failed += 1
                    result.failed.append(f"{candidate.name}: {message}")
                    actions._notify_progress(
                        progress_callback,
                        message=f"[{item_index}/{total_containers}] {candidate.name}: FAILED - {message}",
                        current=item_index,
                        total=total_containers,
                        kind="container_failed",
                        container_path=candidate,
                    )
                    continue

                override_value = overrides.get(str(candidate))
                if override_value is not None:
                    actions._write_container_attrs(
                        candidate,
                        {"matadorSpecimenId": int(override_value)},
                    )

                actions._notify_progress(
                    progress_callback,
                    message=f"[{item_index}/{total_containers}] {candidate.name}: Starting Matador resend...",
                    current=item_index,
                    total=total_containers,
                    kind="container_started",
                    container_path=candidate,
                )

                calibration_zip_paths: List[Path] = []
                if not use_stub_h5_only:
                    try:
                        actions._notify_progress(
                            progress_callback,
                            message=f"[{item_index}/{total_containers}] {candidate.name}: Rebuilding old-format folder and ZIP...",
                            current=item_index,
                            total=total_containers,
                            kind="prepare_old_format",
                            container_path=candidate,
                        )
                        _summary, old_format_group, old_format_zip_path, calibration_zip_paths = (
                            actions._prepare_old_format_payload(
                                candidate,
                                archive_folder=candidate.parent,
                                config=config,
                                persist_old_format=persist_old_format,
                            )
                        )
                        if old_format_group is not None:
                            result.old_format_paths.append(old_format_group)
                    except Exception as exc:
                        result.old_format_failed.append(f"{candidate.name}: {exc}")
                        old_format_zip_path = None
                        calibration_zip_paths = []

                if use_stub_h5_only:
                    upload_result = actions.execute_upload_stub(
                        candidate,
                        uploader_id=resolved_uploader_id,
                        upload_session_id=actions.create_upload_session_id(
                            uploader_id=resolved_uploader_id
                        ),
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
                        bytes_uploaded=int(candidate.stat().st_size),
                        local_checksum_sha256=sha256_file(candidate),
                        response_checksum_sha256="",
                        remote_container_id="",
                    )
                else:
                    upload_result = actions._execute_matador_upload(
                        candidate,
                        old_format_zip_path=Path(old_format_zip_path),
                        calibration_zip_paths=calibration_zip_paths,
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
                        batch_session_cache=batch_session_cache,
                        batch_calibration_uploaded=batch_calibration_uploaded,
                        matador_manifest_path=Path(candidate).parent / ".matador_uploaded",
                    )
                if upload_result.upload_session_id and not result.upload_session_id:
                    result.upload_session_id = str(upload_result.upload_session_id)

                wrote_upload_meta = actions.write_upload_metadata(
                    candidate,
                    uploader_id=resolved_uploader_id,
                    lock_user=lock_user,
                )
                wrote_upload_result = actions.write_upload_result_metadata(
                    candidate,
                    upload_result=upload_result,
                )
                wrote_upload_log = actions.append_upload_attempt_log(
                    candidate,
                    operator_id=resolved_uploader_id,
                    upload_result=upload_result,
                )
                metadata_write_ok = (
                    bool(wrote_upload_meta)
                    and bool(wrote_upload_result)
                    and bool(wrote_upload_log)
                )
                effective_upload_pending = bool(
                    getattr(upload_result, "verification_pending", False)
                    and metadata_write_ok
                )
                effective_upload_success = bool(
                    upload_result.success
                    and metadata_write_ok
                    and not effective_upload_pending
                )

                mark_transferred = getattr(container_manager, "mark_container_transferred", None)
                if callable(mark_transferred):
                    if effective_upload_success or prior_transfer_status == "sent":
                        mark_transferred(candidate, sent=True)
                    elif prior_transfer_status == actions.TRANSFER_STATUS_REQ_RESEND:
                        pass
                    else:
                        mark_transferred(candidate, sent=False)

                if effective_upload_success:
                    result.upload_success += 1
                    actions._notify_progress(
                        progress_callback,
                        message=f"[{item_index}/{total_containers}] {candidate.name}: SUCCESS - archived container re-uploaded.",
                        current=item_index,
                        total=total_containers,
                        kind="container_done",
                        container_path=candidate,
                    )
                elif effective_upload_pending:
                    result.upload_pending += 1
                    actions._notify_progress(
                        progress_callback,
                        message=(
                            f"[{item_index}/{total_containers}] {candidate.name}: "
                            "PENDING - archived container uploaded; Matador verification will continue asynchronously."
                        ),
                        current=item_index,
                        total=total_containers,
                        kind="container_pending",
                        container_path=candidate,
                    )
                else:
                    result.upload_failed += 1
                    failure_message = (
                        "upload metadata write failed"
                        if upload_result.success and not metadata_write_ok
                        else str(upload_result.message)
                    )
                    result.failed.append(f"{candidate.name}: {failure_message}")
                    actions._notify_progress(
                        progress_callback,
                        message=f"[{item_index}/{total_containers}] {candidate.name}: FAILED - {failure_message}",
                        current=item_index,
                        total=total_containers,
                        kind="container_failed",
                        container_path=candidate,
                    )

                result.archived_paths.append(candidate)
            except Exception as exc:
                result.upload_failed += 1
                result.failed.append(f"{candidate.name}: {exc}")
                actions._notify_progress(
                    progress_callback,
                    message=f"[{item_index}/{total_containers}] {candidate.name}: FAILED - unexpected error ({exc})",
                    current=item_index,
                    total=total_containers,
                    kind="container_failed",
                    container_path=candidate,
                )

        return result
