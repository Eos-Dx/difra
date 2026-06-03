from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import h5py
import numpy as np


class MatadorZipBundleMetadataMixin:
    """Matador ZIP specimen, distance, manifest, and measurementData helpers."""

    @staticmethod
    def _iso_utc_now() -> str:
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

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
        direct_value = cls._to_float(
            h5f.attrs.get("distance_cm", h5f.attrs.get("distanceCm"))
        )
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
    def _measurement_data_payload(
        cls,
        *,
        h5f: h5py.File,
        config: Optional[Dict[str, Any]],
        specimen_info: Dict[str, Any],
        bundle_base: str,
        distance_mm: int,
        machine_summary: Dict[str, Any],
        calibration_group_hash: str,
    ) -> Dict[str, Any]:
        cfg = config or {}
        study_id = cls._coerce_optional_int(
            h5f.attrs.get("matadorStudyId", cfg.get("matador_study_id"))
        )
        machine_id = cls._coerce_optional_int(
            h5f.attrs.get("matadorMachineId", cfg.get("matador_machine_id"))
        )
        user_id = cls._coerce_optional_int(
            h5f.attrs.get(
                "matadorUserId", cfg.get("matador_user_id", h5f.attrs.get("operator_id"))
            )
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
            h5f.attrs.get(
                "matadorOrganizationName", cfg.get("matador_organization_name")
            ),
            "",
        ).strip()
        org_country = cls._as_text(
            h5f.attrs.get(
                "matadorOrganizationCountry", cfg.get("matador_organization_country")
            ),
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
                    wavelength_angstrom = float(value) * 1e10 if value < 1e-6 else float(value)
                    break
            if wavelength_angstrom is not None:
                break
        if wavelength_angstrom is None:
            wavelength_angstrom = (
                cls._to_float(cfg.get("matador_wavelength_angstrom")) or 1.5406
            )

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
        created_at = f"{acquisition_date}T00:00:00.000Z" if acquisition_date else cls._iso_utc_now()

        organization_payload: Dict[str, Any] = {"id": int(org_id) if org_id is not None else None}
        if org_name:
            organization_payload["name"] = org_name
        if org_country:
            organization_payload["country"] = org_country

        return {
            "id": None,
            "name": bundle_base,
            "distanceInMM": int(distance_mm),
            "processingStatus": "REQUEST_FOR_VALIDATION",
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
            "CALIBRATION_GROUP_HASH": calibration_group_hash,
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
