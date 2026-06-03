"""Procedural lock/archive helpers extracted from H5ManagementLockingMixin."""

import logging
import time
from pathlib import Path

try:
    from PyQt5.QtWidgets import QInputDialog, QMessageBox
except Exception:
    class QInputDialog:  # pragma: no cover - fallback for stubbed test environments
        @staticmethod
        def getText(*_args, **_kwargs):
            return "", True

    class QMessageBox:  # pragma: no cover - fallback for stubbed test environments
        Yes = 1
        No = 0
        AcceptRole = 1
        ActionRole = 2

        def __init__(self, *_args, **_kwargs):
            self._clicked = None

        @staticmethod
        def question(*_args, **_kwargs):
            return QMessageBox.No

        @staticmethod
        def information(*_args, **_kwargs):
            return None

        @staticmethod
        def warning(*_args, **_kwargs):
            return None

        @staticmethod
        def critical(*_args, **_kwargs):
            return None

        def setWindowTitle(self, *_args, **_kwargs):
            return None

        def setIcon(self, *_args, **_kwargs):
            return None

        def setText(self, *_args, **_kwargs):
            return None

        def addButton(self, *_args, **_kwargs):
            return None

        def setDefaultButton(self, *_args, **_kwargs):
            return None

        def clickedButton(self):
            return self._clicked

        def exec_(self):
            return QMessageBox.No

from difra.gui.container_api import get_container_manager
from difra.gui.main_window_ext.technical.h5_management_archive_ops import (
    _find_existing_technical_companion_archive_folder,
    _repack_hdf5_in_place,
    _rewrite_technical_source_paths,
    _safe_archive_token,
    _unique_archive_destination,
    archive_existing_containers,
    update_aux_table_paths_after_archive,
)
from difra.gui.main_window_ext.technical.h5_management_lock_guards import (
    _ensure_distances_configured,
)
from difra.gui.main_window_ext.technical.h5_management_poni_update_actions import (
    update_active_technical_container_poni,
)

logger = logging.getLogger(__name__)


def create_new_technical_container(owner):
    """Compatibility wrapper for the legacy create action."""
    if hasattr(owner, "_create_new_active_technical_container"):
        created = owner._create_new_active_technical_container(clear_table=True)
        if created is not None:
            QMessageBox.information(
                owner,
                "Technical Container",
                f"Created new technical container:\n{created}",
            )
        return
    QMessageBox.information(
        owner,
        "Removed Workflow",
        "Legacy 'New Container' flow is removed.",
    )


def archive_active_technical_container(owner):
    """Archive the active technical container after irreversible confirmation."""
    active_getter = getattr(owner, "_active_technical_container_path_obj", None)
    if callable(active_getter):
        container_path = active_getter()
    else:
        raw = str(getattr(owner, "_active_technical_container_path", "") or "").strip()
        container_path = Path(raw) if raw else None

    if container_path is None:
        QMessageBox.warning(
            owner,
            "No Active Container",
            "No active technical container loaded or created.",
        )
        return False

    container_path = Path(container_path)
    if not container_path.exists():
        QMessageBox.warning(
            owner,
            "Container Missing",
            f"Technical container not found:\n{container_path}",
        )
        return False

    container_manager = get_container_manager(owner.config if hasattr(owner, "config") else None)

    reply = QMessageBox.question(
        owner,
        "Archive Technical Container",
        "This action cannot be reverted.\n\n"
        f"Container: {container_path.name}\n\n"
        "The container will be locked (if needed) and moved to archive together "
        "with related technical files.\n\n"
        "Continue?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if reply != QMessageBox.Yes:
        owner._log_technical_event(
            f"Archive cancelled by user for {container_path.name}"
        )
        return False

    is_locked = bool(container_manager.is_container_locked(container_path))
    if not is_locked:
        lock_reply = QMessageBox.question(
            owner,
            "Lock Before Archive",
            "This container is not locked.\n\n"
            f"Container: {container_path.name}\n\n"
            "It must be locked before archive. Lock now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if lock_reply != QMessageBox.Yes:
            owner._log_technical_event(
                f"Archive cancelled: user declined lock for {container_path.name}"
            )
            return False

        lock_fn = getattr(owner, "lock_active_technical_container", None)
        if callable(lock_fn):
            locked_now = bool(lock_fn())
        else:
            locked_now = False

        if not locked_now:
            QMessageBox.warning(
                owner,
                "Archive Blocked",
                "Container could not be locked. Archive was cancelled.",
            )
            return False

    try:
        archived_path = owner._lock_and_archive_technical_container(container_path)
    except Exception as exc:
        QMessageBox.critical(
            owner,
            "Archive Failed",
            f"Failed to archive technical container:\n{exc}",
        )
        owner._log_technical_event(
            f"Failed to archive technical container {container_path.name}: {exc}"
        )
        return False

    owner._log_technical_event(
        f"Archived active technical container: {container_path.name} -> {Path(archived_path).name}"
    )
    set_state = getattr(owner, "_set_container_state", None)
    if callable(set_state):
        set_state(
            Path(archived_path),
            state=getattr(owner, "STATE_ARCHIVED", "archived"),
            reason="manual_archive_completed",
        )
    QMessageBox.information(
        owner,
        "Container Archived",
        "Technical container archived successfully.\n\n"
        f"Archived file: {Path(archived_path).name}\n"
        f"Archive folder: {Path(archived_path).parent}",
    )
    return True


def lock_active_technical_container(owner):
    """Lock currently active technical container."""
    import h5py

    if hasattr(owner, "_sync_active_technical_container_from_table"):
        try:
            synced = owner._sync_active_technical_container_from_table(show_errors=False)
            if not synced and hasattr(owner, "_log_technical_event"):
                owner._log_technical_event(
                    "Pre-lock technical sync skipped/failed silently; proceeding to lock validation"
                )
        except Exception as exc:
            logger.warning(
                "Pre-lock technical sync raised an exception; continuing with lock validation: %s",
                exc,
                exc_info=True,
            )

    active_path = str(getattr(owner, "_active_technical_container_path", "") or "").strip()
    if not active_path:
        QMessageBox.warning(
            owner,
            "No Active Container",
            "No active technical container loaded or created.",
        )
        return False

    container_path = Path(active_path)
    if not container_path.exists():
        QMessageBox.warning(
            owner,
            "Container Missing",
            f"Technical container not found:\n{container_path}",
        )
        return False

    container_manager = get_container_manager(owner.config if hasattr(owner, "config") else None)
    if container_manager.is_container_locked(container_path):
        set_state = getattr(owner, "_set_container_state", None)
        if callable(set_state):
            set_state(
                Path(container_path),
                state=getattr(owner, "STATE_LOCKED", "locked"),
                reason="already_locked",
            )
        QMessageBox.information(
            owner,
            "Already Locked",
            f"Container is already locked:\n{container_path.name}",
        )
        return True

    if not _ensure_distances_configured(owner, action_name="Lock Container"):
        return False

    if bool(getattr(owner, "config", {}).get("DEV", False)):
        has_poni_fn = getattr(owner, "_container_has_poni_datasets", None)
        collect_aliases = getattr(owner, "_collect_lock_detector_aliases", None)
        auto_provision = getattr(owner, "_auto_provision_demo_poni_files", None)
        sync_fn = getattr(owner, "_sync_active_technical_container_from_table", None)

        missing_embedded_poni = False
        if callable(has_poni_fn):
            try:
                missing_embedded_poni = not bool(has_poni_fn(container_path))
            except Exception:
                missing_embedded_poni = False

        if missing_embedded_poni and callable(collect_aliases):
            try:
                aliases = list(collect_aliases(container_path) or [])
            except Exception:
                aliases = []

            aliases = sorted({str(alias).strip() for alias in aliases if str(alias).strip()})
            if aliases:
                provisioned = False
                if callable(auto_provision):
                    try:
                        provisioned = bool(auto_provision(aliases))
                    except Exception:
                        provisioned = False

                loaded_poni = getattr(owner, "ponis", {})
                has_loaded_poni = isinstance(loaded_poni, dict) and all(
                    str(loaded_poni.get(alias) or "").strip()
                    for alias in aliases
                )

                if (provisioned or has_loaded_poni) and callable(sync_fn):
                    synced = bool(sync_fn(show_errors=False))
                    if not synced:
                        QMessageBox.warning(
                            owner,
                            "PONI Sync Failed",
                            "DEMO PONI files were prepared automatically, but container sync failed.\n\n"
                            "Fix technical rows and try locking again.",
                        )
                        if hasattr(owner, "_log_technical_event"):
                            owner._log_technical_event(
                                f"Lock blocked: failed to auto-sync demo PONI for {container_path.name}"
                        )
                        return False
                    if provisioned:
                        QMessageBox.information(
                            owner,
                            "Demo PONI Added",
                            "DEMO mode detected. Fake PONI files were added automatically.\n\n"
                            "Validation will now continue before lock.",
                        )
                        if hasattr(owner, "_log_technical_event"):
                            owner._log_technical_event(
                                f"Auto-synced DEMO PONI files before lock for {container_path.name}"
                            )

    container_id = container_path.stem
    try:
        with h5py.File(container_path, "r") as h5f:
            raw_id = h5f.attrs.get("container_id")
            if isinstance(raw_id, bytes):
                raw_id = raw_id.decode("utf-8", errors="replace")
            if raw_id:
                container_id = str(raw_id)
    except Exception:
        pass

    if not owner._ensure_poni_before_lock(container_path, container_id):
        sync_state = getattr(owner, "_sync_container_state", None)
        if callable(sync_state):
            sync_state(Path(container_path), reason="lock_blocked_missing_poni")
        return False

    if not owner._validate_container_before_lock(container_path, container_id):
        set_state = getattr(owner, "_set_container_state", None)
        if callable(set_state):
            set_state(
                Path(container_path),
                state=getattr(owner, "STATE_VALIDATION_FAILED", "validation_failed"),
                reason="lock_blocked_validation",
            )
        return False

    confirm_preview = getattr(owner, "_confirm_poni_center_preview_before_lock", None)
    if callable(confirm_preview):
        if not confirm_preview(container_path, container_id):
            sync_state = getattr(owner, "_sync_container_state", None)
            if callable(sync_state):
                sync_state(Path(container_path), reason="lock_blocked_review")
            return False

    lock_result = owner._lock_container(str(container_path), container_id)
    if lock_result is False:
        sync_state = getattr(owner, "_sync_container_state", None)
        if callable(sync_state):
            sync_state(Path(container_path), reason="lock_failed")
        return False
    owner._active_technical_container_locked = bool(
        container_manager.is_container_locked(container_path)
    ) or bool(lock_result is not False)
    set_state = getattr(owner, "_set_container_state", None)
    if callable(set_state) and bool(owner._active_technical_container_locked):
        set_state(
            Path(container_path),
            state=getattr(owner, "STATE_LOCKED", "locked"),
            reason="lock_completed",
        )
    return bool(owner._active_technical_container_locked)


def lock_container(owner, container_path: str, container_id: str):
    """Lock the technical container and archive raw data."""
    from .helpers import _get_technical_archive_folder

    container_manager = get_container_manager(owner.config if hasattr(owner, "config") else None)
    operator_id = "unknown"
    try:
        from difra.gui.operator_manager import OperatorManager

        operator_manager = OperatorManager()
        operator_id = operator_manager.get_current_operator_id() or "unknown"
    except Exception:
        operator_id = "unknown"

    try:
        logger.info(
            "Locking technical container: id=%s path=%s operator=%s",
            container_id,
            str(container_path),
            str(operator_id),
        )
        container_manager.lock_technical_container(
            Path(container_path),
            locked_by=operator_id,
            notes="Auto-locked after generation and validation",
        )
        set_state = getattr(owner, "_set_container_state", None)
        if callable(set_state):
            set_state(
                Path(container_path),
                state=getattr(owner, "STATE_LOCKED", "locked"),
                reason="lock_container_api_success",
            )

        owner._log_technical_event(f"Container {container_id} locked by {operator_id}")
        logger.info("Technical container locked: id=%s operator=%s", container_id, str(operator_id))

        archived_count = 0
        try:
            archive_folder = Path(
                _get_technical_archive_folder(owner.config if hasattr(owner, "config") else None)
            )
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            operator_token = (
                "".join(
                    ch if ch.isalnum() or ch in ("-", "_") else "_"
                    for ch in str(operator_id or "")
                ).strip("_")
                or "unknown"
            )
            archive_subdir = archive_folder / (
                f"{_safe_archive_token(Path(container_path).stem, 'technical')}_"
                f"{operator_token}_{timestamp}"
            )

            file_patterns = None
            if hasattr(owner, "config") and owner.config:
                file_patterns = owner.config.get(
                    "technical_archive_patterns",
                    ["*.txt", "*.dsc", "*.npy", "*.poni", "*_state.json"],
                )

            archived_count = container_manager.archive_technical_data_files(
                container_path=Path(container_path),
                archive_folder=archive_subdir,
                file_patterns=file_patterns,
            )
            updated_sources = _rewrite_technical_source_paths(
                Path(container_path),
                archive_subdir,
            )
            owner._update_aux_table_paths_after_archive(archive_subdir)

            if archived_count > 0:
                owner._log_technical_event(
                    f"Archived {archived_count} data file(s) to {archive_subdir.name}"
                )
            if updated_sources > 0:
                owner._log_technical_event(
                    f"Updated {updated_sources} embedded source path(s) to {archive_subdir.name}"
                )
            logger.info(
                "Archived technical container companion files: id=%s archived=%d folder=%s",
                container_id,
                int(archived_count),
                str(archive_subdir),
            )
            try:
                from difra.gui.session_lifecycle_service import SessionLifecycleService

                SessionLifecycleService.copy_archive_item_to_mirror(
                    archive_subdir,
                    config=owner.config if hasattr(owner, "config") else None,
                    archive_kind="technical",
                )
            except Exception as exc:
                logger.warning("Failed to mirror technical archive folder: %s", exc)
        except Exception as exc:
            logger.warning("Failed to archive data files: %s", exc)
            owner._log_technical_event(f"Warning: Could not archive data files: {exc}")

        try:
            before_mb, after_mb = _repack_hdf5_in_place(Path(container_path))
            owner._log_technical_event(
                f"Compacted technical container: {before_mb:.1f} MB -> {after_mb:.1f} MB"
            )
        except Exception as exc:
            logger.warning("Failed to repack technical container: %s", exc, exc_info=True)
            owner._log_technical_event(
                f"Warning: Could not compact technical container: {exc}"
            )

        QMessageBox.information(
            owner,
            "Container Locked",
            f"✅ Container locked successfully!\n\n"
            f"Container ID: {container_id}\n"
            f"Locked by: {operator_id}\n"
            f"Location: {container_path}\n"
            f"Raw data archived: {archived_count} file(s)\n\n"
            f"This container is now ready for session measurements.",
        )
        return True
    except Exception as exc:
        QMessageBox.critical(
            owner,
            "Lock Failed",
            f"Failed to lock container:\n{exc}\n\nContainer location: {container_path}",
        )
        owner._log_technical_event(f"Failed to lock container: {exc}")
        logger.error(
            "Technical container lock failed: id=%s path=%s error=%s",
            container_id,
            str(container_path),
            str(exc),
        )
        return False
