import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class ZoneMeasurementsProfilePreviewMixin:
    def _extract_profile_from_measurement(
        self,
        measurement_ref: str,
        alias: str = "",
        npt: int = 200,
    ):
        value = self._measurement_ref_to_filename(measurement_ref)
        if not value:
            return None
        try:
            from difra.gui.technical.capture import _load_measurement_array
            from difra.gui.technical.analysis_compat import (
                initialize_azimuthal_integrator_poni_text,
            )
            from difra.gui.technical.widgets import MeasurementHistoryWidget

            data = _load_measurement_array(value)
            arr = np.asarray(data, dtype=float)
            if arr.ndim != 2 or arr.size == 0:
                return None

            poni_text = None
            if value.startswith("h5ref://"):
                poni_text = MeasurementHistoryWidget._resolve_poni_text_from_h5ref(
                    value,
                    alias=alias,
                )

            if not poni_text:
                ponis = getattr(self, "ponis", {}) or {}
                for candidate in MeasurementHistoryWidget._expand_technical_alias_candidates(alias):
                    if candidate in ponis and str(ponis.get(candidate) or "").strip():
                        poni_text = str(ponis.get(candidate) or "").strip()
                        break

            mask = None
            masks = getattr(self, "masks", {}) or {}
            for candidate in MeasurementHistoryWidget._expand_technical_alias_candidates(alias):
                if candidate in masks:
                    mask = masks.get(candidate)
                    break

            if poni_text:
                try:
                    ai = initialize_azimuthal_integrator_poni_text(poni_text)
                    result = ai.integrate1d(
                        arr,
                        max(int(npt), 2),
                        unit="q_nm^-1",
                        error_model="azimuthal",
                        mask=mask,
                    )
                    radial = np.asarray(result.radial, dtype=float).reshape(-1)
                    intensity = np.asarray(result.intensity, dtype=float).reshape(-1)
                    finite = np.isfinite(radial) & np.isfinite(intensity)
                    radial = radial[finite]
                    intensity = intensity[finite]
                    if radial.size >= 2 and intensity.size >= 2:
                        return {
                            "q_values": radial,
                            "intensity": intensity,
                        }
                except Exception:
                    logger.debug(
                        "Failed to build azimuthal profile preview from '%s'",
                        value,
                        exc_info=True,
                    )

            return {
                "q_values": None,
                "intensity": np.nanmean(arr, axis=0),
            }
        except Exception:
            logger.debug(
                "Failed to extract detector profile from '%s'",
                value,
                exc_info=True,
            )
            return None

    def _update_profile_previews_from_result_files(
        self,
        result_files: dict,
        point_uid: Optional[str] = None,
    ):
        updater = getattr(self, "update_detector_profile_preview", None)
        if not callable(updater):
            return
        uid = str(point_uid or "").strip() or None
        for alias, measurement_ref in (result_files or {}).items():
            try:
                profile = self._extract_profile_from_measurement(
                    measurement_ref,
                    alias=alias,
                )
            except TypeError:
                profile = self._extract_profile_from_measurement(measurement_ref)
            if profile is None:
                continue
            try:
                if uid is None:
                    updater(alias, profile)
                else:
                    try:
                        updater(alias, profile, uid)
                    except TypeError:
                        updater(alias, profile)
            except Exception:
                logger.debug(
                    "Suppressed exception in process_results_mixin.py",
                    exc_info=True,
                )
