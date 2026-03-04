"""Restore-focused helpers extracted from SessionWorkspaceMixin."""

import json
import logging
from pathlib import Path

from difra.gui.container_api import get_schema

logger = logging.getLogger(__name__)


def restore_session_workspace_from_container(owner, session_path: Path):
    """Restore image/zones/points from an existing session container into GUI."""
    if not hasattr(owner, "state"):
        owner.state = {}

    try:
        import h5py

        schema = get_schema(owner.config if hasattr(owner, "config") else None)

        restored_shapes = []
        restored_points = []
        restored_image = False
        restored_ratio = None
        restored_include_center = None
        restored_stage_reference = None

        with h5py.File(session_path, "r") as h5f:
            images_group = h5f.get(schema.GROUP_IMAGES)
            if images_group:
                image_keys = sorted(
                    key for key in images_group.keys() if key.startswith("img_")
                )
                if image_keys:
                    image_group = images_group[image_keys[0]]
                    if "data" in image_group:
                        restored_image = owner._set_image_from_array(image_group["data"][:])

            zones_group = h5f.get(schema.GROUP_IMAGES_ZONES)
            if zones_group:
                for index, zone_id in enumerate(sorted(zones_group.keys()), start=1):
                    zone_group = zones_group[zone_id]
                    zone_role = str(
                        owner._decode_attr(
                            zone_group.attrs.get(schema.ATTR_ZONE_ROLE, "sample_holder")
                        )
                    ).lower()
                    shape_name = str(
                        owner._decode_attr(
                            zone_group.attrs.get(schema.ATTR_ZONE_SHAPE, "circle")
                        )
                    ).lower()
                    geometry_value = None
                    if "geometry_px" in zone_group:
                        raw_geometry = zone_group["geometry_px"][()]
                        if isinstance(raw_geometry, bytes):
                            raw_geometry = raw_geometry.decode("utf-8", errors="replace")
                        geometry_value = raw_geometry

                    x = y = width = height = 0.0
                    if isinstance(geometry_value, str):
                        parsed = json.loads(geometry_value)
                        if isinstance(parsed, dict):
                            if "center" in parsed and "radius" in parsed:
                                center_x, center_y = parsed.get("center", [0, 0])
                                radius = float(parsed.get("radius", 0))
                                x = float(center_x) - radius
                                y = float(center_y) - radius
                                width = height = radius * 2.0
                            else:
                                x = float(parsed.get("x", 0))
                                y = float(parsed.get("y", 0))
                                width = float(parsed.get("width", 0))
                                height = float(parsed.get("height", 0))
                        elif isinstance(parsed, list) and len(parsed) >= 4:
                            x, y, width, height = [float(parsed[i]) for i in range(4)]
                    elif geometry_value is not None:
                        values = list(geometry_value)
                        if len(values) >= 4:
                            x, y, width, height = [float(values[i]) for i in range(4)]

                    ui_role = "include" if zone_role == "sample_holder" else zone_role
                    restored_shapes.append(
                        {
                            "id": index,
                            "uid": f"shape_{zone_id}",
                            "type": "circle" if shape_name == "circle" else "rectangle",
                            "role": ui_role,
                            "geometry": {
                                "x": x,
                                "y": y,
                                "width": width,
                                "height": height,
                            },
                        }
                    )

            points_group = h5f.get(schema.GROUP_POINTS)
            if points_group:
                for point_id in sorted(points_group.keys()):
                    point_group = points_group[point_id]
                    pixel_coords = point_group.attrs.get(schema.ATTR_PIXEL_COORDINATES, [])
                    if len(pixel_coords) < 2:
                        continue
                    point_index = int(point_id.split("_")[-1])
                    point_uid = owner._decode_attr(point_group.attrs.get("point_uid", ""))
                    restored_points.append(
                        {
                            "id": point_index,
                            "x": float(pixel_coords[0]),
                            "y": float(pixel_coords[1]),
                            "type": "generated",
                            "radius": 5.0,
                            "uid": point_uid if point_uid else None,
                        }
                    )

            mapping_ds = h5f.get(f"{schema.GROUP_IMAGES_MAPPING}/mapping")
            if mapping_ds is not None:
                mapping_raw = mapping_ds[()]
                if isinstance(mapping_raw, bytes):
                    mapping_raw = mapping_raw.decode("utf-8", errors="replace")
                mapping = json.loads(mapping_raw)
                conversion = mapping.get("pixel_to_mm_conversion", {})
                if isinstance(conversion, dict):
                    if "ratio" in conversion:
                        restored_ratio = float(conversion["ratio"])
                    restored_include_center = owner._normalize_xy_pair(
                        conversion.get("include_center_px")
                        or conversion.get("center_px")
                        or conversion.get("image_center_px")
                    )
                    restored_stage_reference = owner._normalize_xy_pair(
                        conversion.get("stage_reference_mm")
                        or conversion.get("reference_mm")
                        or conversion.get("stage_origin_mm")
                    )

        owner.state["shapes"] = restored_shapes
        owner.state["zone_points"] = restored_points

        if hasattr(owner, "_restore_shapes"):
            owner._restore_shapes(restored_shapes)
        if hasattr(owner, "_restore_points"):
            owner._restore_points(restored_points)
        if hasattr(owner, "_refresh_id_counter"):
            owner._refresh_id_counter()

        if hasattr(owner, "update_points_table"):
            owner.update_points_table()
        if hasattr(owner, "update_shape_table"):
            owner.update_shape_table()

        if restored_ratio is not None:
            owner.pixel_to_mm_ratio = restored_ratio
        if restored_include_center is not None:
            owner.include_center = (
                float(restored_include_center[0]),
                float(restored_include_center[1]),
            )
        if restored_stage_reference is not None:
            owner._set_spin_value_if_present("real_x_pos_mm", restored_stage_reference[0])
            owner._set_spin_value_if_present("real_y_pos_mm", restored_stage_reference[1])
        if hasattr(owner, "update_conversion_label"):
            owner.update_conversion_label()
        if hasattr(owner, "update_coordinates"):
            owner.update_coordinates()

        owner._restore_measurement_history_from_session(session_path)
        refresh_point_visuals = getattr(owner, "refresh_point_visual_states", None)
        if callable(refresh_point_visuals):
            refresh_point_visuals()

        logger.info(
            "Restored workspace from session container: session=%s image_loaded=%s shapes=%d points=%d",
            session_path,
            restored_image,
            len(restored_shapes),
            len(restored_points),
        )
        if hasattr(owner, "_append_session_log"):
            owner._append_session_log(
                f"Workspace restored from session: shapes={len(restored_shapes)}, points={len(restored_points)}"
            )
    except Exception as exc:
        logger.warning(
            "Session workspace restore failed for %s: %s",
            session_path,
            exc,
            exc_info=True,
        )
        if hasattr(owner, "_append_session_log"):
            owner._append_session_log(
                f"Workspace restore failed: {type(exc).__name__}"
            )


def restore_measurement_history_from_session(owner, session_path: Path):
    """Populate per-point measurement widgets from session container payloads."""
    if not hasattr(owner, "measurement_widgets"):
        owner.measurement_widgets = {}
    if not hasattr(owner, "add_measurement_widget_to_panel"):
        return

    try:
        import h5py

        schema = get_schema(owner.config if hasattr(owner, "config") else None)
        detector_id_to_alias = {}
        for detector_cfg in (owner.config or {}).get("detectors", []):
            detector_id = detector_cfg.get("id")
            detector_alias = detector_cfg.get("alias")
            if detector_id and detector_alias:
                detector_id_to_alias[str(detector_id)] = str(detector_alias)

        point_uid_to_display = {}
        identity_getter = getattr(owner, "_get_point_identity_from_row", None)
        if callable(identity_getter) and hasattr(owner, "pointsTable") and owner.pointsTable is not None:
            for row in range(owner.pointsTable.rowCount()):
                try:
                    point_uid, point_display_id = identity_getter(row)
                except Exception:
                    continue
                point_uid = str(point_uid or "").strip()
                if point_uid:
                    point_uid_to_display[point_uid] = point_display_id

        with h5py.File(session_path, "r") as h5f:
            measurements_group = h5f.get(schema.GROUP_MEASUREMENTS)
            if measurements_group is None:
                return
            session_point_uid_by_index = {}
            points_group = h5f.get(schema.GROUP_POINTS)
            if points_group is not None:
                for point_group_name in sorted(points_group.keys()):
                    if not str(point_group_name).startswith("pt_"):
                        continue
                    try:
                        point_index = int(str(point_group_name).split("_")[-1])
                    except Exception:
                        continue
                    point_group = points_group[point_group_name]
                    point_uid = owner._decode_attr(point_group.attrs.get("point_uid", ""))
                    point_uid = str(point_uid or "").strip()
                    if point_uid:
                        session_point_uid_by_index[point_index] = point_uid

            for point_group_name in sorted(measurements_group.keys()):
                if not str(point_group_name).startswith("pt_"):
                    continue
                try:
                    point_index = int(str(point_group_name).split("_")[-1])
                except Exception:
                    continue

                point_group = measurements_group[point_group_name]
                point_uid = str(session_point_uid_by_index.get(point_index) or "").strip()
                if not point_uid:
                    point_uid = str(
                        owner._decode_attr(point_group.attrs.get("point_uid", "")) or ""
                    ).strip()
                if not point_uid and callable(identity_getter):
                    try:
                        point_uid, _point_display_id = identity_getter(point_index - 1)
                    except Exception:
                        point_uid = None
                    point_uid = str(point_uid or "").strip()
                if not point_uid:
                    continue

                point_display_id = point_uid_to_display.get(point_uid)
                if point_display_id is None:
                    try:
                        point_display_id = int(str(point_uid).split("_", 1)[0])
                    except Exception:
                        point_display_id = None

                try:
                    owner.add_measurement_widget_to_panel(
                        point_uid, point_display_id=point_display_id
                    )
                except TypeError:
                    owner.add_measurement_widget_to_panel(point_uid)
                widget = owner.measurement_widgets.get(point_uid)
                if widget is None:
                    continue

                for meas_name in sorted(point_group.keys()):
                    meas_group = point_group[meas_name]
                    timestamp = owner._decode_attr(
                        meas_group.attrs.get(schema.ATTR_TIMESTAMP_END, "")
                    )
                    if not timestamp:
                        timestamp = owner._decode_attr(
                            meas_group.attrs.get(schema.ATTR_TIMESTAMP_START, "")
                        )
                    if not timestamp:
                        timestamp = owner._decode_attr(
                            meas_group.attrs.get(schema.ATTR_TIMESTAMP, "")
                        )
                    results = {}
                    for detector_group_name in sorted(meas_group.keys()):
                        if not str(detector_group_name).startswith("det_"):
                            continue
                        detector_group = meas_group[detector_group_name]
                        detector_alias = owner._decode_attr(
                            detector_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                        )
                        if not detector_alias:
                            detector_id = owner._decode_attr(
                                detector_group.attrs.get(schema.ATTR_DETECTOR_ID, "")
                            )
                            detector_alias = detector_id_to_alias.get(
                                str(detector_id),
                                str(detector_group_name).replace("det_", "").upper(),
                            )

                        dataset_name = schema.DATASET_PROCESSED_SIGNAL
                        if dataset_name not in detector_group:
                            continue
                        dataset_path = (
                            f"{schema.GROUP_MEASUREMENTS}/{point_group_name}/"
                            f"{meas_name}/{detector_group_name}/{dataset_name}"
                        )
                        h5_ref = f"h5ref://{session_path}#{dataset_path}"
                        results[str(detector_alias)] = {
                            "filename": h5_ref,
                            "goodness": None,
                        }

                    if results:
                        widget.add_measurement(results, timestamp or "from container")
    except Exception as exc:
        logger.warning(
            "Failed to restore measurement history from session container %s: %s",
            session_path,
            exc,
            exc_info=True,
        )
