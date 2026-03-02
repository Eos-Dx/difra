"""Compatibility layer for azimuthal integration and mask helpers.

Prefer the original `xrdanalysis` helpers when available, but fall back to a
small local implementation based on `pyFAI` so standalone DiFRA can run
without the old monorepo package.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

_BACKEND = "unavailable"
_XRD_CREATE_MASK = None
_XRD_INIT_DF = None
_XRD_INIT_PONI = None
_PYFAI = None
_AzimuthalIntegrator = None

try:
    from xrdanalysis.data_processing.azimuthal_integration import (
        initialize_azimuthal_integrator_df as _XRD_INIT_DF,
        initialize_azimuthal_integrator_poni_text as _XRD_INIT_PONI,
    )
    from xrdanalysis.data_processing.utility_functions import (
        create_mask as _XRD_CREATE_MASK,
    )

    _BACKEND = "xrdanalysis"
except Exception:
    try:
        import pyFAI as _PYFAI

        try:
            from pyFAI.integrator.azimuthal import AzimuthalIntegrator as _AzimuthalIntegrator
        except Exception:
            from pyFAI.azimuthalIntegrator import AzimuthalIntegrator as _AzimuthalIntegrator

        _BACKEND = "pyfai"
    except Exception:
        _BACKEND = "unavailable"


def backend_name() -> str:
    return _BACKEND


def create_mask(values, size=None):
    if _XRD_CREATE_MASK is not None:
        return _XRD_CREATE_MASK(values, size=size)

    arr = np.asarray(values)

    if size is None:
        if arr.ndim == 0:
            return None
        return arr.astype(bool)

    try:
        width = int(size[0])
        height = int(size[1])
    except Exception:
        width = height = 0

    if width <= 0 or height <= 0:
        if arr.ndim == 0:
            return None
        return arr.astype(bool)

    if arr.ndim == 2 and arr.shape == (height, width):
        return arr.astype(bool)

    mask = np.zeros((height, width), dtype=bool)
    if arr.size == 0:
        return mask

    coords = arr
    if coords.ndim == 1 and coords.size % 2 == 0:
        coords = coords.reshape(-1, 2)

    if coords.ndim == 2 and coords.shape[1] >= 2:
        for coord in coords:
            try:
                x = int(coord[0])
                y = int(coord[1])
            except Exception:
                continue
            if 0 <= x < width and 0 <= y < height:
                mask[y, x] = True

    return mask


def initialize_azimuthal_integrator_poni_text(poni_text: str):
    if _XRD_INIT_PONI is not None:
        return _XRD_INIT_PONI(poni_text)
    if _PYFAI is None:
        raise RuntimeError("No azimuthal integration backend is available")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".poni",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(str(poni_text or ""))
            tmp_path = Path(handle.name)
        return _PYFAI.load(str(tmp_path))
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def initialize_azimuthal_integrator_df(
    pixel_size,
    center_column,
    center_row,
    wavelength,
    sample_distance_mm,
):
    if _XRD_INIT_DF is not None:
        return _XRD_INIT_DF(
            pixel_size,
            center_column,
            center_row,
            wavelength,
            sample_distance_mm,
        )
    if _AzimuthalIntegrator is None:
        raise RuntimeError("No azimuthal integration backend is available")

    ai = _AzimuthalIntegrator()

    pixel_size_m = float(pixel_size)
    pixel_size_um = pixel_size_m * 1_000_000.0

    wavelength_value = float(wavelength)
    if wavelength_value > 1e-6:
        wavelength_value *= 1e-10

    ai.setFit2D(
        float(sample_distance_mm),
        float(center_column),
        float(center_row),
        pixelX=float(pixel_size_um),
        pixelY=float(pixel_size_um),
        wavelength=float(wavelength_value),
    )
    return ai
