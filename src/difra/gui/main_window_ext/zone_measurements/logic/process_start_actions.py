"""Procedural measurement-start helpers extracted from ProcessStartMixin."""

import time
import uuid
from copy import copy
from pathlib import Path


def _pm():
    from difra.gui.main_window_ext.zone_measurements.logic import process_mixin as pm

    return pm


def resolve_session_point_plan(owner, measurement_points):
    """Map current GUI points onto an existing session container when resuming."""
    pm = _pm()

    default_plan = {
        "mode": "new",
        "measurement_points": list(measurement_points),
        "session_point_indices": [idx for idx in range(1, len(measurement_points) + 1)],
        "measured_count": 0,
    }

    session_manager = getattr(owner, "session_manager", None)
    if session_manager is None:
        return default_plan
    if not hasattr(session_manager, "is_session_active") or not session_manager.is_session_active():
        return default_plan
    schema = getattr(session_manager, "schema", None)
    if schema is None:
        return default_plan

    try:
        session_points = owner._load_active_session_points_metadata()

        if not session_points:
            return default_plan

        has_session_uids = any(
            str(sp.get("point_uid") or "").strip() for sp in session_points
        )
        has_session_coords = any(sp.get("physical_xy") is not None for sp in session_points)
        if (
            len(session_points) != len(measurement_points)
            and not has_session_uids
            and not has_session_coords
        ):
            pm.logger.warning(
                "Session/GUI point count mismatch without UID/coordinate metadata; rebuilding session points from current grid",
                session_points=int(len(session_points)),
                gui_points=int(len(measurement_points)),
            )
            return default_plan

        measured_status = owner._as_text(schema.POINT_STATUS_MEASURED).strip().lower()
        pending_indices = [
            int(sp["point_index"])
            for sp in session_points
            if sp["status"] != measured_status
        ]
        measured_count = len(session_points) - len(pending_indices)

        if measured_count == 0:
            return default_plan

        if not pending_indices:
            return {
                **default_plan,
                "mode": "complete",
                "measurement_points": [],
                "session_point_indices": [],
                "measured_count": measured_count,
            }

        measurement_uid_to_idx = {}
        for idx, mp in enumerate(measurement_points):
            uid = str(mp.get("unique_id") or "").strip()
            if uid and uid not in measurement_uid_to_idx:
                measurement_uid_to_idx[uid] = idx

        session_to_measure_idx = {}
        used_indices = set()

        for sp in session_points:
            sp_idx = int(sp["point_index"])
            sp_uid = str(sp.get("point_uid") or "").strip()
            if not sp_uid:
                continue
            mp_idx = measurement_uid_to_idx.get(sp_uid)
            if mp_idx is None or mp_idx in used_indices:
                continue
            session_to_measure_idx[sp_idx] = mp_idx
            used_indices.add(mp_idx)

        for sp in session_points:
            sp_idx = int(sp["point_index"])
            if sp_idx in session_to_measure_idx:
                continue
            sp_xy = sp.get("physical_xy")
            if sp_xy is None:
                continue

            best_idx = None
            best_d2 = float("inf")
            for idx, mp in enumerate(measurement_points):
                if idx in used_indices:
                    continue
                d2 = owner._point_distance_sq(sp_xy, (mp.get("x"), mp.get("y")))
                if d2 < best_d2:
                    best_d2 = d2
                    best_idx = idx

            if best_idx is not None:
                session_to_measure_idx[sp_idx] = best_idx
                used_indices.add(best_idx)

        for sp in session_points:
            sp_idx = int(sp["point_index"])
            if sp_idx in session_to_measure_idx:
                continue
            pos = sp_idx - 1
            if 0 <= pos < len(measurement_points) and pos not in used_indices:
                session_to_measure_idx[sp_idx] = pos
                used_indices.add(pos)

        resumed_points = []
        resumed_session_indices = []
        for session_point_index in pending_indices:
            mp_idx = session_to_measure_idx.get(int(session_point_index))
            if mp_idx is None:
                continue
            resumed_points.append(measurement_points[mp_idx])
            resumed_session_indices.append(int(session_point_index))

        if len(resumed_points) != len(pending_indices):
            pm.logger.warning(
                "Resume mapping could not match all pending points; using mapped subset",
                pending_points=int(len(pending_indices)),
                mapped_points=int(len(resumed_points)),
                session_points=int(len(session_points)),
                gui_points=int(len(measurement_points)),
            )
            return {
                **default_plan,
                "mode": "resume_mapping_incomplete",
                "measurement_points": resumed_points,
                "session_point_indices": resumed_session_indices,
                "measured_count": measured_count,
                "pending_count": int(len(pending_indices)),
                "mapped_count": int(len(resumed_points)),
            }

        if not resumed_points:
            pm.logger.warning(
                "No pending session points could be mapped; rebuilding from current grid",
                pending_points=int(len(pending_indices)),
            )
            return default_plan

        return {
            **default_plan,
            "mode": "resume",
            "measurement_points": resumed_points,
            "session_point_indices": resumed_session_indices,
            "measured_count": measured_count,
        }
    except Exception as exc:
        pm.logger.warning(
            "Failed to inspect existing session points for resume; starting from full point set",
            error=str(exc),
        )
        return default_plan


def start_measurements(owner):
    """Main measurement-start orchestration entrypoint."""
    pm = _pm()

    owner.auto_save_state()
    if hasattr(owner, "_enforce_measurement_output_folder_lock"):
        owner._enforce_measurement_output_folder_lock(show_message=False)
    if hasattr(owner, "_current_measurement_output_folder"):
        owner.measurement_folder = Path(owner._current_measurement_output_folder())
    else:
        owner.measurement_folder = Path(owner.folderLineEdit.text().strip())
    if hasattr(owner, "folderLineEdit") and owner.folderLineEdit is not None:
        owner.folderLineEdit.setText(str(owner.measurement_folder))
    owner.state_path_measurements = (
        owner.measurement_folder / f"{owner.fileNameLineEdit.text()}_state.json"
    )
    pm.logger.info(
        "Measurement start requested",
        measurement_folder=str(owner.measurement_folder),
        state_file=str(owner.state_path_measurements),
    )

    if hasattr(owner, "refresh_sidecar_status"):
        if not owner.refresh_sidecar_status(show_message=True):
            owner._append_capture_log(
                "Start cancelled: A2K sidecar heartbeat unavailable"
            )
            return

    if not owner.measurement_folder.exists():
        pm.QMessageBox.warning(
            owner,
            "Folder Error",
            "Selected folder does not exist. Please select the correct folder.",
        )
        owner._append_capture_log("Start failed: save folder does not exist")
        return

    if not owner._ensure_writable_session_for_measurement():
        owner._append_session_log("Start cancelled: no writable session container")
        return

    if not owner._confirm_poni_settings_before_measurement():
        owner._append_capture_log("Start cancelled: PONI confirmation rejected")
        return

    try:
        from .preflight_dialog import PreflightDialog

        dialog = PreflightDialog(
            owner,
            session_manager=getattr(owner, "session_manager", None),
        )
        if dialog.exec_() != dialog.Accepted:
            owner._append_capture_log("Start cancelled: preflight checklist not confirmed")
            return
    except Exception as exc:
        pm.logger.warning("Preflight dialog failed; proceeding without it", error=str(exc))

    group_hash = getattr(owner, "calibration_group_hash", None)
    if not group_hash:
        try:
            group_hash = uuid.uuid4().hex[:16]
        except Exception:
            group_hash = None
        setattr(owner, "calibration_group_hash", group_hash)
    if group_hash:
        try:
            if isinstance(getattr(owner, "state", None), dict):
                owner.state["CALIBRATION_GROUP_HASH"] = group_hash
        except Exception:
            pass

    try:
        owner.state_measurements = copy(owner.state)
    except Exception as exc:
        pm.logger.error("Error copying state for measurements", error=str(exc))
        pm.QMessageBox.warning(owner, "No state", "Save it.")
        return

    try:
        from difra.hardware.auxiliary import encode_image_to_base64

        owner.state_measurements["image_base64"] = encode_image_to_base64(
            owner.image_view.current_image_path
        )
        owner._dump_state_measurements()
    except Exception as exc:
        pm.logger.error("Error saving state with encoded image", error=str(exc))

    if owner.pointsTable.rowCount() == 0:
        pm.logger.warning("No points available for measurement")
        owner._append_capture_log("Start cancelled: no measurement points")
        return

    owner.start_btn.setEnabled(False)
    owner.pause_btn.setEnabled(True)
    owner.stop_btn.setEnabled(True)
    if hasattr(owner, "skip_btn") and owner.skip_btn is not None:
        owner.skip_btn.setEnabled(True)
    clear_previews = getattr(owner, "clear_detector_profile_previews", None)
    if callable(clear_previews):
        clear_previews()
    owner.stopped = False
    owner.paused = False
    owner._session_point_indices = []

    generated_points = owner.image_view.points_dict["generated"]["points"]
    user_points = owner.image_view.points_dict["user"]["points"]
    session_points = owner._load_active_session_points_metadata()
    session_xy_by_uid = {
        str(sp.get("point_uid") or "").strip(): sp.get("physical_xy")
        for sp in session_points
        if str(sp.get("point_uid") or "").strip() and sp.get("physical_xy") is not None
    }
    session_xy_by_index = {
        int(sp["point_index"]): sp.get("physical_xy")
        for sp in session_points
        if sp.get("physical_xy") is not None
    }
    all_points = []
    for i, item in enumerate(generated_points):
        center = item.sceneBoundingRect().center()
        uid = owner._point_item_uid(item, i + 1)
        session_xy = session_xy_by_uid.get(str(uid).strip()) or session_xy_by_index.get(i + 1)
        if session_xy is not None:
            x_mm, y_mm = session_xy
        else:
            x_mm = (
                owner.real_x_pos_mm.value()
                - (center.x() - owner.include_center[0]) / owner.pixel_to_mm_ratio
            )
            y_mm = (
                owner.real_y_pos_mm.value()
                - (center.y() - owner.include_center[1]) / owner.pixel_to_mm_ratio
            )
        all_points.append((i, x_mm, y_mm, uid))
    offset = len(generated_points)
    for j, item in enumerate(user_points):
        center = item.sceneBoundingRect().center()
        session_point_index = offset + j + 1
        uid = owner._point_item_uid(item, session_point_index)
        session_xy = session_xy_by_uid.get(str(uid).strip()) or session_xy_by_index.get(
            session_point_index
        )
        if session_xy is not None:
            x_mm, y_mm = session_xy
        else:
            x_mm = (
                owner.real_x_pos_mm.value()
                - (center.x() - owner.include_center[0]) / owner.pixel_to_mm_ratio
            )
            y_mm = (
                owner.real_y_pos_mm.value()
                - (center.y() - owner.include_center[1]) / owner.pixel_to_mm_ratio
            )
        all_points.append((offset + j, x_mm, y_mm, uid))
    all_points_sorted = sorted(all_points, key=lambda tup: (tup[1], tup[2]))
    owner.sorted_indices = [tup[0] for tup in all_points_sorted]
    owner.total_points = len(owner.sorted_indices)
    owner.current_measurement_sorted_index = 0

    owner.progressBar.setMaximum(owner.total_points)
    owner.progressBar.setValue(0)
    owner.integration_time = owner.integrationSpinBox.value()
    owner.initial_estimate = owner.total_points * owner.integration_time
    owner.measurementStartTime = time.time()
    owner.timeRemainingLabel.setText(
        f"Estimated time: {owner.initial_estimate:.0f} sec"
    )
    pm.logger.info(
        "Starting measurements in sorted order",
        total_points=owner.total_points,
        integration_time=owner.integration_time,
    )
    owner._append_capture_log(
        f"Start: {owner.total_points} points, T={owner.integration_time:.2f}s"
    )

    try:
        if hasattr(owner, "_get_stage_limits"):
            limits = owner._get_stage_limits()
        else:
            limits = (
                owner.stage_controller.get_limits()
                if hasattr(owner, "stage_controller")
                else None
            )
    except Exception:
        limits = None
    if not limits:
        limits = {"x": (-14.0, 14.0), "y": (-14.0, 14.0)}
    x_min, x_max = limits["x"]
    y_min, y_max = limits["y"]

    measurement_points = []
    skipped_points = []
    valid_idx = 0

    for _orig_idx, (pt_idx, x_mm, y_mm, point_uid) in enumerate(all_points_sorted):
        if (x_min <= x_mm <= x_max) and (y_min <= y_mm <= y_max):
            unique_id = (
                str(point_uid).strip()
                if point_uid
                else owner._new_measurement_point_uid(valid_idx + 1)
            )
            measurement_points.append(
                {
                    "unique_id": unique_id,
                    "index": valid_idx,
                    "point_index": pt_idx,
                    "x": x_mm,
                    "y": y_mm,
                }
            )
            valid_idx += 1
        else:
            skipped_points.append((pt_idx, x_mm, y_mm))
            pm.logger.warning(
                f"Skipping measurement point {pt_idx} at ({x_mm:.3f}, {y_mm:.3f}) mm - "
                f"outside limits X[{x_min:.1f},{x_max:.1f}] Y[{y_min:.1f},{y_max:.1f}] mm"
            )

    owner.sorted_indices = [mp["point_index"] for mp in measurement_points]
    owner.total_points = len(owner.sorted_indices)
    owner.progressBar.setMaximum(owner.total_points)
    owner.initial_estimate = owner.total_points * owner.integration_time
    owner.timeRemainingLabel.setText(
        f"Estimated time: {owner.initial_estimate:.0f} sec"
    )

    if skipped_points:
        pm.logger.info(
            f"Filtered measurement points: {len(measurement_points)} valid, "
            f"{len(skipped_points)} skipped due to axis limits"
        )
        owner._append_capture_log(
            f"Filtered points: {len(measurement_points)} valid, {len(skipped_points)} skipped"
        )

    if not measurement_points:
        pm.logger.error("No valid measurement points within axis limits")
        pm.QMessageBox.warning(
            owner,
            "No Valid Points",
            f"All measurement points exceed the axis limits of X[{x_min:.1f},{x_max:.1f}] and Y[{y_min:.1f},{y_max:.1f}] mm. "
            "Please adjust your measurement grid.",
        )
        owner._set_measurement_controls_idle()
        owner._append_capture_log("Start failed: all points are outside stage limits")
        return

    full_measurement_points = list(measurement_points)
    session_plan = owner._resolve_session_point_plan(measurement_points)
    should_seed_session_points = True
    if session_plan.get("mode") == "resume_mapping_incomplete":
        pending_count = int(session_plan.get("pending_count", 0))
        mapped_count = int(session_plan.get("mapped_count", 0))
        pm.QMessageBox.warning(
            owner,
            "Resume Mapping Incomplete",
            (
                "Restored session contains pending points that could not be fully mapped "
                "to current GUI points.\n\n"
                f"Pending points in session: {pending_count}\n"
                f"Mapped GUI points: {mapped_count}\n\n"
                "Measurement start was cancelled to avoid silently skipping points.\n"
                "Please reload workspace points from container and try again."
            ),
        )
        owner._append_capture_log(
            "Start cancelled: resume mapping incomplete "
            f"({mapped_count}/{pending_count} points mapped)"
        )
        owner._append_session_log("Resume cancelled due to incomplete point mapping")
        owner._set_measurement_controls_idle()
        return
    if session_plan.get("mode") == "resume":
        measured_count = int(session_plan.get("measured_count", 0))
        pending_count = int(len(session_plan.get("measurement_points", []) or []))
        choice = owner._choose_resume_or_remeasure(
            measured_count=measured_count,
            pending_count=pending_count,
        )
        if choice == "resume":
            measurement_points = list(session_plan.get("measurement_points", []) or [])
            should_seed_session_points = False
            owner._session_point_indices = list(
                session_plan.get("session_point_indices", []) or []
            )
            pm.logger.info(
                "Resuming restored session with pending points only",
                measured_points=measured_count,
                pending_points=int(len(measurement_points)),
            )
            owner._append_session_log(
                f"Resume mode: {measured_count} point(s) already measured, "
                f"{len(measurement_points)} pending"
            )
        else:
            measurement_points = full_measurement_points
            should_seed_session_points = False
            owner._session_point_indices = [
                idx for idx in range(1, len(measurement_points) + 1)
            ]
            pm.logger.info(
                "User selected full re-measurement for restored session",
                total_points=int(len(measurement_points)),
            )
            owner._append_session_log(
                "Re-measure mode: all points will be captured again"
            )

        owner.sorted_indices = [mp["point_index"] for mp in measurement_points]
        owner.total_points = len(owner.sorted_indices)
        owner.progressBar.setMaximum(owner.total_points)
        owner.initial_estimate = owner.total_points * owner.integration_time
        owner.timeRemainingLabel.setText(
            f"Estimated time: {owner.initial_estimate:.0f} sec"
        )
    elif session_plan.get("mode") == "complete":
        if not owner._confirm_remeasure_completed_session(
            total_points=len(full_measurement_points)
        ):
            pm.logger.info("Restore start skipped: all session points already measured")
            owner._append_capture_log("Start skipped: restored session already complete")
            owner._set_measurement_controls_idle()
            return

        measurement_points = full_measurement_points
        should_seed_session_points = False
        owner._session_point_indices = [
            idx for idx in range(1, len(measurement_points) + 1)
        ]
        owner.sorted_indices = [mp["point_index"] for mp in measurement_points]
        owner.total_points = len(owner.sorted_indices)
        owner.progressBar.setMaximum(owner.total_points)
        owner.initial_estimate = owner.total_points * owner.integration_time
        owner.timeRemainingLabel.setText(
            f"Estimated time: {owner.initial_estimate:.0f} sec"
        )
        owner._append_session_log(
            "Re-measure mode: completed session will be measured again"
        )
    else:
        owner._session_point_indices = list(
            session_plan.get("session_point_indices", []) or []
        )

    if session_plan.get("mode") == "new":
        existing_points_count = owner._existing_session_point_count()
        if existing_points_count > 0:
            owner._session_point_indices = []

    if not owner._session_point_indices:
        mapped_from_original = []
        existing_points_count = owner._existing_session_point_count()
        if existing_points_count > 0:
            for i, mp in enumerate(measurement_points):
                try:
                    original_idx = int(mp.get("point_index", i))
                except Exception:
                    continue
                session_point_index = original_idx + 1
                if 1 <= session_point_index <= existing_points_count:
                    mapped_from_original.append(session_point_index)
            if len(mapped_from_original) == len(measurement_points):
                owner._session_point_indices = mapped_from_original
                pm.logger.info(
                    "Mapped measurement order to existing session points",
                    mapped_points=int(len(owner._session_point_indices)),
                    session_points=int(existing_points_count),
                )

        if not owner._session_point_indices:
            owner._session_point_indices = [
                idx for idx in range(1, len(measurement_points) + 1)
            ]

    if not measurement_points:
        pm.logger.info("No pending measurement points after resume filtering")
        owner._append_capture_log("Start skipped: no pending points after filtering")
        owner._set_measurement_controls_idle()
        return

    owner._reuse_existing_i0_from_session = False
    try:
        if hasattr(owner, "attenuationCheckBox") and owner.attenuationCheckBox.isChecked():
            if owner._session_has_i0_measurement():
                owner._reuse_existing_i0_from_session = True
                pm.logger.info(
                    "Skipping I0 background capture: session already contains I0 measurement",
                    i0_counter=getattr(
                        getattr(owner, "session_manager", None),
                        "i0_counter",
                        None,
                    ),
                )
                owner._append_capture_log(
                    "I0 already recorded in session; reusing existing I0"
                )
            else:
                owner._capture_attenuation_background()
    except Exception as exc:
        pm.logger.warning(
            "Failed to capture attenuation background; will continue without it",
            error=str(exc),
        )

    owner.state["measurement_points"] = measurement_points
    owner.state["skipped_points"] = [
        {
            "point_index": pt_idx,
            "x": x_mm,
            "y": y_mm,
            "reason": "axis_limit_exceeded",
        }
        for pt_idx, x_mm, y_mm in skipped_points
    ]

    owner.state_measurements["measurement_points"] = measurement_points
    owner.state_measurements["skipped_points"] = owner.state["skipped_points"]
    group_hash = getattr(owner, "calibration_group_hash", None)
    if group_hash:
        owner.state_measurements["CALIBRATION_GROUP_HASH"] = group_hash
    owner.auto_save_state()

    if hasattr(owner, "session_manager") and owner.session_manager.is_session_active():
        try:
            pm.logger.info("=== SESSION CONTAINER POPULATION ===")
            existing_points_count = owner._existing_session_point_count()
            if should_seed_session_points and existing_points_count > 0:
                should_seed_session_points = False
                if not owner._session_point_indices:
                    mapped_indices = []
                    for idx, mp in enumerate(measurement_points):
                        try:
                            mapped_indices.append(int(mp.get("point_index", idx)) + 1)
                        except Exception:
                            mapped_indices.append(int(idx) + 1)
                    owner._session_point_indices = mapped_indices
                pm.logger.info(
                    "Session already contains points; skipping point regeneration",
                    existing_points=int(existing_points_count),
                    planned_points=int(len(measurement_points)),
                )
                owner._append_session_log(
                    f"Session points already exist ({existing_points_count}); reusing them"
                )
            if should_seed_session_points:
                points_for_session = []
                for pt in measurement_points:
                    pt_idx = pt["point_index"]
                    generated = owner.image_view.points_dict["generated"]["points"]
                    user = owner.image_view.points_dict["user"]["points"]

                    if pt_idx < len(generated):
                        point_item = generated[pt_idx]
                    else:
                        user_idx = pt_idx - len(generated)
                        point_item = user[user_idx]

                    center = point_item.sceneBoundingRect().center()
                    points_for_session.append(
                        {
                            "pixel_coordinates": [float(center.x()), float(center.y())],
                            "physical_coordinates_mm": [pt["x"], pt["y"]],
                            "point_uid": str(pt.get("unique_id") or ""),
                        }
                    )

                pm.logger.info(
                    f"Adding {len(points_for_session)} points to session container..."
                )
                owner._append_session_log(
                    f"Initializing session container: {len(points_for_session)} points"
                )
                owner.session_manager.add_points(points_for_session)
                pm.logger.info(
                    f"✓ Added {len(points_for_session)} points to session container"
                )
                owner._append_session_log(
                    f"Session points written: {len(points_for_session)}"
                )
            else:
                pm.logger.info(
                    "Reusing existing points from session; skipping point regeneration"
                )
                owner._append_session_log(
                    "Session points reused from existing container"
                )

            if hasattr(owner, "_add_zones_to_session"):
                pm.logger.info("Adding zones to session container...")
                num_shapes = len(owner.state.get("shapes", []))
                pm.logger.info(f"Found {num_shapes} shapes in state")
                owner._add_zones_to_session()
                pm.logger.info("✓ Zones processing complete")
                owner._append_session_log(f"Session zones synced: {num_shapes}")
            else:
                pm.logger.warning("⚠ _add_zones_to_session method not found")

            if hasattr(owner, "_add_mapping_to_session"):
                pm.logger.info("Adding mapping to session container...")
                owner._add_mapping_to_session()
                pm.logger.info("✓ Mapping added")
                owner._append_session_log("Session image mapping updated")
            else:
                pm.logger.warning("⚠ _add_mapping_to_session method not found")

            lock_shapes = getattr(owner, "_mark_current_shapes_as_measurement_locked", None)
            if callable(lock_shapes):
                lock_shapes()

            pm.logger.info("=== SESSION CONTAINER INITIALIZED ===")
            owner._append_session_log("Session container initialization complete")
        except Exception as exc:
            pm.logger.error(
                f"Failed to add points to session container: {exc}",
                exc_info=True,
            )
            owner._append_session_log(
                f"Session initialization failed: {type(exc).__name__}"
            )

    owner.measure_next_point()
