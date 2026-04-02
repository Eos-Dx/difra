"""Procedural session flow helpers extracted from SessionFlowMixin."""

import logging
from pathlib import Path

from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QFileDialog, QMessageBox

from difra.gui.container_api import get_container_manager, get_schema

logger = logging.getLogger(__name__)


def prompt_and_attach_sample_image(owner) -> str | None:
    """Offer loading an existing sample photo after a session is created."""
    reply = QMessageBox.question(
        owner,
        "Load Sample Image",
        "Session container created.\n\n"
        "Would you like to load a sample image from disk now?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )
    if reply != QMessageBox.Yes:
        return None

    default_folder = ""
    if hasattr(owner, "config") and isinstance(owner.config, dict):
        default_folder = str(
            owner.config.get("default_image_folder")
            or owner.config.get("default_folder")
            or ""
        ).strip()
    file_path, _ = QFileDialog.getOpenFileName(
        owner,
        "Load Sample Image For Session",
        default_folder or str(Path.home()),
        "Image Files (*.png *.jpg *.jpeg *.tif *.tiff *.bmp);;All Files (*)",
    )
    if not file_path:
        return None

    image_array = None
    if hasattr(owner, "_load_image_array_from_path"):
        image_array = owner._load_image_array_from_path(file_path)
    if image_array is None:
        raise RuntimeError(f"Failed to load image data from {file_path}")

    owner.session_manager.add_sample_image(
        image_data=image_array,
        image_index=1,
        image_type="sample",
    )

    pixmap = QPixmap(file_path)
    if (
        hasattr(owner, "image_view")
        and owner.image_view is not None
        and not pixmap.isNull()
    ):
        owner.image_view.set_image(pixmap, image_path=file_path)
    reset_rotation = getattr(owner, "_reset_sample_photo_rotation_state", None)
    if callable(reset_rotation):
        reset_rotation()

    clear_shapes = getattr(owner, "delete_all_shapes_from_table", None)
    if callable(clear_shapes):
        clear_shapes()
    clear_points = getattr(owner, "delete_all_points", None)
    if callable(clear_points):
        clear_points()

    if hasattr(owner, "_append_session_log"):
        owner._append_session_log(
            f"Sample image loaded into session from disk: {Path(file_path).name}"
        )
    return file_path


def handle_new_sample_image(owner, image_path: str):
    """Attach a newly loaded/captured sample image to the active manual session."""
    if not owner.session_manager.is_session_active():
        QMessageBox.information(
            owner,
            "Create Session First",
            "Create the session container manually before loading or capturing a sample photo.",
        )
        return

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
            raise RuntimeError(f"Failed to load image data from {image_path}")
    except Exception as exc:
        QMessageBox.warning(
            owner,
            "Sample Image Not Saved",
            f"Failed to attach sample image to the active session container.\n\n{exc}",
        )
        logger.warning(
            "Failed to add image to session container: %s",
            exc,
            exc_info=True,
        )
        if hasattr(owner, "_append_session_log"):
            owner._append_session_log(
                f"Sample image attach failed: {type(exc).__name__}"
            )
        return

    if hasattr(owner, "_append_session_log"):
        owner._append_session_log(
            f"Attached sample image to active session: {Path(image_path).name}"
        )
    reset_rotation = getattr(owner, "_reset_sample_photo_rotation_state", None)
    if callable(reset_rotation):
        reset_rotation()
    if hasattr(owner, "update_session_status"):
        owner.update_session_status()


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
                enable_measurement_controls = getattr(
                    owner, "enable_measurement_controls", None
                )
                if callable(enable_measurement_controls):
                    enable_measurement_controls(
                        bool(getattr(owner, "hardware_initialized", False))
                    )
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
