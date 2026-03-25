"""Procedural session flow helpers extracted from SessionFlowMixin."""

import logging
from pathlib import Path

from PyQt5.QtWidgets import QDialog, QMessageBox

from difra.gui.container_api import get_container_manager, get_schema
from difra.gui.main_window_ext.new_session_dialog import NewSessionDialog

logger = logging.getLogger(__name__)


def handle_new_sample_image(owner, image_path: str):
    """Create a new session when a sample image is loaded or captured."""
    if owner.session_manager.is_session_active():
        reform_handler = getattr(owner, "_try_reform_active_session_for_new_image", None)
        if callable(reform_handler):
            try:
                if reform_handler(image_path):
                    if hasattr(owner, "_append_session_log"):
                        owner._append_session_log(
                            "Reused active session after image reform reset"
                        )
                    return
            except Exception as exc:
                logger.warning(
                    "Image reform handler failed; falling back to replacement flow: %s",
                    exc,
                    exc_info=True,
                )
        if not owner._handle_session_replacement():
            if hasattr(owner, "_append_session_log"):
                owner._append_session_log("New sample load cancelled")
            return

    default_distance = None
    if hasattr(owner, "_default_session_distance_cm"):
        try:
            default_distance = owner._default_session_distance_cm()
        except Exception:
            default_distance = None

    dialog = NewSessionDialog(
        owner.operator_manager,
        owner,
        default_distance=default_distance,
    )

    if dialog.exec_() != QDialog.Accepted:
        logger.info("User cancelled session creation")
        if hasattr(owner, "_append_session_log"):
            owner._append_session_log("Session creation cancelled by user")
        return

    params = dialog.get_parameters()

    session_folder = owner.get_session_folder()
    if not session_folder:
        session_folder = Path(image_path).parent
        logger.info("Using image directory as session folder: %s", session_folder)

    try:
        session_id, session_path = owner.session_manager.create_session(
            folder=session_folder,
            distance_cm=params["distance_cm"],
            technical_container_path=getattr(
                owner, "_active_technical_container_path", None
            ),
            sample_id=params["sample_id"],
            operator_id=params.get("operator_id"),
            **{
                k: v
                for k, v in params.items()
                if k not in ["sample_id", "operator_id", "distance_cm"]
            },
        )

        try:
            image_array = None
            if hasattr(owner, "_load_image_array_from_path"):
                image_array = owner._load_image_array_from_path(image_path)

            if image_array is not None:
                owner.session_manager.add_sample_image(
                    image_data=image_array,
                    image_index=1,
                    image_type="sample",
                )
                logger.info("Added sample image to session container")
                if hasattr(owner, "_append_session_log"):
                    owner._append_session_log("Sample image saved to session container")
            else:
                logger.warning("Failed to load image as array: %s", image_path)

        except Exception as exc:
            logger.warning(
                "Failed to add image to session container: %s",
                exc,
                exc_info=True,
            )

        QMessageBox.information(
            owner,
            "Session Created",
            f"Session created successfully!\n\n"
            f"Specimen ID: {params['sample_id']}\n"
            f"Study: {params.get('study_name', 'UNSPECIFIED')}\n"
            f"Project: {params.get('project_id', params.get('study_name', 'UNSPECIFIED'))}\n"
            f"Container: {session_path.name}\n\n"
            f"Sample image added to container.",
        )

        logger.info(
            "Created new session: %s for sample %s with image: %s",
            session_id,
            params["sample_id"],
            image_path,
        )
        if hasattr(owner, "_append_session_log"):
            owner._append_session_log(
                f"Created session {session_path.name} for sample {params['sample_id']}"
            )

        owner.update_session_status()

    except Exception as exc:
        QMessageBox.critical(
            owner,
            "Session Creation Failed",
            f"Failed to create session:\n\n{str(exc)}",
        )
        logger.error("Failed to create session: %s", exc, exc_info=True)
        if hasattr(owner, "_append_session_log"):
            owner._append_session_log(
                f"Session creation failed during new sample flow: {type(exc).__name__}"
            )


def load_session_container_from_path(owner, file_path: Path) -> bool:
    """Load a session container from an explicit path."""
    file_path = Path(file_path)
    if not file_path.exists():
        QMessageBox.critical(
            owner,
            "File Not Found",
            f"Container file not found:\n{file_path}",
        )
        return False

    if not owner._prepare_for_session_container_switch(file_path):
        return False

    if hasattr(owner, "_append_session_log"):
        owner._append_session_log(f"Opening existing session container: {file_path.name}")

    try:
        import h5py

        schema = get_schema(owner.config if hasattr(owner, "config") else None)
        container_manager = get_container_manager(
            owner.config if hasattr(owner, "config") else None
        )
        is_locked = container_manager.is_container_locked(file_path)

        with h5py.File(file_path, "r") as file_handle:
            sample_id = owner._decode_attr(
                file_handle.attrs.get("specimenId", file_handle.attrs.get(schema.ATTR_SAMPLE_ID, "Unknown"))
            )
            study_name = owner._decode_attr(
                file_handle.attrs.get(schema.ATTR_STUDY_NAME, "UNSPECIFIED")
            )
            session_id = owner._decode_attr(
                file_handle.attrs.get(schema.ATTR_SESSION_ID, "Unknown")
            )
            operator_id = owner._decode_attr(
                file_handle.attrs.get(schema.ATTR_OPERATOR_ID, "Unknown")
            )
            distance_cm = file_handle.attrs.get(schema.ATTR_DISTANCE_CM, None)
            beam_energy_kev = file_handle.attrs.get(schema.ATTR_BEAM_ENERGY_KEV, None)
            num_points = len(file_handle.get(schema.GROUP_POINTS, {}).keys())
            meas_group = file_handle.get(schema.GROUP_MEASUREMENTS, {})
            num_measurements = 0
            for point_group in meas_group.values():
                num_measurements += len(list(point_group.keys()))

        lock_status = "🔒 LOCKED (read-only)" if is_locked else "🔓 Unlocked (editable)"
        message = (
            f"Container Information:\n\n"
            f"Specimen ID: {sample_id}\n"
            f"Study: {study_name}\n"
            f"Session ID: {session_id}\n"
            f"Operator: {operator_id}\n"
            f"Status: {lock_status}\n\n"
            f"Data Summary:\n"
            f"  Points: {num_points}\n"
            f"  Measurements: {num_measurements}\n\n"
        )

        if distance_cm is not None:
            message += f"Distance: {distance_cm} cm\n"
        if beam_energy_kev is not None:
            message += f"Beam Energy: {beam_energy_kev} keV\n\n"

        QMessageBox.information(owner, "Session Container Opened", message, QMessageBox.Ok)

        session_info = owner.session_manager.open_existing_session(file_path)
        if not isinstance(session_info, dict):
            session_info = {}
        owner._sync_attenuation_controls_after_restore(session_info)

        logger.info(
            "Opened existing session container: sample_id=%s locked=%s path=%s",
            sample_id,
            is_locked,
            str(file_path),
        )
        if hasattr(owner, "_append_session_log"):
            mode = "read-only" if is_locked else "editable"
            owner._append_session_log(
                f"Opened session container {file_path.name} ({mode})"
            )

        owner._handle_incomplete_measurements_after_restore(file_path)
        owner._restore_session_workspace_from_container(file_path)

        if hasattr(owner, "_populate_aux_table_from_h5"):
            try:
                owner._populate_aux_table_from_h5(str(file_path), set_active=False)
            except Exception as tech_restore_error:
                logger.warning(
                    "Failed to restore technical table from session: %s",
                    tech_restore_error,
                )
        owner.update_session_status()

        QMessageBox.information(
            owner,
            "Container Ready",
            "Session container opened successfully!\n\n"
            "You can now analyze the data in DIFRA.\n\n"
            f"Note: {'Read-only mode (locked)' if is_locked else 'Editable mode'}",
        )
        return True
    except Exception as exc:
        QMessageBox.critical(
            owner,
            "Failed to Open Container",
            f"Failed to open session container:\n\n{str(exc)}",
        )
        logger.error("Failed to open session container: %s", exc, exc_info=True)
        if hasattr(owner, "_append_session_log"):
            owner._append_session_log(
                f"Failed to open session container: {type(exc).__name__}"
            )
        return False
