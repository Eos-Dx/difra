"""Measurement point planning helpers for ProcessStartActions."""

import time


def _pm():
    from difra.gui.main_window_ext.zone_measurements.logic import process_start_actions

    return process_start_actions._pm()


def _collect_sorted_points(owner):
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
        session_xy = session_xy_by_uid.get(str(uid).strip()) or session_xy_by_index.get(
            i + 1
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
    return sorted(all_points, key=lambda tup: (tup[1], tup[2]))


def _stage_limits(owner):
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
    return limits


def _set_progress_estimate(owner, point_count: int):
    owner.total_points = int(point_count)
    owner.progressBar.setMaximum(owner.total_points)
    owner.initial_estimate = owner.total_points * owner.integration_time
    owner.timeRemainingLabel.setText(
        f"Estimated time: {owner.initial_estimate:.0f} sec"
    )


def _filter_stage_points(owner, all_points_sorted, limits):
    pm = _pm()
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
    return measurement_points, skipped_points


def prepare_measurement_point_plan(owner):
    pm = _pm()
    all_points_sorted = _collect_sorted_points(owner)
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

    limits = _stage_limits(owner)
    measurement_points, skipped_points = _filter_stage_points(
        owner,
        all_points_sorted,
        limits,
    )
    owner.sorted_indices = [mp["point_index"] for mp in measurement_points]
    _set_progress_estimate(owner, len(owner.sorted_indices))

    if skipped_points:
        pm.logger.info(
            f"Filtered measurement points: {len(measurement_points)} valid, "
            f"{len(skipped_points)} skipped due to axis limits"
        )
        owner._append_capture_log(
            f"Filtered points: {len(measurement_points)} valid, {len(skipped_points)} skipped"
        )

    if not measurement_points:
        x_min, x_max = limits["x"]
        y_min, y_max = limits["y"]
        pm.logger.error("No valid measurement points within axis limits")
        pm.QMessageBox.warning(
            owner,
            "No Valid Points",
            f"All measurement points exceed the axis limits of X[{x_min:.1f},{x_max:.1f}] and Y[{y_min:.1f},{y_max:.1f}] mm. "
            "Please adjust your measurement grid.",
        )
        owner._set_measurement_controls_idle()
        owner._append_capture_log("Start failed: all points are outside stage limits")
        return None

    return measurement_points, skipped_points
