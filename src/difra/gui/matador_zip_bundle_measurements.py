from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Tuple

import h5py


class MatadorZipBundleMeasurementMixin:
    """Matador ZIP measurement, detector PONI, and technical aux helpers."""

    @classmethod
    def _measurement_points_from_state_or_container(
        cls,
        *,
        h5f: h5py.File,
        state_payload: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], Dict[int, str]]:
        raw_points = state_payload.get("measurement_points")
        normalized_points: List[Dict[str, Any]] = []

        if isinstance(raw_points, list) and raw_points:
            for idx, point in enumerate(raw_points):
                if not isinstance(point, dict):
                    continue
                x_mm = cls._to_float(point.get("x"))
                y_mm = cls._to_float(point.get("y"))
                point_index = cls._safe_int(point.get("point_index"))
                if point_index is None:
                    point_index = cls._safe_int(point.get("index"))
                if point_index is None:
                    point_index = idx + 1
                unique_id = cls._as_text(point.get("unique_id"), "").strip()
                if not unique_id:
                    unique_id = hashlib.md5(
                        f"{point_index}:{x_mm}:{y_mm}".encode("utf-8")
                    ).hexdigest()[:16]
                normalized_points.append(
                    {
                        "unique_id": unique_id,
                        "index": idx,
                        "point_index": int(point_index),
                        "x": x_mm,
                        "y": y_mm,
                    }
                )

        if not normalized_points:
            built_points = cls._build_measurement_points(h5f)
            for idx, point in enumerate(built_points):
                x_mm = cls._to_float(point.get("x"))
                y_mm = cls._to_float(point.get("y"))
                point_index = cls._safe_int(point.get("point_index")) or (idx + 1)
                point_name = cls._as_text(
                    point.get("unique_id"), f"pt_{point_index:03d}"
                )
                unique_id = hashlib.md5(
                    f"{point_name}:{point_index}:{x_mm}:{y_mm}".encode("utf-8")
                ).hexdigest()[:16]
                normalized_points.append(
                    {
                        "unique_id": unique_id,
                        "index": idx,
                        "point_index": int(point_index),
                        "x": x_mm,
                        "y": y_mm,
                    }
                )

        point_uid_by_index = {
            int(point["point_index"]): str(point["unique_id"])
            for point in normalized_points
            if point.get("unique_id") is not None
        }
        return normalized_points, point_uid_by_index

    @classmethod
    def _collect_detector_poni(cls, h5f: h5py.File) -> Dict[str, Dict[str, str]]:
        detector_poni: Dict[str, Dict[str, str]] = {}
        schema = cls._schema_for_h5(h5f)
        technical_group = h5f.get(
            getattr(schema, "GROUP_TECHNICAL", "/entry/technical")
        )
        if technical_group is None:
            return detector_poni

        poni_group = h5f.get(
            getattr(schema, "GROUP_TECHNICAL_PONI", f"{technical_group.name}/poni")
        )
        if poni_group is None:
            return detector_poni

        for poni_name in sorted(poni_group.keys()):
            poni_ds = poni_group[poni_name]
            alias = cls._as_text(
                poni_ds.attrs.get(
                    getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")
                ),
                "",
            ).upper()
            if not alias:
                continue
            detector_poni[alias] = {
                "poni_filename": cls._as_text(
                    poni_ds.attrs.get("poni_filename"),
                    str(poni_name),
                ),
                "poni_value": cls._as_text(poni_ds[()], ""),
            }
        return detector_poni

    @classmethod
    def _collect_technical_aux(
        cls,
        *,
        h5f: h5py.File,
        state_payload: Dict[str, Any],
        day_token: str,
    ) -> List[Dict[str, str]]:
        seen = set()
        rows: List[Dict[str, str]] = []

        raw_rows = state_payload.get("technical_aux")
        if isinstance(raw_rows, list):
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                technical_type = cls._as_text(row.get("type"), "").upper()
                alias = cls._as_text(row.get("alias"), "").upper()
                if not technical_type or not alias:
                    continue
                key = (technical_type, alias)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"type": technical_type, "alias": alias})

        events, _canonical_distance = cls._collect_technical_events(
            h5f=h5f,
            day_token=day_token,
            default_distance_int=17,
        )
        for event in events:
            technical_type = cls._as_text(event.get("type"), "").upper()
            alias = cls._as_text(event.get("alias"), "").upper()
            if not technical_type or not alias:
                continue
            key = (technical_type, alias)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"type": technical_type, "alias": alias})
        return rows

    @classmethod
    def _infer_measurement_meta(
        cls,
        *,
        det_group,
        detector_alias: str,
        detector_id: str,
        x_mm,
        y_mm,
        unique_id: str,
        base_file: str,
        integration_s,
        calibration_group_hash: str,
    ) -> Dict[str, Any]:
        merged: Dict[str, Any] = {
            "x": x_mm,
            "y": y_mm,
            "unique_id": unique_id,
            "base_file": base_file,
            "integration_time": integration_s,
            "detector_alias": detector_alias,
            "detector_id": detector_id,
            "CALIBRATION_GROUP_HASH": calibration_group_hash,
        }
        for attr_key, state_key in (
            ("detector_type", "detector_type"),
            ("detector_size", "detector_size"),
            ("pixel_size_um", "pixel_size_um"),
            ("faulty_pixels", "faulty_pixels"),
        ):
            attr_value = det_group.attrs.get(attr_key)
            if attr_value is None:
                continue
            merged[state_key] = cls._as_json_value(attr_value)
        return merged

    @classmethod
    def _export_regular_measurements(
        cls,
        *,
        h5f: h5py.File,
        export_dir: Path,
        day_token: str,
        bundle_base: str,
        calibration_group_hash: str,
        point_uid_by_index: Dict[int, str],
    ) -> Tuple[int, Dict[str, Dict[str, Any]], List[str], Dict[str, Any]]:
        schema = cls._schema_for_h5(h5f)
        measurements_group = h5f.get(
            getattr(schema, "GROUP_MEASUREMENTS", "/entry/measurements")
        )
        if measurements_group is None:
            return 0, {}, [], {}

        exported = 0
        measurements_meta: Dict[str, Dict[str, Any]] = {}
        file_names: List[str] = []
        machine_summary: Dict[str, Any] = {}

        for point_name in sorted(measurements_group.keys()):
            point_group = measurements_group[point_name]
            x_mm, y_mm = cls._extract_point_coordinates(h5f, point_name)
            try:
                session_point_idx = int(str(point_name).split("_")[-1])
            except Exception:
                session_point_idx = len(point_uid_by_index) + 1
            unique_id = point_uid_by_index.get(session_point_idx)
            if not unique_id:
                unique_id = hashlib.md5(
                    f"{point_name}:{x_mm}:{y_mm}".encode("utf-8")
                ).hexdigest()[:16]
                point_uid_by_index[session_point_idx] = unique_id

            for meas_name in sorted(point_group.keys()):
                measurement_group = point_group[meas_name]
                ts_token = cls._normalize_timestamp_token(
                    measurement_group.attrs.get(
                        getattr(schema, "ATTR_TIMESTAMP_END", "timestamp_end")
                    )
                    or measurement_group.attrs.get(
                        getattr(schema, "ATTR_TIMESTAMP_START", "timestamp_start")
                    ),
                    day_token,
                )

                for det_name in sorted(measurement_group.keys()):
                    if not str(det_name).startswith("det_"):
                        continue
                    det_group = measurement_group[det_name]
                    dataset_processed_signal = getattr(
                        schema, "DATASET_PROCESSED_SIGNAL", "processed_signal"
                    )
                    if dataset_processed_signal not in det_group:
                        continue

                    detector_alias = cls._as_text(
                        det_group.attrs.get(
                            getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")
                        ),
                        str(det_name).replace("det_", "").upper(),
                    ).upper()
                    detector_id = cls._as_text(
                        det_group.attrs.get(
                            getattr(schema, "ATTR_DETECTOR_ID", "detector_id")
                        ),
                        detector_alias,
                    )
                    base = cls._measurement_file_base(
                        bundle_base=bundle_base,
                        point_name=str(point_name),
                        point_unique_id=str(unique_id),
                        measurement_name=str(meas_name),
                        x_mm=x_mm,
                        y_mm=y_mm,
                        timestamp_token=ts_token,
                        detector_alias=detector_alias,
                    )

                    integration_s = None
                    integration_ms = cls._to_float(
                        det_group.attrs.get(
                            getattr(
                                schema,
                                "ATTR_INTEGRATION_TIME_MS",
                                "integration_time_ms",
                            )
                        )
                    )
                    if integration_ms is not None:
                        integration_s = integration_ms / 1000.0

                    meta_entry = cls._infer_measurement_meta(
                        det_group=det_group,
                        detector_alias=detector_alias,
                        detector_id=detector_id,
                        x_mm=x_mm,
                        y_mm=y_mm,
                        unique_id=unique_id,
                        base_file=bundle_base,
                        integration_s=integration_s,
                        calibration_group_hash=calibration_group_hash,
                    )

                    if not machine_summary:
                        machine_summary = {
                            "detector_alias": detector_alias,
                            "detector_id": detector_id,
                            "detector_type": meta_entry.get("detector_type"),
                            "detector_size": meta_entry.get("detector_size"),
                            "pixel_size_um": meta_entry.get("pixel_size_um"),
                            "integration_s": integration_s,
                            "beam_energy_keV": cls._to_float(
                                det_group.attrs.get("beam_energy_keV")
                            ),
                        }

                    blob_group = det_group.get("blob")
                    raw_blobs: Dict[str, bytes] = {}
                    if blob_group is not None:
                        for blob_name in sorted(blob_group.keys()):
                            if not str(blob_name).startswith("raw_"):
                                continue
                            raw_blobs[str(blob_name)] = cls._read_blob_bytes(
                                blob_group[blob_name]
                            )

                    matrix_name, matrix_payload, _matrix_ext, dsc_payload = (
                        cls._select_matrix_payload(
                            base=base,
                            processed_signal=det_group[dataset_processed_signal][()],
                            raw_blobs=raw_blobs,
                        )
                    )
                    cls._write_bytes_if_changed(export_dir / matrix_name, matrix_payload)
                    measurements_meta[matrix_name] = dict(meta_entry)
                    file_names.append(matrix_name)
                    exported += 1
                    if dsc_payload:
                        dsc_name = cls._measurement_dsc_sidecar_name(matrix_name)
                        cls._write_bytes_if_changed(export_dir / dsc_name, dsc_payload)
                        file_names.append(dsc_name)
                        exported += 1

        return exported, measurements_meta, file_names, machine_summary
