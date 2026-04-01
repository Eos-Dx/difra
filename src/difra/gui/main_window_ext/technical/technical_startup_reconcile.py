"""Startup reconciliation helpers for technical containers."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from PyQt5.QtWidgets import QInputDialog, QMessageBox

from difra.gui.container_api import get_container_manager

logger = logging.getLogger(__name__)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_archived_technical_containers(owner):
    from .helpers import _get_technical_archive_folder

    archive_base = Path(
        _get_technical_archive_folder(owner.config if hasattr(owner, "config") else None)
    )
    if not archive_base.exists():
        return []

    archived = []
    seen = set()
    for pattern in ("technical_*.nxs.h5", "technical_*.h5"):
        for tech_path in archive_base.rglob(pattern):
            if not tech_path.is_file():
                continue
            try:
                resolved = str(tech_path.resolve())
            except (OSError, RuntimeError, TypeError, ValueError):
                resolved = str(tech_path)
            if resolved in seen:
                continue
            seen.add(resolved)
            archived.append(tech_path)
    archived.sort(
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
        reverse=True,
    )
    return archived


def find_duplicate_archived_technical_container(owner, container_path: Path):
    container_path = Path(container_path)
    if not container_path.exists():
        return None

    try:
        current_size = int(container_path.stat().st_size)
    except (OSError, RuntimeError, TypeError, ValueError):
        current_size = None

    current_hash = None
    for archived_path in list_archived_technical_containers(owner):
        try:
            if current_size is not None and int(archived_path.stat().st_size) != current_size:
                continue
        except (OSError, RuntimeError, TypeError, ValueError):
            continue

        try:
            if current_hash is None:
                current_hash = sha256_path(container_path)
            if sha256_path(archived_path) == current_hash:
                return archived_path
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Suppressed exception while comparing technical container duplicates",
                exc_info=True,
            )
    return None


def delete_storage_technical_container(owner, container_path: Path) -> bool:
    container_path = Path(container_path)
    if not container_path.exists():
        return False

    try:
        container_path.unlink()
    except Exception:
        logger.warning(
            "Failed to delete duplicate technical container: %s",
            container_path,
            exc_info=True,
        )
        return False

    current_active = owner._active_technical_container_path_obj()
    if current_active is not None and owner._paths_same(current_active, container_path):
        owner._active_technical_container_path = ""
        owner._active_technical_container_locked = False
        if hasattr(owner, "_refresh_technical_output_folder_lock"):
            try:
                owner._refresh_technical_output_folder_lock()
            except (AttributeError, RuntimeError, TypeError):
                logger.debug(
                    "Suppressed exception while refreshing technical folder lock after delete",
                    exc_info=True,
                )
    return True


def format_startup_technical_container_option(owner, container_path: Path) -> str:
    container_path = Path(container_path)
    container_manager = get_container_manager(
        owner.config if hasattr(owner, "config") else None
    )

    try:
        modified_label = owner.time.strftime(
            "%Y-%m-%d %H:%M",
            owner.time.localtime(container_path.stat().st_mtime),
        )
    except Exception:
        import time as _time

        try:
            modified_label = _time.strftime(
                "%Y-%m-%d %H:%M",
                _time.localtime(container_path.stat().st_mtime),
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            modified_label = "unknown time"

    try:
        distance_cm = owner._read_technical_container_distance_cm(container_path)
    except Exception:
        distance_cm = None
    distance_label = (
        f"{float(distance_cm):.2f} cm" if distance_cm is not None else "unknown distance"
    )

    try:
        is_locked = bool(container_manager.is_container_locked(container_path))
    except Exception:
        is_locked = False
    status_label = "LOCKED" if is_locked else "UNLOCKED"

    return f"{modified_label} | {distance_label} | {status_label} | {container_path.name}"


def prompt_startup_technical_container_selection(owner, candidates):
    options = []
    labels = []

    for path in candidates:
        options.append(path)
        labels.append(format_startup_technical_container_option(owner, path))

    labels.append("No container / archive or remove all")
    choice, ok = QInputDialog.getItem(
        owner,
        "Multiple Technical Containers Found",
        "More than one technical container was found in Technical Measurements.\n\n"
        "Each option shows: date/time | distance | lock status | file name.\n\n"
        "Choose which container to keep active. All others will be compared with the archive "
        "and either removed as duplicates or archived.\n\n"
        "Choose 'No container / archive or remove all' to keep none.",
        labels,
        0,
        False,
    )
    if not ok:
        return "cancel", None

    choice_text = str(choice or "").strip()
    if choice_text == labels[-1]:
        return "none", None

    for path, label in zip(options, labels[:-1]):
        if label == choice_text:
            return "keep", path
    return "cancel", None


def reconcile_startup_technical_containers(owner):
    from .helpers import _get_technical_storage_folder

    storage_folder = Path(
        _get_technical_storage_folder(owner.config if hasattr(owner, "config") else None)
    )
    candidates = owner._list_storage_technical_containers(storage_folder)
    if len(candidates) <= 1:
        return True

    action, selected_path = owner._prompt_startup_technical_container_selection(candidates)
    if action == "cancel":
        owner._log_technical_event("Startup technical container reconciliation cancelled by user")
        return False

    kept_path = Path(selected_path) if selected_path is not None else None

    for path in candidates:
        if kept_path is not None and owner._paths_same(path, kept_path):
            continue

        duplicate_archived = owner._find_duplicate_archived_technical_container(path)
        if duplicate_archived is not None:
            if owner._delete_storage_technical_container(path):
                owner._log_technical_event(
                    f"Removed duplicate startup technical container: {path.name} "
                    f"(matches archived {duplicate_archived.name})"
                )
            continue

        archived = owner._lock_and_archive_technical_container(path)
        owner._log_technical_event(
            f"Archived extra startup technical container: {path.name} -> {archived.name}"
        )

    if kept_path is None:
        owner._active_technical_container_path = ""
        owner._active_technical_container_locked = False
        if hasattr(owner, "auxTable") and owner.auxTable is not None:
            try:
                owner.auxTable.setRowCount(0)
            except Exception:
                logger.debug(
                    "Suppressed exception while clearing aux table after startup reconciliation",
                    exc_info=True,
                )
        if hasattr(owner, "_refresh_technical_output_folder_lock"):
            try:
                owner._refresh_technical_output_folder_lock()
            except Exception:
                logger.debug(
                    "Suppressed exception while refreshing technical output lock after startup reconciliation",
                    exc_info=True,
                )
        owner._log_technical_event("No startup technical container kept active")
        return True

    if kept_path.exists():
        loaded = owner.load_technical_h5_from_path(str(kept_path), show_dialogs=False)
        if not loaded:
            QMessageBox.warning(
                owner,
                "Technical Container",
                f"Failed to load selected technical container:\n{kept_path}",
            )
            return False
        owner._log_technical_event(
            f"Kept startup technical container active: {kept_path.name}"
        )
    return True
