"""Procedural lock/archive helpers extracted from H5ManagementLockingMixin."""

import logging
import shutil
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

logger = logging.getLogger(__name__)


def _ensure_distances_configured(owner, *, action_name: str) -> bool:
    """Ensure detector distances are configured before sensitive technical actions."""
    get_aliases = getattr(owner, "_get_active_detector_aliases", None)
    aliases = []
    if callable(get_aliases):
        try:
            aliases = [str(alias).strip() for alias in (get_aliases() or []) if str(alias).strip()]
        except Exception:
            aliases = []

    distance_map = {}
    get_distance_map = getattr(owner, "_distance_map_by_alias", None)
    if not callable(get_distance_map):
        return True
    if callable(get_distance_map):
        try:
            distance_map = {
                str(alias).strip(): float(value)
                for alias, value in (get_distance_map() or {}).items()
                if str(alias).strip()
            }
        except Exception:
            distance_map = {}

    missing = [alias for alias in aliases if alias not in distance_map]
    if not missing:
        sync_state = getattr(owner, "_sync_container_state", None)
        active_getter = getattr(owner, "_active_technical_container_path_obj", None)
        if callable(sync_state) and callable(active_getter):
            active_path = active_getter()
            if active_path is not None:
                sync_state(Path(active_path), reason=f"distances_verified:{action_name}")
        return True

    QMessageBox.information(
        owner,
        "Detector Distances Required",
        f"Detector distances are required before '{action_name}'.\n\n"
        "Please set distances now.",
    )

    configure_distances = getattr(owner, "configure_detector_distances", None)
    if callable(configure_distances):
        setattr(owner, "_suppress_distance_auto_container_creation", True)
        try:
            configure_distances()
        finally:
            setattr(owner, "_suppress_distance_auto_container_creation", False)

    distance_map = {}
    if callable(get_distance_map):
        try:
            distance_map = {
                str(alias).strip(): float(value)
                for alias, value in (get_distance_map() or {}).items()
                if str(alias).strip()
            }
        except Exception:
            distance_map = {}
    missing = [alias for alias in aliases if alias not in distance_map]
    if not missing:
        sync_state = getattr(owner, "_sync_container_state", None)
        active_getter = getattr(owner, "_active_technical_container_path_obj", None)
        if callable(sync_state) and callable(active_getter):
            active_path = active_getter()
            if active_path is not None:
                sync_state(Path(active_path), reason=f"distances_configured:{action_name}")
        return True

    set_state = getattr(owner, "_set_active_container_state", None)
    if callable(set_state):
        set_state(
            state=getattr(owner, "STATE_PENDING_DISTANCES", "pending_distances"),
            reason=f"missing_distances:{action_name}",
        )
    QMessageBox.warning(
        owner,
        "Distances Not Configured",
        "Operation cancelled: distances are still missing for detector(s): "
        + ", ".join(missing),
    )
    if hasattr(owner, "_log_technical_event"):
        owner._log_technical_event(
            f"{action_name} blocked: detector distances missing for {missing}"
        )
    return False


def archive_existing_containers(owner, storage_folder: str) -> int:
    """Archive any existing .h5 containers in storage folder before creating new one."""
    from .helpers import _get_technical_archive_folder

    container_manager = get_container_manager(owner.config if hasattr(owner, "config") else None)
    storage_path = Path(storage_folder)
    if not storage_path.exists():
        return 0

    h5_files = list(storage_path.glob("*.h5"))
    if not h5_files:
        return 0

    archive_base = Path(
        _get_technical_archive_folder(owner.config if hasattr(owner, "config") else None)
    )

    archived_count = 0
    for h5_file in h5_files:
        try:
            filename = h5_file.stem
            parts = filename.split("_")
            container_id = parts[1] if len(parts) >= 2 else filename

            is_locked = container_manager.is_container_locked(h5_file)
            created_by_error = False
            error_reason = ""

            if not is_locked:
                reply = QMessageBox.question(
                    owner,
                    "Unvalidated Technical Container",
                    f"Found unvalidated technical container:\n\n"
                    f"Container ID: {container_id}\n"
                    f"File: {h5_file.name}\n\n"
                    f"You are about to create a new technical container.\n"
                    f"The existing container will be archived.\n\n"
                    f"Was this container created by error?\n\n"
                    f"Select 'Yes' to mark as error (you can provide a reason).\n"
                    f"Select 'No' to archive without error marking.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )

                if reply == QMessageBox.Yes:
                    created_by_error = True
                    reason, ok = QInputDialog.getText(
                        owner,
                        "Error Reason",
                        f"Why was container {container_id} created by error?\n\n"
                        f"(Optional - provide brief description)",
                    )
                    if ok and reason.strip():
                        error_reason = reason.strip()
                    else:
                        error_reason = "User marked as error without specifying reason"

                    owner._log_technical_event(
                        f"Container {container_id} marked as created_by_error: {error_reason}"
                    )

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            archive_operator = "unknown"
            try:
                import h5py

                with h5py.File(h5_file, "r") as file_handle:
                    raw_operator = (
                        file_handle.attrs.get("locked_by")
                        or file_handle.attrs.get("operator_id")
                    )
                    if isinstance(raw_operator, bytes):
                        raw_operator = raw_operator.decode("utf-8", errors="replace")
                    archive_operator = (
                        "".join(
                            ch if ch.isalnum() or ch in ("-", "_") else "_"
                            for ch in str(raw_operator or "")
                        ).strip("_")
                        or "unknown"
                    )
            except Exception:
                archive_operator = "unknown"

            archive_folder = archive_base / f"{container_id}_{archive_operator}_{timestamp}"
            archive_folder.mkdir(parents=True, exist_ok=True)

            dest_h5 = archive_folder / h5_file.name
            shutil.move(str(h5_file), str(dest_h5))

            if created_by_error:
                import h5py

                try:
                    with h5py.File(dest_h5, "a") as file_handle:
                        file_handle.attrs["created_by_error"] = True
                        file_handle.attrs["error_reason"] = error_reason
                        file_handle.attrs["archived_timestamp"] = timestamp
                        file_handle.attrs["container_state"] = "archived"
                        file_handle.attrs["container_state_reason"] = "archived_during_container_replacement"
                        file_handle.attrs["container_state_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    owner._log_technical_event(
                        f"Added error attributes to archived container: {h5_file.name}"
                    )
                except Exception as exc:
                    logger.warning("Failed to add error attributes to %s: %s", h5_file.name, exc)
            else:
                try:
                    import h5py

                    with h5py.File(dest_h5, "a") as file_handle:
                        file_handle.attrs["container_state"] = "archived"
                        file_handle.attrs["container_state_reason"] = "archived_during_container_replacement"
                        file_handle.attrs["container_state_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                except Exception as exc:
                    logger.debug(
                        "Failed to persist archive state marker for %s: %s",
                        h5_file.name,
                        exc,
                        exc_info=True,
                    )

            owner._log_technical_event(
                f"Archived H5 container: {h5_file.name} -> {archive_folder.name}/"
                + (f" [ERROR: {error_reason}]" if created_by_error else "")
            )

            file_patterns = None
            if hasattr(owner, "config") and owner.config:
                file_patterns = owner.config.get(
                    "technical_archive_patterns",
                    ["*.txt", "*.dsc", "*.npy", "*.poni", "*_state.json"],
                )

            try:
                archive_technical_data_files = container_manager.archive_technical_data_files
                dummy_container_path = storage_path / h5_file.name
                raw_file_count = archive_technical_data_files(
                    container_path=dummy_container_path,
                    archive_folder=archive_folder,
                    file_patterns=file_patterns,
                )

                if raw_file_count > 0:
                    owner._log_technical_event(
                        f"Archived {raw_file_count} data file(s) with container"
                    )
            except Exception as exc:
                logger.warning("Failed to archive data files: %s", exc)

            archived_count += 1

        except Exception as exc:
            logger.warning("Failed to archive %s: %s", h5_file.name, exc)
            owner._log_technical_event(f"Warning: Could not archive {h5_file.name}: {exc}")

    return archived_count


def update_aux_table_paths_after_archive(owner, archive_folder: Path) -> int:
    """Remap aux table file paths to archived locations for visualization."""
    try:
        from difra.gui.main_window_ext import technical_measurements as tm
    except Exception:
        return 0

    if not hasattr(owner, "auxTable") or owner.auxTable is None:
        return 0

    updated = 0
    archive_folder = Path(archive_folder)
    for row in range(owner.auxTable.rowCount()):
        file_item = owner.auxTable.item(row, 1)
        if file_item is None:
            continue

        old_path = str(file_item.data(tm.Qt.UserRole) or "").strip()
        if not old_path:
            continue

        old_file = Path(old_path)
        if old_file.exists():
            continue

        candidate = archive_folder / old_file.name
        if not candidate.exists():
            continue

        file_item.setData(tm.Qt.UserRole, str(candidate))
        updated += 1

    if updated > 0:
        owner._log_technical_event(
            f"Updated {updated} technical table path(s) to archive folder: {archive_folder.name}"
        )
    return updated


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


def update_active_technical_container_poni(owner):
    """Refresh PONI files for the active technical container."""
    active_path = str(getattr(owner, "_active_technical_container_path", "") or "").strip()
    if not active_path:
        QMessageBox.warning(
            owner,
            "No Active Container",
            "Load or create a technical container before updating PONI files.",
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
                reason="poni_update_blocked_locked",
            )
        QMessageBox.warning(
            owner,
            "Container Locked",
            "Locked technical container cannot be modified.\n\n"
            "Archive it and create/load another active container to update PONI files.",
        )
        return False

    if not _ensure_distances_configured(owner, action_name="Upload PONI"):
        return False

    set_state = getattr(owner, "_set_container_state", None)
    if callable(set_state):
        set_state(
            Path(container_path),
            state=getattr(owner, "STATE_PENDING_PONI", "pending_poni"),
            reason="poni_update_requested",
        )

    aliases = []
    collect_aliases = getattr(owner, "_collect_lock_detector_aliases", None)
    if callable(collect_aliases):
        try:
            aliases = list(collect_aliases(container_path) or [])
        except Exception:
            aliases = []

    if not aliases:
        get_active_aliases = getattr(owner, "_get_active_detector_aliases", None)
        if callable(get_active_aliases):
            try:
                aliases = list(get_active_aliases() or [])
            except Exception:
                aliases = []

    aliases = sorted({str(alias).strip() for alias in aliases if str(alias).strip()})
    if not aliases:
        QMessageBox.warning(
            owner,
            "No Detector Aliases",
            "Could not determine detector aliases for PONI update.",
        )
        return False

    prompt_selection = getattr(owner, "_prompt_poni_selection_for_lock", None)
    if not callable(prompt_selection):
        QMessageBox.critical(
            owner,
            "PONI Update Error",
            "PONI selection workflow is unavailable in this build.",
        )
        return False

    if not prompt_selection(aliases):
        return False

    sync_fn = getattr(owner, "_sync_active_technical_container_from_table", None)
    if not callable(sync_fn):
        QMessageBox.critical(
            owner,
            "PONI Update Error",
            "Container synchronization is unavailable.",
        )
        return False

    synced = bool(sync_fn(show_errors=False))
    if not synced:
        if callable(set_state):
            set_state(
                Path(container_path),
                state=getattr(owner, "STATE_PENDING_PONI", "pending_poni"),
                reason="poni_sync_failed",
            )
        QMessageBox.warning(
            owner,
            "PONI Sync Failed",
            "PONI files were selected, but active container sync failed.\n\n"
            "Fix technical rows and retry.",
        )
        if hasattr(owner, "_log_technical_event"):
            owner._log_technical_event(
                f"PONI update failed: container sync failed for {container_path.name}"
            )
        return False

    if callable(set_state):
        set_state(
            Path(container_path),
            state=getattr(owner, "STATE_PENDING_PONI_REVIEW", "pending_poni_review"),
            reason="poni_synced_review_required",
        )

    show_preview = getattr(owner, "_show_poni_center_preview_for_container", None)
    run_review = getattr(owner, "_run_poni_center_review_workflow", None)
    if callable(run_review):
        reviewed = bool(
            run_review(
                Path(container_path),
                container_id=container_path.stem,
                prompt_reload_on_reject=True,
            )
        )
        if not reviewed:
            return False
    elif callable(show_preview):
        try:
            show_preview(str(container_path))
            if callable(set_state):
                set_state(
                    Path(container_path),
                    state=getattr(owner, "STATE_PENDING_PONI_REVIEW", "pending_poni_review"),
                    reason="poni_preview_shown",
                )
        except Exception:
            logger.debug("Suppressed PONI center preview error after update", exc_info=True)

    sync_state = getattr(owner, "_sync_container_state", None)
    if callable(sync_state):
        sync_state(Path(container_path), reason="poni_update_completed")

    if hasattr(owner, "_log_technical_event"):
        owner._log_technical_event(
            f"Updated PONI files for active technical container: {container_path.name}"
        )
    QMessageBox.information(
        owner,
        "PONI Updated",
        "PONI calibration files were updated and synced to the active technical container.",
    )
    return True


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
            archive_subdir = archive_folder / f"{container_id}_{operator_token}_{timestamp}"

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
            owner._update_aux_table_paths_after_archive(archive_subdir)

            if archived_count > 0:
                owner._log_technical_event(
                    f"Archived {archived_count} data file(s) to {archive_subdir.name}"
                )
            logger.info(
                "Archived technical container companion files: id=%s archived=%d folder=%s",
                container_id,
                int(archived_count),
                str(archive_subdir),
            )
        except Exception as exc:
            logger.warning("Failed to archive data files: %s", exc)
            owner._log_technical_event(f"Warning: Could not archive data files: {exc}")

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
