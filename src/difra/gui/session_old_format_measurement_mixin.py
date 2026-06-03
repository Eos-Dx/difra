"""Export session containers into legacy folder layout used by older DIFRA flows."""

import base64
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import h5py
import numpy as np


@dataclass
class OldFormatExportSummary:
    """Summary for one legacy export operation."""

    export_dir: Path
    state_path: Path
    raw_file_count: int
    technical_file_count: int


class SessionOldFormatMeasurementMixin:
    """Old-format export behavior split from SessionOldFormatExporter."""

    @classmethod
    def _export_measurement_raw(
        cls,
        *,
        h5f: h5py.File,
        sample_dir: Path,
        sample_base_with_distance: str,
        day_token: str,
        calibration_group_hash: str,
        point_uid_by_session_index: Dict[int, str],
        existing_lookup: Tuple[
            Dict[str, Dict[str, Any]],
            Dict[str, Dict[str, Any]],
            Dict[Tuple[str, str], Dict[str, Any]],
            Dict[Tuple[str, str], Dict[str, Any]],
        ],
    ) -> Tuple[int, Dict[str, Dict[str, Any]]]:
        schema = cls._schema_for_h5(h5f)
        measurements_group = h5f.get(getattr(schema, "GROUP_MEASUREMENTS", "/entry/measurements"))
        if measurements_group is None:
            return 0, {}

        by_txt, by_npy, by_uid_alias, by_uid_detector = existing_lookup

        exported = 0
        measurements_meta: Dict[str, Dict[str, Any]] = {}

        for point_name in sorted(measurements_group.keys()):
            point_group = measurements_group[point_name]
            x_mm, y_mm = cls._extract_point_coordinates(h5f, point_name)
            try:
                session_point_idx = int(str(point_name).split("_")[-1])
            except Exception:
                session_point_idx = len(point_uid_by_session_index) + 1
            point_uid = point_uid_by_session_index.get(session_point_idx, str(point_name))

            for meas_name in sorted(point_group.keys()):
                measurement_group = point_group[meas_name]
                ts_token = cls._normalize_timestamp_token(
                    measurement_group.attrs.get(getattr(schema, "ATTR_TIMESTAMP_END", "timestamp_end"))
                    or measurement_group.attrs.get(getattr(schema, "ATTR_TIMESTAMP_START", "timestamp_start")),
                    day_token,
                )

                for det_name in sorted(measurement_group.keys()):
                    if not str(det_name).startswith("det_"):
                        continue
                    det_group = measurement_group[det_name]
                    dataset_processed_signal = getattr(schema, "DATASET_PROCESSED_SIGNAL", "processed_signal")
                    if dataset_processed_signal not in det_group:
                        continue

                    detector_alias = cls._as_text(
                        det_group.attrs.get(getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")),
                        str(det_name).replace("det_", "").upper(),
                    ).upper()
                    detector_id = cls._as_text(
                        det_group.attrs.get(getattr(schema, "ATTR_DETECTOR_ID", "detector_id")),
                        detector_alias,
                    )
                    base = cls._measurement_file_base(
                        bundle_base=sample_base_with_distance,
                        point_name=str(point_name),
                        point_unique_id=str(point_uid),
                        measurement_name=str(meas_name),
                        x_mm=x_mm,
                        y_mm=y_mm,
                        timestamp_token=ts_token,
                        detector_alias=detector_alias,
                    )
                    blob_group = det_group.get("blob")
                    raw_blobs: Dict[str, bytes] = {}
                    if blob_group is not None:
                        for blob_name in sorted(blob_group.keys()):
                            if not str(blob_name).startswith("raw_"):
                                continue
                            raw_blobs[str(blob_name)] = cls._read_blob_bytes(blob_group[blob_name])

                    matrix_name, matrix_payload, _matrix_ext, dsc_payload = cls._select_matrix_payload(
                        base=base,
                        processed_signal=det_group[dataset_processed_signal][()],
                        raw_blobs=raw_blobs,
                    )
                    cls._write_bytes_if_changed(sample_dir / matrix_name, matrix_payload)
                    exported += 1
                    if dsc_payload:
                        cls._write_bytes_if_changed(
                            sample_dir / cls._measurement_dsc_sidecar_name(matrix_name),
                            dsc_payload,
                        )
                        exported += 1

                    integration_s = None
                    integration_ms = cls._to_float(
                        det_group.attrs.get(getattr(schema, "ATTR_INTEGRATION_TIME_MS", "integration_time_ms"))
                    )
                    if integration_ms is not None:
                        integration_s = integration_ms / 1000.0

                    existing_entry = (
                        by_txt.get(f"{base}.txt")
                        or by_npy.get(f"{base}.npy")
                        or by_uid_alias.get((point_uid, detector_alias))
                        or by_uid_detector.get((point_uid, detector_id))
                    )
                    if not isinstance(existing_entry, dict):
                        existing_entry = {}

                    merged = dict(existing_entry)
                    merged["x"] = x_mm
                    merged["y"] = y_mm
                    merged["unique_id"] = point_uid
                    merged["base_file"] = sample_base_with_distance
                    merged["integration_time"] = integration_s
                    merged["detector_alias"] = detector_alias
                    merged["detector_id"] = detector_id
                    if calibration_group_hash:
                        merged["CALIBRATION_GROUP_HASH"] = calibration_group_hash

                    for attr_key, state_key in (
                        ("detector_type", "detector_type"),
                        ("detector_size", "detector_size"),
                        ("pixel_size_um", "pixel_size_um"),
                        ("faulty_pixels", "faulty_pixels"),
                    ):
                        if state_key in merged and merged.get(state_key) is not None:
                            continue
                        attr_value = det_group.attrs.get(attr_key)
                        if attr_value is not None:
                            if isinstance(attr_value, np.ndarray):
                                merged[state_key] = attr_value.tolist()
                            else:
                                merged[state_key] = attr_value

                    measurements_meta[matrix_name] = merged

        return exported, measurements_meta

    @classmethod
    def export_from_session_container(
        cls,
        session_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
        archive_folder: Optional[Path] = None,
        target_root: Optional[Path] = None,
    ) -> OldFormatExportSummary:
        """Export legacy raw/state/technical folders from a session container."""
        source = Path(session_path)
        if not source.exists():
            raise FileNotFoundError(f"Session container not found: {source}")

        cfg = config or {}

        with h5py.File(source, "r") as h5f:
            sample_id = cls._as_text(
                h5f.attrs.get("specimenId", h5f.attrs.get("sample_id")),
                "UNKNOWN",
            )
            study_name = cls._as_text(h5f.attrs.get("study_name"), "UNSPECIFIED")
            session_id = cls._as_text(h5f.attrs.get("session_id"), source.stem)
            operator_id = cls._as_text(
                h5f.attrs.get("operator_id") or h5f.attrs.get("locked_by"),
                "unknown",
            )
            acquisition_date = cls._as_text(h5f.attrs.get("acquisition_date"), "")

            state_payload = cls._load_state_payload(h5f)
            if not isinstance(state_payload, dict):
                state_payload = {}

            fallback_timestamps = cls._collect_fallback_timestamps(h5f)
            day_token = cls._resolve_day_token(
                acquisition_date=acquisition_date,
                fallback_timestamps=fallback_timestamps,
            )

            root = (
                Path(target_root)
                if target_root is not None
                else cls.resolve_old_format_root(
                    config=config,
                    archive_folder=archive_folder,
                )
            )
            root.mkdir(parents=True, exist_ok=True)
            day_dir = root / day_token
            day_dir.mkdir(parents=True, exist_ok=True)
            measurements_root = day_dir / "measurements"
            measurements_root.mkdir(parents=True, exist_ok=True)

            group_hash = cls._resolve_calibration_group_hash(
                h5f,
                state_payload=state_payload,
                config=cfg,
                fallback=source.stem,
            )
            state_payload["CALIBRATION_GROUP_HASH"] = group_hash

            default_distance_int = cls._distance_int(
                cls._to_float(cfg.get("default_technical_distance_cm")),
                default=17,
            )
            technical_count, distance_token, technical_aux_map, detector_poni_map, _tech_meta_path = cls._export_technical_measurements(
                h5f=h5f,
                day_dir=day_dir,
                day_token=day_token,
                default_distance_int=default_distance_int,
                calibration_group_hash=group_hash,
                config=cfg,
            )

            sample_folder_name = cls._derive_sample_folder_name(
                state_payload=state_payload,
                sample_id=sample_id,
                study_name=study_name,
                session_id=session_id,
            )
            specimen_folder_name = cls._safe_token(sample_folder_name, "sample")
            sample_dir = measurements_root / specimen_folder_name
            sample_dir.mkdir(parents=True, exist_ok=True)

            sample_base_with_distance = cls._derive_sample_base_with_distance(
                state_payload=state_payload,
                sample_id=sample_id,
                distance_token=distance_token,
            )

            measurement_points = state_payload.get("measurement_points")
            if not isinstance(measurement_points, list) or not measurement_points:
                measurement_points = cls._build_measurement_points(h5f)
                state_payload["measurement_points"] = measurement_points

            point_uid_by_session_index: Dict[int, str] = {}
            for idx, point in enumerate(measurement_points, start=1):
                if isinstance(point, dict):
                    uid = cls._as_text(point.get("unique_id"), "").strip()
                    if uid:
                        point_uid_by_session_index[idx] = uid

            existing_lookup = cls._build_existing_measurement_lookup(state_payload)
            raw_count, measurements_meta = cls._export_measurement_raw(
                h5f=h5f,
                sample_dir=sample_dir,
                sample_base_with_distance=sample_base_with_distance,
                day_token=day_token,
                calibration_group_hash=group_hash,
                point_uid_by_session_index=point_uid_by_session_index,
                existing_lookup=existing_lookup,
            )

            state_payload["measurements_meta"] = measurements_meta

            attenuation_count, attenuation_files = cls._export_attenuation_files(
                h5f=h5f,
                sample_dir=sample_dir,
                sample_base_with_distance=sample_base_with_distance,
                day_token=day_token,
                state_payload=state_payload,
                point_uid_by_session_index=point_uid_by_session_index,
            )
            raw_count += attenuation_count
            if attenuation_files:
                state_payload["attenuation_files"] = attenuation_files
            elif "attenuation_files" in state_payload:
                state_payload["attenuation_files"] = {}

            if technical_aux_map:
                preferred_types = ["DARK", "EMPTY", "AGBH", "BACKGROUND"]
                technical_aux_rows: List[Dict[str, Any]] = []
                for technical_type in preferred_types:
                    for (row_type, alias), file_path in sorted(technical_aux_map.items()):
                        if row_type != technical_type:
                            continue
                        technical_aux_rows.append(
                            {
                                "file_path": file_path,
                                "type": row_type,
                                "alias": alias,
                            }
                        )
                for (row_type, alias), file_path in sorted(technical_aux_map.items()):
                    if row_type in preferred_types:
                        continue
                    technical_aux_rows.append(
                        {
                            "file_path": file_path,
                            "type": row_type,
                            "alias": alias,
                        }
                    )
                state_payload["technical_aux"] = technical_aux_rows

            if detector_poni_map:
                state_payload["detector_poni"] = detector_poni_map

            state_payload.setdefault("sample_id", sample_id)
            state_payload.setdefault("specimenId", sample_id)
            state_payload.setdefault("study_name", study_name)
            state_payload.setdefault("session_id", session_id)
            state_payload.setdefault("operator_id", operator_id)

            exported_image = cls._export_session_image(
                state_payload=state_payload,
                h5f=h5f,
                export_dir=sample_dir,
                sample_id=sample_id,
                session_id=session_id,
            )
            if exported_image is not None:
                state_payload["image"] = str(exported_image.resolve())
                try:
                    state_payload["image_base64"] = base64.b64encode(
                        exported_image.read_bytes()
                    ).decode("ascii")
                except Exception:
                    pass

            state_filename = "session.json"
            state_path = sample_dir / state_filename
            state_path.write_text(
                json.dumps(state_payload, indent=2),
                encoding="utf-8",
            )

        return OldFormatExportSummary(
            export_dir=day_dir,
            state_path=state_path,
            raw_file_count=raw_count,
            technical_file_count=technical_count,
        )
