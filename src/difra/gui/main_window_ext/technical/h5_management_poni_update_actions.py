import logging
from pathlib import Path

from difra.gui.main_window_ext.technical.h5_management_lock_guards import (
    _ensure_distances_configured as ensure_distances_configured,
)


logger = logging.getLogger(__name__)


def _message_box():
    from difra.gui.main_window_ext.technical import h5_management_lock_actions

    return h5_management_lock_actions.QMessageBox


def _container_manager(config):
    from difra.gui.main_window_ext.technical import h5_management_lock_actions

    return h5_management_lock_actions.get_container_manager(config)


def update_active_technical_container_poni(owner):
    """Refresh PONI files for the active technical container."""
    active_path = str(getattr(owner, "_active_technical_container_path", "") or "").strip()
    if not active_path:
        _message_box().warning(
            owner,
            "No Active Container",
            "Load or create a technical container before updating PONI files.",
        )
        return False

    container_path = Path(active_path)
    if not container_path.exists():
        _message_box().warning(
            owner,
            "Container Missing",
            f"Technical container not found:\n{container_path}",
        )
        return False

    container_manager = _container_manager(owner.config if hasattr(owner, "config") else None)
    if container_manager.is_container_locked(container_path):
        set_state = getattr(owner, "_set_container_state", None)
        if callable(set_state):
            set_state(
                Path(container_path),
                state=getattr(owner, "STATE_LOCKED", "locked"),
                reason="poni_update_blocked_locked",
            )
        _message_box().warning(
            owner,
            "Container Locked",
            "Locked technical container cannot be modified.\n\n"
            "Archive it and create/load another active container to update PONI files.",
        )
        return False

    if not ensure_distances_configured(owner, action_name="Upload PONI"):
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
        _message_box().warning(
            owner,
            "No Detector Aliases",
            "Could not determine detector aliases for PONI update.",
        )
        return False

    prompt_selection = getattr(owner, "_prompt_poni_selection_for_lock", None)
    if not callable(prompt_selection):
        _message_box().critical(
            owner,
            "PONI Update Error",
            "PONI selection workflow is unavailable in this build.",
        )
        return False

    if not prompt_selection(aliases):
        return False

    sync_fn = getattr(owner, "_sync_active_technical_container_from_table", None)
    if not callable(sync_fn):
        _message_box().critical(
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
        _message_box().warning(
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
    _message_box().information(
        owner,
        "PONI Updated",
        "PONI calibration files were updated and synced to the active technical container.",
    )
    return True

