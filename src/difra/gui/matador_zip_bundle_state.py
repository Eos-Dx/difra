from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List

import h5py


class MatadorZipBundleStateMixin:
    """State cleanup and embedded-image helpers for Matador ZIP bundles."""

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
    def _resolve_embedded_image_base64(
        cls,
        *,
        state_payload: Dict[str, Any],
        h5f: h5py.File,
    ) -> str:
        payload = cls._as_text(
            state_payload.get("image_base64") or state_payload.get("image_b64"),
            "",
        ).strip()
        if payload:
            if "," in payload and "base64" in payload[:40].lower():
                payload = payload.split(",", 1)[1]
            return payload

        image_ref = cls._as_text(state_payload.get("image"), "").strip()
        candidate_sources: List[Path] = []
        if image_ref:
            candidate_sources.append(Path(image_ref))
            image_ref_posix = image_ref.replace("\\", "/")
            if image_ref_posix != image_ref:
                candidate_sources.append(Path(image_ref_posix))

        for source_path in candidate_sources:
            try:
                if source_path.exists() and source_path.is_file():
                    return base64.b64encode(source_path.read_bytes()).decode("ascii")
            except Exception:
                continue

        image_bytes = cls._extract_image_bytes_from_container(h5f)
        if image_bytes:
            return base64.b64encode(image_bytes).decode("ascii")
        return ""
