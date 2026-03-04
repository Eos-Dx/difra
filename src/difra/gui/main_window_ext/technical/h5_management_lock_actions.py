"""Procedural lock/archive helpers extracted from H5ManagementLockingMixin."""

import logging
import shutil
import time
from pathlib import Path

from PyQt5.QtWidgets import QInputDialog, QMessageBox

from difra.gui.container_api import get_container_manager

logger = logging.getLogger(__name__)


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
                    owner._log_technical_event(
                        f"Added error attributes to archived container: {h5_file.name}"
                    )
                except Exception as exc:
                    logger.warning("Failed to add error attributes to %s: %s", h5_file.name, exc)

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


def lock_active_technical_container(owner):
    """Lock currently active technical container."""
    import h5py

    if hasattr(owner, "_sync_active_technical_container_from_table"):
        owner._sync_active_technical_container_from_table(show_errors=True)

    active_path = str(getattr(owner, "_active_technical_container_path", "") or "").strip()
    if not active_path:
        QMessageBox.warning(
            owner,
            "No Active Container",
            "No active technical container loaded or created.",
        )
        return

    container_path = Path(active_path)
    if not container_path.exists():
        QMessageBox.warning(
            owner,
            "Container Missing",
            f"Technical container not found:\n{container_path}",
        )
        return

    container_manager = get_container_manager(owner.config if hasattr(owner, "config") else None)
    if container_manager.is_container_locked(container_path):
        QMessageBox.information(
            owner,
            "Already Locked",
            f"Container is already locked:\n{container_path.name}",
        )
        return

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
        return

    if not owner._validate_container_before_lock(container_path, container_id):
        return

    lock_container(owner, str(container_path), container_id)
    owner._active_technical_container_locked = True


def lock_container(owner, container_path: str, container_id: str):
    """Lock the technical container and archive raw data."""
    from difra.gui.operator_manager import OperatorManager
    from .helpers import _get_technical_archive_folder

    container_manager = get_container_manager(owner.config if hasattr(owner, "config") else None)
    operator_manager = OperatorManager()
    operator_id = operator_manager.get_current_operator_id() or "unknown"

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
