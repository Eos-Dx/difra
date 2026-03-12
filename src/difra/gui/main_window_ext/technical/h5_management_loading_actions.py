"""Procedural loading/create helpers extracted from H5ManagementLoadingMixin."""

import logging
from pathlib import Path

from PyQt5.QtWidgets import QMessageBox

from difra.gui.container_api import get_container_manager, get_technical_container
from difra.gui.matador_upload_api import build_matador_upload_api

logger = logging.getLogger(__name__)


def finalize_active_session_for_new_technical_container(owner) -> bool:
    """Close/archive the active session before creating a new technical container."""
    if not hasattr(owner, "session_manager") or owner.session_manager is None:
        return True
    if not owner.session_manager.is_session_active():
        return True

    try:
        info = owner.session_manager.get_session_info() or {}
    except Exception:
        info = {}

    raw_session_path = str(getattr(owner.session_manager, "session_path", "") or "").strip()
    if not raw_session_path:
        return False
    session_path = Path(raw_session_path)
    sample_id = (
        info.get("sample_id")
        or getattr(owner.session_manager, "sample_id", None)
        or "UNKNOWN"
    )

    reply = QMessageBox.question(
        owner,
        "Active Session Will Be Closed",
        "Creating a new technical container requires closing the active session.\n\n"
        f"Sample ID: {sample_id}\n"
        f"Session: {session_path.name}\n\n"
        "The current session data will be saved, the session container will be "
        "locked, a cloud-send attempt will be made, and if sending is unavailable "
        "the session will be archived as LOCKED / UNSENT.\n\n"
        "Continue?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if reply != QMessageBox.Yes:
        return False

    try:
        from difra.gui.session_finalize_workflow import SessionFinalizeWorkflow
        from difra.gui.session_lifecycle_actions import SessionLifecycleActions

        container_manager = get_container_manager(
            owner.config if hasattr(owner, "config") else None
        )
        measurements_folder = session_path.parent
        lock_user = getattr(owner.session_manager, "operator_id", None)

        readable_meta = SessionFinalizeWorkflow.ensure_human_readable_metadata(
            session_path=session_path,
            logger=logger,
        )
        SessionFinalizeWorkflow.store_json_state_in_container(
            session_path=session_path,
            measurements_folder=measurements_folder,
            sample_id=sample_id,
            logger=logger,
        )
        SessionLifecycleActions.finalize_session_container(
            session_path=session_path,
            container_manager=container_manager,
            lock_user=lock_user,
        )

        send_error = None
        try:
            owner._attempt_forced_session_send(session_path)
        except Exception as exc:
            send_error = exc

        archive_dest, archived_count = SessionFinalizeWorkflow.archive_measurement_files(
            measurements_folder=measurements_folder,
            sample_id=readable_meta.get("sample_id") or sample_id,
            session_id=readable_meta.get("session_id"),
            study_name=readable_meta.get("study_name"),
            project_id=readable_meta.get("project_id"),
            operator_id=readable_meta.get("operator_id"),
            config=owner.config if hasattr(owner, "config") else None,
            logger=logger,
        )
        SessionFinalizeWorkflow.create_session_bundle_zip(
            session_path=session_path,
            archive_folder=archive_dest,
            logger=logger,
        )
        archived_session_path = SessionFinalizeWorkflow.archive_session_container_into_folder(
            session_path=session_path,
            archive_folder=archive_dest,
            logger=logger,
        )
        uploader_id = None
        if hasattr(owner, "operator_manager") and owner.operator_manager is not None:
            get_current_operator_id = getattr(
                owner.operator_manager, "get_current_operator_id", None
            )
            if callable(get_current_operator_id):
                uploader_id = get_current_operator_id()
        if not uploader_id and hasattr(owner, "config") and isinstance(owner.config, dict):
            uploader_id = owner.config.get("operator_id")

        upload_session_id = SessionLifecycleActions.create_upload_session_id(
            uploader_id=uploader_id,
            lock_user=lock_user,
        )
        upload_api = build_matador_upload_api(
            owner.config if hasattr(owner, "config") and isinstance(owner.config, dict) else None
        )
        upload_result = SessionLifecycleActions.execute_upload_stub(
            Path(archived_session_path),
            uploader_id=uploader_id,
            lock_user=lock_user,
            upload_session_id=upload_session_id,
            upload_api=upload_api,
            simulate_failure=send_error is not None,
            failure_message=f"Cloud send failed: {send_error}" if send_error is not None else None,
        )

        mark_transferred = getattr(container_manager, "mark_container_transferred", None)
        if callable(mark_transferred):
            mark_transferred(Path(archived_session_path), sent=upload_result.success)
        SessionLifecycleActions.write_upload_metadata(
            Path(archived_session_path),
            uploader_id=uploader_id,
            lock_user=lock_user,
        )
        SessionLifecycleActions.write_upload_result_metadata(
            Path(archived_session_path),
            upload_result=upload_result,
        )
        SessionLifecycleActions.append_upload_attempt_log(
            Path(archived_session_path),
            operator_id=str(uploader_id or lock_user or "unknown"),
            upload_result=upload_result,
        )

        owner.session_manager.close_session()
        if hasattr(owner, "update_session_status"):
            owner.update_session_status()

        if hasattr(owner, "_append_session_log"):
            owner._append_session_log(
                "Forced session close before new technical container: "
                f"{archived_session_path.name}"
            )

        if not upload_result.success:
            QMessageBox.warning(
                owner,
                "Session Send Failed",
                "The active session was closed automatically before creating a new "
                "technical container.\n\n"
                f"Upload error: {upload_result.message}\n\n"
                "The session was archived as LOCKED / UNSENT.\n"
                f"Upload session: {upload_result.upload_session_id}\n"
                f"Archived container: {archived_session_path.name}\n"
                f"Archive folder: {archive_dest}\n"
                f"Archived measurement files: {archived_count}",
            )
        else:
            QMessageBox.information(
                owner,
                "Session Archived",
                "The active session was closed and sent successfully before "
                "creating a new technical container.\n\n"
                f"Upload session: {upload_result.upload_session_id}\n"
                f"Archived container: {archived_session_path.name}\n"
                f"Archive folder: {archive_dest}",
            )
        return True
    except Exception as exc:
        QMessageBox.critical(
            owner,
            "Session Close Failed",
            "Could not close and archive the active session before creating a new "
            f"technical container:\n\n{exc}",
        )
        logger.error(
            "Failed to force-close active session before new technical container: %s",
            exc,
            exc_info=True,
        )
        return False


def prompt_existing_technical_container_resolution(owner, existing_path: Path):
    """Ask whether to reuse or replace a matching technical container."""
    msg_box = QMessageBox(owner)
    msg_box.setWindowTitle("Technical Container Already Exists")
    msg_box.setIcon(QMessageBox.Warning)
    msg_box.setText(
        "A technical container already exists for the current setup.\n\n"
        f"Container: {existing_path.name}\n\n"
        "Choose whether to keep using the existing container or archive it and create a new one."
    )
    use_btn = msg_box.addButton("Use Existing", QMessageBox.AcceptRole)
    new_btn = msg_box.addButton("Create New", QMessageBox.ActionRole)
    cancel_btn = msg_box.addButton(QMessageBox.Cancel)
    msg_box.setDefaultButton(cancel_btn)
    msg_box.exec_()

    clicked = msg_box.clickedButton()
    if clicked == use_btn:
        if owner.load_technical_h5_from_path(str(existing_path), show_dialogs=False):
            owner._log_technical_event(
                f"Reusing existing technical container: {existing_path.name}"
            )
            return "use_existing"
        QMessageBox.warning(
            owner,
            "Technical Container",
            f"Failed to load existing technical container:\n{existing_path}",
        )
        return "cancel"

    if clicked == new_btn:
        if not finalize_active_session_for_new_technical_container(owner):
            return "cancel"

        try:
            current_active = owner._active_technical_container_path_obj()
            if current_active is not None and owner._paths_same(current_active, existing_path):
                if hasattr(owner, "_sync_active_technical_container_from_table"):
                    owner._sync_active_technical_container_from_table(show_errors=True)

            archived = owner._lock_and_archive_technical_container(existing_path)
            owner._log_technical_event(
                f"Archived previous technical container: {existing_path.name} -> {archived.name}"
            )
        except Exception as exc:
            QMessageBox.critical(
                owner,
                "Archive Failed",
                f"Failed to archive existing technical container:\n{exc}",
            )
            return "cancel"

        return "create_new"

    return "cancel"


def create_new_active_technical_container(owner, *, clear_table: bool = False):
    """Create or reuse the active technical container for the current detector setup."""
    from .helpers import _get_technical_storage_folder

    distances_by_alias = owner._distance_map_by_alias()

    storage_folder = _get_technical_storage_folder(
        owner.config if hasattr(owner, "config") else None
    )
    technical_container = get_technical_container(
        owner.config if hasattr(owner, "config") else None
    )

    root_distance_cm = (
        float(next(iter(distances_by_alias.values())))
        if distances_by_alias
        else float((owner.config or {}).get("default_technical_distance_cm", 17.0))
    )
    storage_containers = owner._list_storage_technical_containers(storage_folder)
    active_path = owner._active_technical_container_path_obj()

    if distances_by_alias:
        matching_candidates = [
            path
            for path in storage_containers
            if owner._distance_matches(
                owner._read_technical_container_distance_cm(path),
                root_distance_cm,
            )
        ]
        stale_paths = [
            path
            for path in storage_containers
            if not any(owner._paths_same(path, match) for match in matching_candidates)
        ]
    else:
        matching_candidates = storage_containers[:1]
        stale_paths = storage_containers[1:]

    if (
        active_path is not None
        and active_path.exists()
        and not any(owner._paths_same(active_path, path) for path in storage_containers)
    ):
        active_distance = owner._read_technical_container_distance_cm(active_path)
        if (not distances_by_alias) or owner._distance_matches(active_distance, root_distance_cm):
            matching_candidates.insert(0, active_path)

    matching_path = matching_candidates[0] if matching_candidates else None
    active_is_stale = bool(
        active_path is not None
        and any(owner._paths_same(active_path, path) for path in stale_paths)
    )

    if active_is_stale:
        if not finalize_active_session_for_new_technical_container(owner):
            return None

    for stale_path in stale_paths:
        try:
            archived = owner._lock_and_archive_technical_container(stale_path)
            owner._log_technical_event(
                f"Archived stale technical container: {stale_path.name} -> {archived.name}"
            )
        except Exception as exc:
            QMessageBox.critical(
                owner,
                "Archive Failed",
                f"Failed to archive outdated technical container:\n{exc}",
            )
            return None

    if matching_path is not None and matching_path.exists():
        decision = prompt_existing_technical_container_resolution(
            owner,
            existing_path=matching_path,
        )
        if decision == "use_existing":
            return matching_path
        if decision != "create_new":
            return None
    else:
        if not active_is_stale and not finalize_active_session_for_new_technical_container(owner):
            return None

    refreshed_distances = dict(distances_by_alias or {})

    # For new container creation always prompt distance configuration first.
    # If user cancels, creation still continues, but lock/PONI flows will require
    # configured distances later.
    if hasattr(owner, "configure_detector_distances") and not bool(
        getattr(owner, "_skip_distance_prompt_once", False)
    ):
        setattr(owner, "_suppress_distance_auto_container_creation", True)
        try:
            owner.configure_detector_distances()
        except Exception as exc:
            logger.warning(
                "Distance configuration dialog failed before new technical container creation: %s",
                exc,
                exc_info=True,
            )
        finally:
            setattr(owner, "_suppress_distance_auto_container_creation", False)

        try:
            refreshed_distances = owner._distance_map_by_alias()
        except Exception:
            refreshed_distances = {}
        if refreshed_distances:
            owner._log_technical_event(
                f"Detector distances confirmed before new container creation: {refreshed_distances}"
            )
        else:
            owner._log_technical_event(
                "Detector distances were not confirmed at creation time; deferred until PONI/lock."
            )

    container_id, file_path = technical_container.create_technical_container(
        folder=storage_folder,
        distance_cm=root_distance_cm,
        producer_software=str(owner.config.get("producer_software") or "difra"),
        producer_version=str(
            owner.config.get("producer_version")
            or owner.config.get("container_version")
            or "unknown"
        ),
    )

    owner._set_active_technical_container(file_path)
    set_state = getattr(owner, "_set_container_state", None)
    if callable(set_state):
        if refreshed_distances:
            set_state(
                Path(file_path),
                state=getattr(owner, "STATE_PENDING_PONI", "pending_poni"),
                reason="container_created_distances_confirmed",
            )
        else:
            set_state(
                Path(file_path),
                state=getattr(owner, "STATE_PENDING_DISTANCES", "pending_distances"),
                reason="container_created_without_distances",
            )
    owner._log_technical_event(
        f"Created technical container: {Path(file_path).name} (id={container_id})"
    )

    if clear_table and hasattr(owner, "auxTable") and owner.auxTable is not None:
        owner.auxTable.setRowCount(0)

    owner._sync_active_technical_container_from_table(show_errors=True)
    return Path(file_path)


def ensure_active_technical_container_available(
    owner,
    *,
    for_edit: bool = False,
    prompt_on_locked: bool = False,
) -> bool:
    """Ensure there is an active technical container suitable for the requested action."""
    container_manager = get_container_manager(owner.config if hasattr(owner, "config") else None)
    active_path = owner._active_technical_container_path_obj()

    if active_path is None or not active_path.exists():
        return create_new_active_technical_container(owner) is not None

    is_locked = bool(container_manager.is_container_locked(active_path))
    owner._active_technical_container_locked = is_locked
    if not is_locked:
        return True

    if not for_edit:
        return True

    if not prompt_on_locked:
        return False

    msg_box = QMessageBox(owner)
    msg_box.setWindowTitle("Technical Container Locked")
    msg_box.setIcon(QMessageBox.Warning)
    msg_box.setText(
        "Active technical container is locked.\n\n"
        f"Container: {active_path.name}\n\n"
        "Choose how to continue measurements:"
    )
    unlock_btn = msg_box.addButton("Unlock && Append", QMessageBox.AcceptRole)
    new_btn = msg_box.addButton("Create New Container", QMessageBox.ActionRole)
    cancel_btn = msg_box.addButton(QMessageBox.Cancel)
    msg_box.setDefaultButton(cancel_btn)
    msg_box.exec_()

    clicked = msg_box.clickedButton()
    if clicked == unlock_btn:
        try:
            container_manager.unlock_container(active_path)
            owner._active_technical_container_locked = False
            owner._log_technical_event(
                f"Unlocked technical container for append: {active_path.name}"
            )
            return True
        except Exception as exc:
            QMessageBox.critical(
                owner,
                "Unlock Failed",
                f"Failed to unlock technical container:\n{exc}",
            )
            return False

    if clicked == new_btn:
        return create_new_active_technical_container(owner, clear_table=True) is not None

    return False
