"""Headless pyFAI AgBH fitting helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np

from difra.gui.technical.pyfai_calibration_common import (
    DEFAULT_CALIBRANT,
    DEFAULT_WAVELENGTH_M,
    HeadlessPoniFitResult,
    _safe_token,
    _to_float,
    detector_size_px,
    parse_poni_parameters,
    pixel_size_m,
)
from difra.gui.technical.pyfai_calibration_io import load_calibration_array


def run_headless_agbh_fit(
    *,
    source_image: str | Path,
    detector_config: Mapping | None,
    distance_m: float,
    output_dir: str | Path,
    alias: str = "",
    center_px: tuple[float, float] | None = None,
    wavelength_m: float | None = None,
    calibrant: str = DEFAULT_CALIBRANT,
    first_visible_ring: int = 1,
    rings_to_show: int = 8,
    points_per_degree: float = 0.25,
    output_prefix: str | None = None,
    retry_count: int = 5,
) -> HeadlessPoniFitResult:
    image = np.asarray(load_calibration_array(source_image), dtype=np.float64)
    if image.ndim != 2:
        raise ValueError(f"Calibration image must be 2D, got shape {image.shape}")

    from pyFAI.calibrant import get_calibrant
    from pyFAI.control_points import ControlPoints
    from pyFAI.detectors._common import Detector
    from pyFAI.goniometer import SingleGeometry

    width, height = detector_size_px(detector_config)
    pixel1, pixel2 = pixel_size_m(detector_config)
    if center_px is None:
        center_px = (float(height) / 2.0, float(width) / 2.0)
    row_px, col_px = center_px
    wavelength = float(wavelength_m or DEFAULT_WAVELENGTH_M)

    cal = get_calibrant(str(calibrant or DEFAULT_CALIBRANT))
    try:
        cal.wavelength = wavelength
    except Exception:
        cal.set_wavelength(wavelength)
    detector = Detector(pixel1=pixel1, pixel2=pixel2, max_shape=(height, width))
    geometry = {
        "dist": float(distance_m),
        "poni1": float(row_px) * pixel1,
        "poni2": float(col_px) * pixel2,
        "rot1": 0.0,
        "rot2": 0.0,
        "rot3": 0.0,
        "wavelength": wavelength,
        "detector": detector,
    }

    max_ring = max(1, int(first_visible_ring) + max(1, int(rings_to_show)) - 1)
    first_zero_based = max(0, int(first_visible_ring) - 1)
    last_zero_based = first_zero_based + max(1, int(rings_to_show)) - 1

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    alias_token = _safe_token(alias, "detector")
    if output_prefix:
        prefix = _safe_token(output_prefix, alias_token)
        npt_path = output_root / f"{prefix}.npt"
        poni_path = output_root / f"{prefix}.poni"
    else:
        stem = _safe_token(Path(str(source_image)).stem)
        npt_path = output_root / f"{stem}_{alias_token}_headless_fit.npt"
        poni_path = output_root / f"{stem}_{alias_token}_headless_fit.poni"

    best = None
    attempts = max(1, int(retry_count or 1))
    for attempt in range(attempts):
        single_geometry = SingleGeometry(
            label=str(alias or "detector"),
            image=image,
            calibrant=cal,
            detector=detector,
            geometry=dict(geometry),
        )
        extracted = single_geometry.extract_cp(
            max_rings=max_ring,
            pts_per_deg=float(points_per_degree),
        )

        filtered = ControlPoints(calibrant=cal)
        for row, col, ring in extracted.getList():
            ring_i = int(ring)
            if first_zero_based <= ring_i <= last_zero_based:
                filtered.append([(float(row), float(col))], ring_i)

        data = np.asarray(filtered.getList(), dtype=np.float64)
        if data.size == 0:
            continue

        refinement = single_geometry.geometry_refinement
        refinement.data = data
        chi2 = None
        refined = False
        if data.shape[0] >= 3:
            chi2 = float(
                refinement.refine3(
                    fix=["wavelength", "rot1", "rot2", "rot3"],
                )
            )
            refined = True

        attempt_poni_path = poni_path.with_name(
            f"{poni_path.stem}_attempt_{attempt}{poni_path.suffix}"
        )
        if attempt_poni_path.exists():
            attempt_poni_path.unlink()
        refinement.save(str(attempt_poni_path))
        poni_text = attempt_poni_path.read_text(encoding="utf-8")
        score = float(chi2) if chi2 is not None else float("inf")
        candidate = (
            score,
            -int(data.shape[0]),
            filtered,
            poni_text,
            int(data.shape[0]),
            bool(refined),
            chi2,
            attempt_poni_path,
        )
        if best is None or candidate[:2] < best[:2]:
            best = candidate

    if best is None:
        raise ValueError("pyFAI did not extract any control points")

    (
        _score,
        _neg_points,
        filtered,
        poni_text,
        point_count,
        refined,
        chi2,
        best_attempt_path,
    ) = best
    filtered.save(str(npt_path))
    if poni_path.exists():
        poni_path.unlink()
    poni_path.write_text(poni_text, encoding="utf-8")
    for attempt_path in output_root.glob(
        f"{poni_path.stem}_attempt_*{poni_path.suffix}"
    ):
        if attempt_path != best_attempt_path:
            try:
                attempt_path.unlink()
            except OSError:
                pass
    try:
        best_attempt_path.unlink()
    except OSError:
        pass
    return HeadlessPoniFitResult(
        poni_path=poni_path,
        poni_text=poni_text,
        npt_path=npt_path,
        extracted_points=point_count,
        refined=refined,
        chi2=chi2,
    )


def is_headless_agbh_fit_plausible(
    fit_result: HeadlessPoniFitResult,
    *,
    seed_poni_text: str,
    detector_config: Mapping | None,
    min_points: int = 12,
    max_distance_change_fraction: float = 0.35,
    max_center_shift_px: float = 64.0,
) -> bool:
    if fit_result.extracted_points < int(min_points):
        return False

    seed = parse_poni_parameters(seed_poni_text)
    fitted = parse_poni_parameters(fit_result.poni_text)
    seed_distance = _to_float(seed.get("Distance"))
    fit_distance = _to_float(fitted.get("Distance"))
    if seed_distance is None or fit_distance is None or seed_distance <= 0.0:
        return False
    distance_fraction = abs(fit_distance - seed_distance) / seed_distance
    if distance_fraction > float(max_distance_change_fraction):
        return False

    pixel1, pixel2 = pixel_size_m(detector_config)
    seed_poni1 = _to_float(seed.get("Poni1"))
    seed_poni2 = _to_float(seed.get("Poni2"))
    fit_poni1 = _to_float(fitted.get("Poni1"))
    fit_poni2 = _to_float(fitted.get("Poni2"))
    if None in (seed_poni1, seed_poni2, fit_poni1, fit_poni2):
        return False
    row_shift = abs(float(fit_poni1) - float(seed_poni1)) / pixel1
    col_shift = abs(float(fit_poni2) - float(seed_poni2)) / pixel2
    return max(row_shift, col_shift) <= float(max_center_shift_px)
