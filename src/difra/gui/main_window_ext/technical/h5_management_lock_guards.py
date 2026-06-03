import logging
from pathlib import Path


logger = logging.getLogger(__name__)


def _message_box():
    from difra.gui.main_window_ext.technical import h5_management_lock_actions

    return h5_management_lock_actions.QMessageBox


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

    _message_box().information(
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
        sync_table = getattr(owner, "_sync_active_technical_container_from_table", None)
        if callable(sync_table):
            setattr(owner, "_use_draft_distances_for_next_sync", True)
            sync_table(show_errors=True)

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
    _message_box().warning(
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


