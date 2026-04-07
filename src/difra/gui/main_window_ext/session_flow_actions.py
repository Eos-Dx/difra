"""Procedural session flow helpers extracted from SessionFlowMixin."""

import logging
from pathlib import Path

from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from difra.gui.container_api import get_container_manager, get_schema
from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.utils.container_validation import validate_container

logger = logging.getLogger(__name__)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _current_session_specimen_id(owner) -> str:
    session_manager = getattr(owner, "session_manager", None)
    session_path = getattr(session_manager, "session_path", None)
    if session_path:
        try:
            import h5py

            schema = get_schema(owner.config if hasattr(owner, "config") else None)
            with h5py.File(session_path, "r") as h5f:
                specimen = _as_text(
                    h5f.attrs.get(
                        "specimenId",
                        h5f.attrs.get(getattr(schema, "ATTR_SAMPLE_ID", "sample_id")),
                    )
                ).strip()
                if specimen:
                    return specimen
        except Exception:
            logger.debug("Failed to read specimenId from active session", exc_info=True)

    return _as_text(getattr(session_manager, "sample_id", "")).strip()


def _find_archived_session_candidates(owner, specimen_id: str) -> list[dict]:
    specimen_text = _as_text(specimen_id).strip()
    if not specimen_text:
        return []

    session_manager = getattr(owner, "session_manager", None)
    session_path = getattr(session_manager, "session_path", None)
    archive_root = SessionLifecycleService.resolve_archive_folder(
        config=owner.config if hasattr(owner, "config") else None,
        session_path=Path(session_path) if session_path else None,
    )
    archive_root = Path(archive_root)
    if not archive_root.exists():
        return []
    container_manager = get_container_manager(
        owner.config if hasattr(owner, "config") else None
    )

    active_resolved = None
    if session_path:
        try:
            active_resolved = Path(session_path).resolve()
        except Exception:
            active_resolved = Path(session_path)

    schema = get_schema(owner.config if hasattr(owner, "config") else None)
    candidates = []

    try:
        import h5py

        for container_path in sorted(archive_root.rglob("*.nxs.h5")):
            try:
                resolved = container_path.resolve()
            except Exception:
                resolved = container_path
            if active_resolved is not None and resolved == active_resolved:
                continue

            try:
                if not bool(container_manager.is_container_locked(container_path)):
                    continue
                report = validate_container(container_path, container_kind="session")
                if not report.is_valid:
                    continue

                with h5py.File(container_path, "r") as h5f:
                    completion_status = _as_text(
                        h5f.attrs.get("session_completion_status")
                    ).strip().lower()
                    if completion_status == "not_complete":
                        continue

                    candidate_specimen = _as_text(
                        h5f.attrs.get(
                            "specimenId",
                            h5f.attrs.get(getattr(schema, "ATTR_SAMPLE_ID", "sample_id")),
                        )
                    ).strip()
                    if candidate_specimen != specimen_text:
                        continue

                    distance_cm = h5f.attrs.get(getattr(schema, "ATTR_DISTANCE_CM", "distance_cm"))
                    acquisition_date = _as_text(h5f.attrs.get("acquisition_date")).strip()
                    creation_ts = _as_text(h5f.attrs.get("creation_timestamp")).strip()
                    candidates.append(
                        {
                            "path": Path(container_path),
                            "specimen_id": candidate_specimen,
                            "distance_cm": _as_text(distance_cm).strip(),
                            "acquisition_date": acquisition_date or creation_ts,
                            "session_name": Path(container_path).name,
                        }
                    )
            except Exception:
                logger.debug(
                    "Skipping archived session candidate due to metadata read failure: %s",
                    container_path,
                    exc_info=True,
                )
    except Exception:
        logger.debug("Failed to scan archived sessions under %s", archive_root, exc_info=True)
        return []

    return sorted(
        candidates,
        key=lambda row: (
            str(row.get("acquisition_date") or ""),
            str(row.get("session_name") or ""),
        ),
        reverse=True,
    )


def _pick_archived_session_candidate(owner, candidates: list[dict]) -> Path | None:
    if not candidates:
        return None

    labels = []
    path_by_label = {}
    for candidate in candidates:
        label = (
            f"{candidate['session_name']} | "
            f"specimenID={candidate['specimen_id']} | "
            f"distance={candidate.get('distance_cm') or '?'} cm | "
            f"date={candidate.get('acquisition_date') or '?'}"
        )
        labels.append(label)
        path_by_label[label] = Path(candidate["path"])

    selected_label, accepted = QInputDialog.getItem(
        owner,
        "Select Previous Session",
        "Matching archived session containers:",
        labels,
        0,
        False,
    )
    if not accepted or not selected_label:
        return None
    return path_by_label.get(str(selected_label))


def clear_session_workspace(owner) -> None:
    """Clear zones, points, and cached workspace state before loading a new session image."""
    clear_shapes = getattr(owner, "delete_all_shapes_from_table", None)
    if callable(clear_shapes):
        try:
            clear_shapes(force=True)
        except TypeError:
            try:
                clear_shapes()
            except Exception:
                logger.warning("Failed to clear workspace shapes", exc_info=True)
        except Exception:
            logger.warning("Failed to clear workspace shapes", exc_info=True)

    clear_points = getattr(owner, "delete_all_points", None)
    if callable(clear_points):
        try:
            clear_points()
        except Exception:
            logger.warning("Failed to clear workspace points", exc_info=True)

    state = getattr(owner, "state", None)
    if isinstance(state, dict):
        state["shapes"] = []
        state["zone_points"] = []
        state["measurement_points"] = []
        state["skipped_points"] = []

    state_measurements = getattr(owner, "state_measurements", None)
    if isinstance(state_measurements, dict):
        state_measurements["measurement_points"] = []
        state_measurements["skipped_points"] = []

    measurement_widgets = getattr(owner, "measurement_widgets", None)
    if measurement_widgets is not None and not isinstance(measurement_widgets, dict):
        owner.measurement_widgets = {}

    update_shape_table = getattr(owner, "update_shape_table", None)
    if callable(update_shape_table):
        try:
            update_shape_table()
        except Exception:
            logger.debug("Failed to refresh shape table after workspace clear", exc_info=True)
    update_points_table = getattr(owner, "update_points_table", None)
    if callable(update_points_table):
        try:
            update_points_table()
        except Exception:
            logger.debug("Failed to refresh points table after workspace clear", exc_info=True)


def _import_workspace_from_previous_session(owner) -> str | None:
    specimen_id = _current_session_specimen_id(owner)
    candidates = _find_archived_session_candidates(owner, specimen_id)
    if not candidates:
        QMessageBox.information(
            owner,
            "No Matching Archived Sessions",
            "No archived session containers were found for this specimen ID.\n\n"
            f"Specimen ID: {specimen_id or 'unknown'}",
        )
        return None

    selected_path = _pick_archived_session_candidate(owner, candidates)
    if selected_path is None:
        return None

    importer = getattr(owner, "import_workspace_from_session_path", None)
    if not callable(importer):
        raise RuntimeError("Workspace import from session container is not available.")

    imported = importer(Path(selected_path))
    if not imported:
        return None
    return f"Session: {Path(selected_path).name}"


def _load_sample_image_from_disk(owner) -> str | None:
    """Load sample image from disk into the active session and workspace."""
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
    prompt_rotation = getattr(owner, "_maybe_prompt_sample_photo_rotation", None)
    if callable(prompt_rotation):
        prompt_rotation()

    clear_session_workspace(owner)

    if hasattr(owner, "_append_session_log"):
        owner._append_session_log(
            f"Sample image loaded into session from disk: {Path(file_path).name}"
        )
    return f"Disk: {Path(file_path).name}"


def prompt_and_attach_sample_image(owner) -> str | None:
    """Offer loading an existing sample photo after a session is created."""
    selected_source, accepted = QInputDialog.getItem(
        owner,
        "Load Sample Image",
        "Session container created.\n\n"
        "Choose whether to load the specimen workspace from a previous session container:",
        [
            "Load image and points from previous session container",
            "Skip for now",
        ],
        0,
        False,
    )
    if not accepted or not selected_source:
        return None
    if selected_source == "Skip for now":
        return None
    if selected_source == "Load image and points from previous session container":
        return _import_workspace_from_previous_session(owner)
    return None


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
    prompt_rotation = getattr(owner, "_maybe_prompt_sample_photo_rotation", None)
    if callable(prompt_rotation):
        prompt_rotation()
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

        previous_prompt_suppression = bool(
            getattr(owner, "_suppress_sample_photo_rotation_prompt", False)
        )
        owner._suppress_sample_photo_rotation_prompt = True
        try:
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
        finally:
            owner._suppress_sample_photo_rotation_prompt = previous_prompt_suppression

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
