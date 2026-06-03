"""Build Matador ZIP bundle payloads directly from session containers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Optional

import h5py

from difra.gui.matador_zip_bundle_measurements import MatadorZipBundleMeasurementMixin
from difra.gui.matador_zip_bundle_metadata import MatadorZipBundleMetadataMixin
from difra.gui.matador_zip_bundle_state import MatadorZipBundleStateMixin
from difra.gui.session_old_format_exporter import SessionOldFormatExporter


@dataclass
class MatadorZipBundleSummary:
    """Summary for one Matador ZIP bundle export."""

    export_dir: Path
    state_path: Path
    metadata_path: Path
    measurement_data_path: Path
    raw_file_count: int


class MatadorZipBundleExporter(
    MatadorZipBundleMetadataMixin,
    MatadorZipBundleMeasurementMixin,
    MatadorZipBundleStateMixin,
    SessionOldFormatExporter,
):
    """Create Matador ZIP bundle folders from a session container."""

    MATRIX_FILE_EXTENSIONS = {".txt", ".npy", ".tiff", ".tif", ".gfrm"}

    @classmethod
    def export_from_session_container(
        cls,
        session_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
        archive_folder: Optional[Path] = None,
        target_root: Optional[Path] = None,
    ) -> MatadorZipBundleSummary:
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

            measurement_points, point_uid_by_index = (
                cls._measurement_points_from_state_or_container(
                    h5f=h5f,
                    state_payload=state_payload,
                )
            )
            active_aliases = state_payload.get("active_detectors_aliases")
            if not isinstance(active_aliases, list) or not active_aliases:
                active_aliases = sorted(
                    {str(alias).upper() for alias in cls._collect_detector_poni(h5f)}
                )

            calibration_group_hash = (
                cls._as_text(state_payload.get("CALIBRATION_GROUP_HASH"), "").strip()
                or cls._default_calibration_group_hash(h5f, fallback=bundle_base)
            )

            raw_count, measurements_meta, raw_file_names, machine_summary = (
                cls._export_regular_measurements(
                    h5f=h5f,
                    export_dir=export_dir,
                    day_token=day_token,
                    bundle_base=bundle_base,
                    calibration_group_hash=calibration_group_hash,
                    point_uid_by_index=point_uid_by_index,
                )
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
            raw_file_names.extend(
                sorted(after_attenuation_files - before_attenuation_files)
            )

            state_payload = {
                **state_payload,
                "measurement_points": measurement_points,
                "skipped_points": state_payload.get("skipped_points", []),
                "active_detectors_aliases": [
                    str(alias).upper() for alias in active_aliases
                ],
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
            image_base64 = cls._resolve_embedded_image_base64(
                state_payload=state_payload,
                h5f=h5f,
            )
            state_payload = cls._strip_machine_local_state(state_payload=state_payload)
            if image_base64:
                state_payload["image_base64"] = image_base64

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
                calibration_group_hash=calibration_group_hash,
            )
            measurement_data_path = export_dir / "measurementData.json"
            measurement_data_bytes = json.dumps(
                measurement_data_payload, indent=2
            ).encode("utf-8")
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
