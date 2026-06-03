from __future__ import annotations

import logging
from pathlib import Path
import shutil
import time
from typing import Any, Dict, Iterable, List, Optional, Set

from difra.gui.matador_upload_api import (
    sha256_file,
)
from difra.gui.session_lifecycle_common import (
    SendArchiveResult,
    UploadStubResult,
)
from difra.gui.session_lifecycle_archive_support_mixin import (
    SessionLifecycleArchiveSupportMixin,
)

logger = logging.getLogger(__name__)
SessionLifecycleActions = None


def _actions_module():
    from difra.gui import session_lifecycle_actions as actions

    return actions


def _build_matador_upload_api(*args, **kwargs):
    return _actions_module().build_matador_upload_api(*args, **kwargs)


def _session_lifecycle_service():
    return _actions_module().SessionLifecycleService


class SessionLifecycleArchiveMixin(SessionLifecycleArchiveSupportMixin):
    @classmethod
    def archive_session_containers(
        cls,
        container_paths: Iterable[Path],
        *,
        container_manager: Any,
        archive_folder: Path,
        config: Optional[Dict[str, Any]] = None,
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
                archived_path = _session_lifecycle_service().archive_session_container(
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

                _session_lifecycle_service().copy_archive_item_to_mirror(
                    Path(archived_path).parent,
                    config=config,
                    archive_kind="measurements",
                )

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
        config: Optional[Dict[str, Any]] = None,
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
            config=config,
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
        specimen_overrides: Optional[Dict[str, int]] = None,
    ) -> SendArchiveResult:
        """Lock (if needed) and archive selected session containers."""
        result = SendArchiveResult()
        if export_old_format is None:
            export_old_format = bool(
                (config or {}).get("enable_old_format_export", True)
            )
        persist_old_format = bool((config or {}).get("persist_old_format_export", False))
        resolved_uploader_id = cls._resolve_uploader_id(
            explicit_uploader_id=uploader_id,
            lock_user=lock_user,
        )
        upload_api = _build_matador_upload_api(config=config)
        use_stub_h5_only = (
            not export_old_format
            and upload_api.__class__.__name__ == "StubMatadorUploadApi"
        )
        active_resolved = (
            Path(active_session_path).resolve()
            if active_session_path is not None
            else None
        )
        overrides = {
            str(Path(path)): int(value)
            for path, value in (specimen_overrides or {}).items()
            if value not in (None, "")
        }

        session_id_by_path = session_ids or {}
        cleanup_folders = set()

        queued_paths = [Path(path) for path in container_paths]
        if len(queued_paths) > 1:
            queued_paths = cls._order_paths_by_matador_group(
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

                override_value = overrides.get(str(candidate))
                if override_value is not None:
                    cls._write_container_attrs(
                        candidate,
                        {"matadorSpecimenId": int(override_value)},
                    )

                calibration_zip_paths: List[Path] = []
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
                        _summary, old_format_group, old_format_zip_path, calibration_zip_paths = (
                            cls._prepare_old_format_payload(
                                candidate,
                                archive_folder=archive_folder,
                                config=config,
                                persist_old_format=persist_old_format,
                            )
                        )
                        if old_format_group is not None:
                            result.old_format_paths.append(old_format_group)
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
                        calibration_zip_paths = []

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
                    archived_path = _session_lifecycle_service().archive_session_container(
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

                upload_exception = None
                try:
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
                            matador_manifest_path=Path(archive_folder) / ".matador_uploaded",
                        )
                except Exception as exc:
                    upload_exception = exc
                    logger.warning(
                        "Matador upload failed after archive: container=%s error=%s",
                        str(archived_path),
                        exc,
                        exc_info=True,
                    )
                    upload_result = UploadStubResult(
                        success=False,
                        upload_session_id="",
                        message=str(exc),
                        bytes_uploaded=0,
                        local_checksum_sha256="",
                        response_checksum_sha256="",
                        remote_container_id="",
                        zip_path=str(old_format_zip_path or ""),
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
                elif effective_upload_pending:
                    result.upload_pending += 1
                    cls._notify_progress(
                        progress_callback,
                        message=(
                            f"[{item_index}/{total_containers}] {candidate.name}: "
                            "PENDING - ZIP and H5 uploaded; Matador verification will continue asynchronously."
                        ),
                        current=item_index,
                        total=total_containers,
                        kind="container_pending",
                        container_path=Path(archived_path),
                    )
                else:
                    result.upload_failed += 1
                    if upload_result.success and not metadata_write_ok:
                        result.failed.append(
                            f"{candidate.name}: upload metadata write failed"
                        )
                    elif upload_exception is not None:
                        result.failed.append(f"{candidate.name}: {upload_result.message}")
                    else:
                        result.failed.append(
                            f"{candidate.name}: upload failed ({upload_result.message})"
                        )
                    cls._notify_progress(
                        progress_callback,
                        message=(
                            f"[{item_index}/{total_containers}] {candidate.name}: "
                            f"FAILED - unexpected error ({upload_result.message})"
                            if upload_exception is not None
                            else f"[{item_index}/{total_containers}] {candidate.name}: FAILED - {upload_result.message}"
                        ),
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
                _session_lifecycle_service().copy_archive_item_to_mirror(
                    Path(archived_path).parent,
                    config=config,
                    archive_kind="measurements",
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
