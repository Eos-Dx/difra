"""Azimuthal integration helpers for daily reports."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np

from difra.gui.daily_report_common import DEFAULT_POINTS


def integrate_detector_signal(
    data: np.ndarray,
    poni_text: str,
    *,
    npt: int = DEFAULT_POINTS,
    q_range: Optional[Tuple[float, float]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or not poni_text.strip():
        return np.asarray([]), np.asarray([])
    try:
        from difra.gui.technical.analysis_compat import (
            initialize_azimuthal_integrator_poni_text,
        )

        ai = initialize_azimuthal_integrator_poni_text(poni_text)
        kwargs: Dict[str, Any] = {}
        if q_range is not None:
            kwargs["radial_range"] = (float(q_range[0]), float(q_range[1]))
        result = ai.integrate1d(
            arr,
            max(int(npt), 2),
            unit="q_nm^-1",
            error_model="azimuthal",
            **kwargs,
        )
        q = np.asarray(result.radial, dtype=float).reshape(-1)
        intensity = np.asarray(result.intensity, dtype=float).reshape(-1)
        finite = np.isfinite(q) & np.isfinite(intensity)
        return q[finite], intensity[finite]
    except Exception:
        return np.asarray([]), np.asarray([])


def _resample_range(
    q: np.ndarray,
    intensity: np.ndarray,
    q_range: Tuple[float, float],
    *,
    points: int,
) -> Tuple[np.ndarray, np.ndarray]:
    q = np.asarray(q, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    finite = np.isfinite(q) & np.isfinite(intensity)
    q = q[finite]
    intensity = intensity[finite]
    if q.size < 2:
        return np.asarray([]), np.asarray([])
    order = np.argsort(q)
    q = q[order]
    intensity = intensity[order]
    if q[0] > float(q_range[0]) or q[-1] < float(q_range[1]):
        return np.asarray([]), np.asarray([])
    mask = (q >= float(q_range[0])) & (q <= float(q_range[1]))
    if np.count_nonzero(mask) < 2:
        return np.asarray([]), np.asarray([])
    target_q = np.linspace(float(q_range[0]), float(q_range[1]), int(points))
    target_i = np.interp(target_q, q[mask], intensity[mask])
    return target_q, target_i


def _integrated_range_is_complete(
    q: np.ndarray,
    intensity: np.ndarray,
    q_range: Tuple[float, float],
    *,
    points: int,
) -> bool:
    q = np.asarray(q, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    if q.size != int(points) or intensity.size != int(points):
        return False
    if not np.all(np.isfinite(q)) or not np.all(np.isfinite(intensity)):
        return False
    q_min = float(np.nanmin(q))
    q_max = float(np.nanmax(q))
    return q_min >= float(q_range[0]) - 1e-6 and q_max <= float(q_range[1]) + 1e-6


def _integrated_signal_fraction(intensity: np.ndarray) -> float:
    values = np.asarray(intensity, dtype=float).reshape(-1)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0
    scale = float(np.nanmax(np.abs(finite)))
    if scale <= 0.0:
        return 0.0
    threshold = max(scale * 1e-6, 1e-12)
    return float(np.count_nonzero(np.abs(finite) > threshold) / finite.size)
