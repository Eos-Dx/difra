"""Plotting math helpers for the session container viewer."""

from __future__ import annotations

import numpy as np

from difra.gui.session_container_viewer_data import (
    MeasurementRecord,
    _detector_is_secondary,
)


def integrate_profile(
    data: np.ndarray,
    *,
    poni_text: str = "",
    npt: int = 200,
    unit: str = "q_nm^-1",
) -> tuple[np.ndarray, np.ndarray, str]:
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2:
        return np.arange(arr.size), arr.reshape(-1), "flattened"
    if poni_text.strip():
        try:
            from difra.gui.technical.analysis_compat import (
                initialize_azimuthal_integrator_poni_text,
            )

            ai = initialize_azimuthal_integrator_poni_text(poni_text)
            result = ai.integrate1d(
                arr, max(int(npt), 2), unit=unit, error_model="azimuthal"
            )
            x = np.asarray(result.radial, dtype=float).reshape(-1)
            y = np.asarray(result.intensity, dtype=float).reshape(-1)
            finite = np.isfinite(x) & np.isfinite(y)
            return x[finite], y[finite], "pyFAI q"
        except Exception:
            pass
    yy, xx = np.indices(arr.shape)
    center_y = (arr.shape[0] - 1) / 2.0
    center_x = (arr.shape[1] - 1) / 2.0
    radius = np.sqrt((yy - center_y) ** 2 + (xx - center_x) ** 2)
    bins = np.linspace(0, float(radius.max()), max(int(npt), 2) + 1)
    which = np.digitize(radius.ravel(), bins) - 1
    values = arr.ravel()
    x_values = []
    y_values = []
    for idx in range(len(bins) - 1):
        mask = which == idx
        if not np.any(mask):
            continue
        x_values.append((bins[idx] + bins[idx + 1]) / 2.0)
        y_values.append(float(np.nanmean(values[mask])))
    return np.asarray(x_values), np.asarray(y_values), "radial pixels"


def _detector_npt(record: MeasurementRecord) -> int:
    return (
        100
        if _detector_is_secondary(record.detector, record.alias, record.detector_id)
        else 200
    )
