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


class SessionLifecycleArchiveMixin:
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
        changed = _session_lifecycle_service().lock_container_if_needed(
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
    @staticmethod
    def _current_transfer_status(container_path: Path, *, container_manager: Any) -> str:
        try:
            with h5py.File(container_path, "r") as h5f:
                root_status = str(
                    h5f.attrs.get("transfer_status", "") or ""
                ).strip().lower()
            if root_status in {
                SessionLifecycleActions.TRANSFER_STATUS_NOT_COMPLETE,
                SessionLifecycleActions.TRANSFER_STATUS_REQ_RESEND,
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
