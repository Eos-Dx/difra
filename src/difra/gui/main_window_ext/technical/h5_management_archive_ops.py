"""File/archive operations for technical H5 container workflows."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Dict, Optional

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

        @staticmethod
        def question(*_args, **_kwargs):
            return QMessageBox.No

        @staticmethod
        def warning(*_args, **_kwargs):
            return None

from difra.gui.container_api import get_container_manager

logger = logging.getLogger(__name__)


def _safe_archive_token(value: str, fallback: str = "unknown") -> str:
    token = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in str(value or "")
    ).strip("_")
    return token or fallback


def _rewrite_technical_source_paths(container_path: Path, archive_folder: Path) -> int:
    """Repoint embedded source_file/source_ref attrs to archived raw files."""
    import h5py

    container_path = Path(container_path)
    archive_folder = Path(archive_folder)
    updated = 0
    original_mode = container_path.stat().st_mode
    writable_mode = original_mode | stat.S_IWUSR

    try:
        os.chmod(container_path, writable_mode)
    except Exception:
        pass

    try:
        with h5py.File(container_path, "a") as h5f:
            def _visit(_name, obj):
                nonlocal updated
                if not hasattr(obj, "attrs"):
                    return
                for attr_name in ("source_file", "source_ref"):
                    raw_value = obj.attrs.get(attr_name)
                    if raw_value is None:
                        continue
                    if isinstance(raw_value, bytes):
                        raw_value = raw_value.decode("utf-8", errors="replace")
                    raw_text = str(raw_value or "").strip()
                    if not raw_text or raw_text.startswith("h5ref://"):
                        continue
                    candidate = archive_folder / Path(raw_text).name
                    if candidate.exists() and raw_text != str(candidate):
                        obj.attrs[attr_name] = str(candidate)
                        updated += 1

            h5f.visititems(_visit)
    finally:
        try:
            os.chmod(container_path, original_mode)
        except Exception:
            pass

    return updated


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _find_existing_technical_companion_archive_folder(
    container_path: Path,
    archive_base: Path,
) -> Optional[Path]:
    """Return an existing archive folder referenced by embedded raw file paths."""
    import h5py

    container_path = Path(container_path)
    archive_base = Path(archive_base)
    parent_counts: Dict[Path, int] = {}

    if not container_path.exists():
        return None

    try:
        with h5py.File(container_path, "r") as h5f:
            def _visit(_name, obj):
                if not hasattr(obj, "attrs"):
                    return
                for attr_name in ("source_file", "source_ref"):
                    raw_value = obj.attrs.get(attr_name)
                    if raw_value is None:
                        continue
                    if isinstance(raw_value, bytes):
                        raw_value = raw_value.decode("utf-8", errors="replace")
                    raw_text = str(raw_value or "").strip()
                    if not raw_text or raw_text.startswith("h5ref://"):
                        continue

                    raw_path = Path(raw_text)
                    if (
                        raw_path.exists()
                        and raw_path.is_file()
                        and _path_is_relative_to(raw_path.parent, archive_base)
                    ):
                        parent_counts[raw_path.parent] = (
                            parent_counts.get(raw_path.parent, 0) + 1
                        )

            h5f.visititems(_visit)
    except Exception:
        logger.debug(
            "Could not inspect technical companion archive folder for %s",
            str(container_path),
            exc_info=True,
        )
        return None

    if not parent_counts:
        return None

    return sorted(
        parent_counts,
        key=lambda path: (-parent_counts[path], str(path)),
    )[0]


def _unique_archive_destination(archive_folder: Path, file_name: str) -> Path:
    destination = Path(archive_folder) / file_name
    if not destination.exists():
        return destination

    source_name = Path(file_name)
    stem = source_name.stem
    suffix = source_name.suffix
    idx = 2
    while True:
        candidate = Path(archive_folder) / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1


def _repack_hdf5_in_place(container_path: Path) -> tuple[float, float]:
    """Compact an HDF5 file by copying live objects into a fresh file in place."""
    import h5py

    container_path = Path(container_path)
    before_mb = float(container_path.stat().st_size) / 1024.0 / 1024.0
    original_mode = container_path.stat().st_mode
    writable_mode = original_mode | stat.S_IWUSR
    temp_path = container_path.with_name(f".{container_path.name}.repack")

    try:
        os.chmod(container_path, writable_mode)
    except Exception:
        pass

    if temp_path.exists():
        temp_path.unlink()

    try:
        with h5py.File(container_path, "r") as src, h5py.File(temp_path, "w") as dst:
            for key, value in src.attrs.items():
                dst.attrs[key] = value
            for name in src.keys():
                src.copy(name, dst, name=name)
        os.replace(temp_path, container_path)
        os.chmod(container_path, original_mode)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    after_mb = float(container_path.stat().st_size) / 1024.0 / 1024.0
    return before_mb, after_mb


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

            archive_folder = _find_existing_technical_companion_archive_folder(
                h5_file,
                archive_base,
            )
            if archive_folder is None:
                archive_folder = archive_base / (
                    f"{_safe_archive_token(h5_file.stem, 'technical')}_"
                    f"{archive_operator}_{timestamp}"
                )
                archive_folder.mkdir(parents=True, exist_ok=True)
            else:
                archive_folder.mkdir(parents=True, exist_ok=True)

            dest_h5 = _unique_archive_destination(archive_folder, h5_file.name)
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
                    try:
                        _rewrite_technical_source_paths(dest_h5, archive_folder)
                    except Exception as exc:
                        logger.warning(
                            "Failed to rewrite archived source paths for %s: %s",
                            dest_h5,
                            exc,
                            exc_info=True,
                        )
                    owner._log_technical_event(
                        f"Archived {raw_file_count} data file(s) with container"
                    )
            except Exception as exc:
                logger.warning("Failed to archive data files: %s", exc)

            try:
                from difra.gui.session_lifecycle_service import SessionLifecycleService

                SessionLifecycleService.copy_archive_item_to_mirror(
                    archive_folder,
                    config=owner.config if hasattr(owner, "config") else None,
                    archive_kind="technical",
                )
            except Exception as exc:
                logger.warning("Failed to mirror archived technical replacement folder: %s", exc)

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
