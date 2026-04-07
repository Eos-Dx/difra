"""Build Matador ZIP bundle payloads directly from session containers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import h5py
import numpy as np

from difra.gui.session_old_format_exporter import SessionOldFormatExporter


@dataclass
class MatadorZipBundleSummary:
    """Summary for one Matador ZIP bundle export."""

    export_dir: Path
    state_path: Path
    metadata_path: Path
    measurement_data_path: Path
    raw_file_count: int


class MatadorZipBundleExporter(SessionOldFormatExporter):
    """Create Matador ZIP bundle folders from a session container."""

    MATRIX_FILE_EXTENSIONS = {".txt", ".npy", ".tiff", ".tif", ".gfrm"}

    @staticmethod
    def _iso_utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @classmethod
    def _coerce_optional_int(cls, value: Any) -> Optional[int]:
        text = cls._as_text(value, "").strip()
        if not text:
            return None
        token = text.split("__", 1)[0].strip()
        try:
            return int(token)
        except Exception:
            digits = []
            for ch in token:
                if ch.isdigit():
                    digits.append(ch)
                elif digits:
                    break
            if not digits:
                return None
            try:
                return int("".join(digits))
            except Exception:
                return None

    @classmethod
    def _as_json_value(cls, value: Any) -> Any:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        return value

    @classmethod
    def _parse_specimen_patient(
        cls,
        *,
        specimen_text: str,
        patient_text: str,
    ) -> Dict[str, Any]:
        specimen_value = cls._as_text(specimen_text, "").strip()
        patient_value = cls._as_text(patient_text, "").strip()

        if "__" in specimen_value:
            specimen_head, specimen_tail = specimen_value.split("__", 1)
            specimen_head = specimen_head.strip()
            specimen_tail = specimen_tail.strip()
            if specimen_head:
                specimen_value = specimen_head
            if not patient_value and specimen_tail:
                patient_value = specimen_tail

        specimen_numeric = cls._coerce_optional_int(specimen_value)
        patient_numeric = cls._coerce_optional_int(patient_value)

        specimen_file_token = cls._safe_token(
            specimen_value or str(specimen_numeric or "") or "unknown_specimen",
            "unknown_specimen",
        )
        patient_file_token = cls._safe_token(
            patient_value or str(patient_numeric or "") or "unknown_patient",
            "unknown_patient",
        )
        return {
            "specimen_text": specimen_value,
            "patient_text": patient_value,
            "specimen_id": specimen_numeric,
            "patient_id": patient_numeric,
            "specimen_file_token": specimen_file_token,
            "patient_file_token": patient_file_token,
            "bundle_key": f"{specimen_file_token}__{patient_file_token}",
        }

    @classmethod
    def _extract_distance_mm(
        cls,
        *,
        h5f: h5py.File,
        config: Optional[Dict[str, Any]] = None,
        day_token: str,
    ) -> int:
        cfg = config or {}
        direct_value = cls._to_float(h5f.attrs.get("distance_cm", h5f.attrs.get("distanceCm")))
        if direct_value is not None:
            return max(1, int(round(direct_value * 10.0)))

        _events, canonical_distance_cm = cls._collect_technical_events(
            h5f=h5f,
            day_token=day_token,
            default_distance_int=cls._distance_int(
                cls._to_float(cfg.get("default_technical_distance_cm")),
                default=17,
            ),
        )
        return max(1, int(round(float(canonical_distance_cm) * 10.0)))

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
                point_name = cls._as_text(point.get("unique_id"), f"pt_{point_index:03d}")
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
        technical_group = h5f.get(getattr(schema, "GROUP_TECHNICAL", "/entry/technical"))
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
                poni_ds.attrs.get(getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")),
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
        x_mm: Optional[float],
        y_mm: Optional[float],
        unique_id: str,
        base_file: str,
        integration_s: Optional[float],
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
        measurements_group = h5f.get(getattr(schema, "GROUP_MEASUREMENTS", "/entry/measurements"))
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
                    alias_token = cls._safe_token(detector_alias, "DETECTOR").upper()
                    base = (
                        f"{bundle_base}_"
                        f"{cls._format_coord_token(x_mm)}_"
                        f"{cls._format_coord_token(y_mm)}_"
                        f"{ts_token}_"
                        f"{alias_token}"
                    )

                    integration_s = None
                    integration_ms = cls._to_float(
                        det_group.attrs.get(getattr(schema, "ATTR_INTEGRATION_TIME_MS", "integration_time_ms"))
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

                    npy_name = f"{base}.npy"
                    cls._write_bytes_if_changed(
                        export_dir / npy_name,
                        cls._npy_bytes(det_group[dataset_processed_signal][()]),
                    )
                    measurements_meta[npy_name] = dict(meta_entry)
                    file_names.append(npy_name)
                    exported += 1

                    blob_group = det_group.get("blob")
                    if blob_group is None:
                        continue

                    for blob_name in sorted(blob_group.keys()):
                        if not str(blob_name).startswith("raw_"):
                            continue
                        ext = str(blob_name)[4:] or "bin"
                        if ext == "txt":
                            raw_name = f"{base}.txt"
                            measurements_meta[raw_name] = dict(meta_entry)
                        elif ext == "dsc":
                            raw_name = f"{base}.txt.dsc"
                        elif ext in {"tif", "tiff"}:
                            raw_name = f"{base}.tiff"
                            measurements_meta[raw_name] = dict(meta_entry)
                        elif ext == "gfrm":
                            raw_name = f"{base}.gfrm"
                            measurements_meta[raw_name] = dict(meta_entry)
                        else:
                            raw_name = f"{base}.{ext}"

                        cls._write_bytes_if_changed(
                            export_dir / raw_name,
                            cls._read_blob_bytes(blob_group[blob_name]),
                        )
                        file_names.append(raw_name)
                        exported += 1

        return exported, measurements_meta, file_names, machine_summary

    @classmethod
    def _strip_machine_local_state(
        cls,
        *,
        state_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        cleaned: Dict[str, Any] = {}
        for key in (
            "measurement_points",
            "skipped_points",
            "active_detectors_aliases",
            "CALIBRATION_GROUP_HASH",
            "detector_poni",
            "technical_aux",
            "measurements_meta",
            "attenuation_files",
            "real_center",
            "pixel_to_mm_ratio",
            "rotation_angle",
            "crop_rect",
            "shapes",
            "zone_points",
        ):
            if key in state_payload:
                cleaned[key] = state_payload[key]

        detector_poni = cleaned.get("detector_poni")
        if isinstance(detector_poni, dict):
            sanitized_poni = {}
            for alias, payload in detector_poni.items():
                if not isinstance(payload, dict):
                    continue
                sanitized_poni[str(alias)] = {
                    "poni_filename": payload.get("poni_filename"),
                    "poni_value": payload.get("poni_value", ""),
                }
            cleaned["detector_poni"] = sanitized_poni

        technical_aux = cleaned.get("technical_aux")
        if isinstance(technical_aux, list):
            sanitized_aux = []
            for row in technical_aux:
                if not isinstance(row, dict):
                    continue
                technical_type = cls._as_text(row.get("type"), "").upper()
                alias = cls._as_text(row.get("alias"), "").upper()
                if technical_type and alias:
                    sanitized_aux.append({"type": technical_type, "alias": alias})
            cleaned["technical_aux"] = sanitized_aux

        attenuation_files = cleaned.get("attenuation_files")
        if isinstance(attenuation_files, dict):
            sanitized_attenuation: Dict[str, Dict[str, Dict[str, str]]] = {}
            for point_uid, point_roles in attenuation_files.items():
                if not isinstance(point_roles, dict):
                    continue
                for role_key, alias_map in point_roles.items():
                    if not isinstance(alias_map, dict):
                        continue
                    for alias, file_path in alias_map.items():
                        name = Path(cls._as_text(file_path, "")).name
                        if not name:
                            continue
                        sanitized_attenuation.setdefault(str(point_uid), {}).setdefault(
                            str(role_key), {}
                        )[str(alias)] = name
            cleaned["attenuation_files"] = sanitized_attenuation

        return cleaned

    @classmethod
    def _measurement_data_payload(
        cls,
        *,
        h5f: h5py.File,
        config: Optional[Dict[str, Any]],
        specimen_info: Dict[str, Any],
        bundle_base: str,
        distance_mm: int,
        machine_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        cfg = config or {}
        study_id = cls._coerce_optional_int(
            h5f.attrs.get("matadorStudyId", cfg.get("matador_study_id"))
        )
        machine_id = cls._coerce_optional_int(
            h5f.attrs.get("matadorMachineId", cfg.get("matador_machine_id"))
        )
        user_id = cls._coerce_optional_int(
            h5f.attrs.get("matadorUserId", cfg.get("matador_user_id", h5f.attrs.get("operator_id")))
        )
        measurement_module_id = cls._coerce_optional_int(
            h5f.attrs.get(
                "matadorMeasurementModuleId",
                cfg.get("matador_measurement_module_id"),
            )
        )
        org_id = cls._coerce_optional_int(
            h5f.attrs.get("matadorOrganizationId", cfg.get("matador_organization_id"))
        )
        org_name = cls._as_text(
            h5f.attrs.get("matadorOrganizationName", cfg.get("matador_organization_name")),
            "",
        ).strip()
        org_country = cls._as_text(
            h5f.attrs.get("matadorOrganizationCountry", cfg.get("matador_organization_country")),
            "",
        ).strip()

        machine_name = cls._as_text(
            h5f.attrs.get("machine_name", cfg.get("machine_name")),
            "",
        ).strip()
        detector_size = machine_summary.get("detector_size")
        matrix_resolution = None
        if isinstance(detector_size, dict):
            width = cls._safe_int(detector_size.get("width"))
            height = cls._safe_int(detector_size.get("height"))
            if width and height:
                matrix_resolution = f"M{width}X{height}"

        pixel_size = None
        pixel_size_um = machine_summary.get("pixel_size_um")
        if isinstance(pixel_size_um, (list, tuple)) and pixel_size_um:
            pixel_size = cls._safe_int(pixel_size_um[0])
        elif pixel_size_um is not None:
            pixel_size = cls._safe_int(pixel_size_um)

        wavelength_angstrom = None
        detector_poni = cls._collect_detector_poni(h5f)
        for payload in detector_poni.values():
            text = cls._as_text(payload.get("poni_value"), "")
            for line in text.splitlines():
                if not line.strip().lower().startswith("wavelength:"):
                    continue
                value = cls._to_float(line.split(":", 1)[1].strip())
                if value is not None:
                    if value < 1e-6:
                        wavelength_angstrom = float(value) * 1e10
                    else:
                        wavelength_angstrom = float(value)
                    break
            if wavelength_angstrom is not None:
                break
        if wavelength_angstrom is None:
            wavelength_angstrom = cls._to_float(cfg.get("matador_wavelength_angstrom")) or 1.5406

        source = cls._as_text(cfg.get("matador_source"), "").strip()
        source_type = cls._as_text(cfg.get("matador_source_type"), "").strip()
        if not source_type:
            if abs(float(wavelength_angstrom) - 1.5406) < 0.05:
                source_type = "CU_K_ALPHA"
                if not source:
                    source = "Cu"
            elif abs(float(wavelength_angstrom) - 0.7093) < 0.05:
                source_type = "MO_K_ALPHA"
                if not source:
                    source = "Mo"

        detector_model = cls._as_text(cfg.get("matador_detector_model"), "").strip()
        if not detector_model:
            detector_type = cls._as_text(machine_summary.get("detector_type"), "").strip()
            if detector_type.lower() == "pixet" and matrix_resolution == "M256X256":
                detector_model = "ADVACAM MiniPix Timepix Standard"
            else:
                detector_model = detector_type

        acquisition_date = cls._as_text(h5f.attrs.get("acquisition_date"), "").strip()
        if acquisition_date:
            created_at = f"{acquisition_date}T00:00:00.000Z"
        else:
            created_at = cls._iso_utc_now()

        organization_payload: Dict[str, Any] = {}
        if org_id is not None:
            organization_payload["id"] = int(org_id)
        else:
            organization_payload["id"] = None
        if org_name:
            organization_payload["name"] = org_name
        if org_country:
            organization_payload["country"] = org_country

        return {
            "id": None,
            "distanceInMM": int(distance_mm),
            "study": {"id": study_id},
            "machineMeasur": {
                "id": machine_id,
                "machineName": machine_name,
                "wavelength": wavelength_angstrom,
                "pixelSize": pixel_size,
                "source": source or None,
                "sourceType": source_type or None,
                "matrixResolution": matrix_resolution,
                "detectorModel": detector_model or None,
                "organization": organization_payload,
                "createdAt": created_at,
                "updatedAt": created_at,
            },
            "user": {"id": user_id},
            "measurementName": bundle_base,
            "patient": {"id": specimen_info.get("patient_id")},
            "specimen": {"id": specimen_info.get("specimen_id")},
            "createdAt": created_at,
            "measurementM": {"id": measurement_module_id},
        }

    @classmethod
    def _build_metadata_json(
        cls,
        *,
        bundle_key: str,
        file_payloads: Dict[str, bytes],
    ) -> bytes:
        file_names = sorted(file_payloads.keys()) + ["metadata.json"]
        base_size = sum(len(payload) for payload in file_payloads.values())
        created_at = cls._iso_utc_now()

        previous_payload = b""
        while True:
            candidate = {
                "key": bundle_key,
                "fileCount": len(file_names),
                "totalSize": int(base_size + len(previous_payload)),
                "createdAt": created_at,
                "fileNames": file_names,
            }
            payload = json.dumps(candidate, indent=2).encode("utf-8")
            if len(payload) == len(previous_payload):
                return payload
            previous_payload = payload

    @classmethod
    def export_from_session_container(
        cls,
        session_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
        archive_folder: Optional[Path] = None,
        target_root: Optional[Path] = None,
    ) -> MatadorZipBundleSummary:
        """Export one Matador ZIP bundle folder from a session container."""
        source = Path(session_path)
        if not source.exists():
            raise FileNotFoundError(f"Session container not found: {source}")

        cfg = config or {}

        with h5py.File(source, "r") as h5f:
            specimen_text = cls._as_text(
                h5f.attrs.get("specimenId", h5f.attrs.get("sample_id")),
                "UNKNOWN",
            )
            patient_text = cls._as_text(h5f.attrs.get("patient_id"), "")
            specimen_info = cls._parse_specimen_patient(
                specimen_text=specimen_text,
                patient_text=patient_text,
            )

            state_payload = cls._load_state_payload(h5f)
            if not isinstance(state_payload, dict):
                state_payload = {}

            fallback_timestamps = cls._collect_fallback_timestamps(h5f)
            day_token = cls._resolve_day_token(
                acquisition_date=cls._as_text(h5f.attrs.get("acquisition_date"), ""),
                fallback_timestamps=fallback_timestamps,
            )
            distance_mm = cls._extract_distance_mm(
                h5f=h5f,
                config=cfg,
                day_token=day_token,
            )
            distance_token = f"{max(1, int(round(distance_mm / 10.0)))}cm"
            bundle_base = f"{specimen_info['bundle_key']}_{distance_token}"

            root = (
                Path(target_root)
                if target_root is not None
                else cls.resolve_old_format_root(
                    config=config,
                    archive_folder=archive_folder,
                )
            )
            root.mkdir(parents=True, exist_ok=True)
            export_dir = root / bundle_base
            export_dir.mkdir(parents=True, exist_ok=True)

            measurement_points, point_uid_by_index = cls._measurement_points_from_state_or_container(
                h5f=h5f,
                state_payload=state_payload,
            )
            active_aliases = state_payload.get("active_detectors_aliases")
            if not isinstance(active_aliases, list) or not active_aliases:
                active_aliases = sorted(
                    {
                        str(alias).upper()
                        for alias in cls._collect_detector_poni(h5f).keys()
                    }
                )

            calibration_group_hash = cls._as_text(
                state_payload.get("CALIBRATION_GROUP_HASH"),
                "",
            ).strip() or hashlib.md5(bundle_base.encode("utf-8")).hexdigest()[:16]

            raw_count, measurements_meta, raw_file_names, machine_summary = cls._export_regular_measurements(
                h5f=h5f,
                export_dir=export_dir,
                day_token=day_token,
                bundle_base=bundle_base,
                calibration_group_hash=calibration_group_hash,
                point_uid_by_index=point_uid_by_index,
            )
            before_attenuation_files = {
                path.name for path in export_dir.iterdir() if path.is_file()
            }
            attenuation_count, attenuation_files = cls._export_attenuation_files(
                h5f=h5f,
                sample_dir=export_dir,
                sample_base_with_distance=bundle_base,
                day_token=day_token,
                state_payload=state_payload,
                point_uid_by_session_index=point_uid_by_index,
            )
            raw_count += attenuation_count
            after_attenuation_files = {
                path.name for path in export_dir.iterdir() if path.is_file()
            }
            attenuation_file_names = sorted(after_attenuation_files - before_attenuation_files)
            raw_file_names.extend(attenuation_file_names)

            state_payload = {
                **state_payload,
                "measurement_points": measurement_points,
                "skipped_points": state_payload.get("skipped_points", []),
                "active_detectors_aliases": [str(alias).upper() for alias in active_aliases],
                "CALIBRATION_GROUP_HASH": calibration_group_hash,
                "detector_poni": cls._collect_detector_poni(h5f),
                "technical_aux": cls._collect_technical_aux(
                    h5f=h5f,
                    state_payload=state_payload,
                    day_token=day_token,
                ),
                "measurements_meta": {
                    key: value
                    for key, value in measurements_meta.items()
                    if Path(key).suffix.lower() in cls.MATRIX_FILE_EXTENSIONS
                },
                "attenuation_files": attenuation_files,
                "real_center": state_payload.get("real_center"),
                "pixel_to_mm_ratio": state_payload.get("pixel_to_mm_ratio"),
                "rotation_angle": state_payload.get("rotation_angle", 0),
                "crop_rect": state_payload.get("crop_rect"),
                "shapes": state_payload.get("shapes", []),
                "zone_points": state_payload.get("zone_points", []),
            }
            state_payload = cls._strip_machine_local_state(state_payload=state_payload)

            state_name = f"{bundle_base}_state.json"
            state_path = export_dir / state_name
            state_bytes = json.dumps(state_payload, indent=2).encode("utf-8")
            cls._write_bytes_if_changed(state_path, state_bytes)

            measurement_data_payload = cls._measurement_data_payload(
                h5f=h5f,
                config=cfg,
                specimen_info=specimen_info,
                bundle_base=bundle_base,
                distance_mm=distance_mm,
                machine_summary=machine_summary,
            )
            measurement_data_path = export_dir / "measurementData.json"
            measurement_data_bytes = json.dumps(measurement_data_payload, indent=2).encode("utf-8")
            cls._write_bytes_if_changed(measurement_data_path, measurement_data_bytes)

            manifest_inputs = {
                **{
                    file_name: (export_dir / file_name).read_bytes()
                    for file_name in sorted(set(raw_file_names))
                },
                state_name: state_bytes,
                "measurementData.json": measurement_data_bytes,
            }
            metadata_bytes = cls._build_metadata_json(
                bundle_key=str(specimen_info["bundle_key"]),
                file_payloads=manifest_inputs,
            )
            metadata_path = export_dir / "metadata.json"
            cls._write_bytes_if_changed(metadata_path, metadata_bytes)

        return MatadorZipBundleSummary(
            export_dir=export_dir,
            state_path=state_path,
            metadata_path=metadata_path,
            measurement_data_path=measurement_data_path,
            raw_file_count=raw_count,
        )
