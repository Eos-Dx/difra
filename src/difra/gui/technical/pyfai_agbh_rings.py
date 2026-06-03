"""AgBH ring overlay, control-point, and clicked-point refinement helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from difra.gui.technical.pyfai_calibration_common import (
    AGBH_D_SPACING_A,
    DEFAULT_CALIBRANT,
    DEFAULT_WAVELENGTH_M,
    _format_float,
    build_seed_poni_text,
    detector_size_px,
    parse_poni_parameters,
    pixel_size_m,
)


def ring_two_theta_rad(*, wavelength_m: float, d_spacing_a: float) -> float | None:
    wavelength_a = float(wavelength_m) * 1e10
    d_value = float(d_spacing_a)
    if wavelength_a <= 0.0 or d_value <= 0.0:
        return None
    ratio = wavelength_a / (2.0 * d_value)
    if ratio <= 0.0 or ratio >= 1.0:
        return None
    import math

    return 2.0 * math.asin(ratio)


def build_agbh_ring_overlays(
    *,
    poni_text: str,
    detector_config: Mapping | None,
    first_visible_ring: int,
    rings_to_show: int = 8,
) -> list[dict]:
    params = parse_poni_parameters(poni_text)
    distance_m = float(params.get("Distance", 0.0))
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    pixel1, pixel2 = pixel_size_m(detector_config)
    if distance_m <= 0.0 or pixel1 <= 0.0 or pixel2 <= 0.0:
        return []

    center_row_px = float(params.get("Poni1", 0.0)) / pixel1
    center_col_px = float(params.get("Poni2", 0.0)) / pixel2
    first_ring = max(1, int(first_visible_ring or 1))
    max_ring = min(len(AGBH_D_SPACING_A), first_ring + max(1, int(rings_to_show)) - 1)

    import math

    overlays = []
    pixel_mean = (pixel1 + pixel2) / 2.0
    for ring_index in range(first_ring, max_ring + 1):
        two_theta = ring_two_theta_rad(
            wavelength_m=wavelength_m,
            d_spacing_a=AGBH_D_SPACING_A[ring_index - 1],
        )
        if two_theta is None:
            continue
        radius_m = distance_m * math.tan(two_theta)
        radius_px = radius_m / pixel_mean
        if not math.isfinite(radius_px) or radius_px <= 0.0:
            continue
        overlays.append(
            {
                "ring_index": ring_index,
                "d_spacing_a": AGBH_D_SPACING_A[ring_index - 1],
                "two_theta_rad": two_theta,
                "center_row_px": center_row_px,
                "center_col_px": center_col_px,
                "radius_px": radius_px,
            }
        )
    return overlays


def write_agbh_control_points_npt(
    *,
    poni_text: str,
    detector_config: Mapping | None,
    output_path: str | Path,
    first_visible_ring: int,
    rings_to_show: int = 4,
    calibrant: str = DEFAULT_CALIBRANT,
    points_per_ring: int = 24,
) -> Path:
    width, height = detector_size_px(detector_config)
    overlays = build_agbh_ring_overlays(
        poni_text=poni_text,
        detector_config=detector_config,
        first_visible_ring=first_visible_ring,
        rings_to_show=rings_to_show,
    )
    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))

    import math

    lines = [
        "# set of control point used by pyFAI to calibrate the geometry of a scattering experiment",
        "# angles are in radians, wavelength in meter and positions in pixels",
        f"calibrant: {calibrant} {wavelength_m}",
        f"wavelength: {wavelength_m}",
        "dspacing:" + " ".join(str(value) for value in AGBH_D_SPACING_A),
    ]
    group_index = 0
    point_count = max(8, int(points_per_ring))
    for overlay in overlays:
        ring_index = int(overlay["ring_index"])
        radius = float(overlay["radius_px"])
        center_col = float(overlay["center_col_px"])
        center_row = float(overlay["center_row_px"])
        points = []
        for idx in range(point_count):
            angle = 2.0 * math.pi * float(idx) / float(point_count)
            col = center_col + radius * math.cos(angle)
            row = center_row + radius * math.sin(angle)
            if 0.0 <= col < float(width) and 0.0 <= row < float(height):
                points.append((col, row))
        if len(points) < 3:
            continue
        lines.extend(
            [
                "",
                f"New group of points: {group_index}",
                f"2theta: {float(overlay['two_theta_rad'])}",
                f"ring: {ring_index - 1}",
            ]
        )
        for col, row in points:
            lines.append(f"point: x={_format_float(col)} y={_format_float(row)}")
        group_index += 1

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def write_agbh_clicked_points_npt(
    *,
    poni_text: str,
    output_path: str | Path,
    ring_index: int,
    points_col_row: Sequence[tuple[float, float]],
    calibrant: str = DEFAULT_CALIBRANT,
) -> Path:
    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    ring = max(1, int(ring_index or 1))
    two_theta = ring_two_theta_rad(
        wavelength_m=wavelength_m,
        d_spacing_a=AGBH_D_SPACING_A[ring - 1],
    )
    lines = [
        "# set of control point used by pyFAI to calibrate the geometry of a scattering experiment",
        "# angles are in radians, wavelength in meter and positions in pixels",
        f"calibrant: {calibrant} {wavelength_m}",
        f"wavelength: {wavelength_m}",
        "dspacing:" + " ".join(str(value) for value in AGBH_D_SPACING_A),
        "",
        "New group of points: 0",
        f"2theta: {_format_float(two_theta or 0.0)}",
        f"ring: {ring - 1}",
    ]
    for col, row in points_col_row:
        lines.append(f"point: x={_format_float(col)} y={_format_float(row)}")

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def write_agbh_points_by_ring_npt(
    *,
    poni_text: str,
    output_path: str | Path,
    points_by_ring: Mapping[int, Sequence[tuple[float, float]]],
    calibrant: str = DEFAULT_CALIBRANT,
) -> Path:
    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    lines = [
        "# set of control point used by pyFAI to calibrate the geometry of a scattering experiment",
        "# angles are in radians, wavelength in meter and positions in pixels",
        f"calibrant: {calibrant} {wavelength_m}",
        f"wavelength: {wavelength_m}",
        "dspacing:" + " ".join(str(value) for value in AGBH_D_SPACING_A),
    ]
    group_index = 0
    for ring in sorted(int(key) for key in points_by_ring.keys()):
        points = list(points_by_ring.get(ring, []) or [])
        if not points:
            continue
        d_spacing_index = min(len(AGBH_D_SPACING_A) - 1, max(0, ring - 1))
        two_theta = ring_two_theta_rad(
            wavelength_m=wavelength_m,
            d_spacing_a=AGBH_D_SPACING_A[d_spacing_index],
        )
        lines.extend(
            [
                "",
                f"New group of points: {group_index}",
                f"2theta: {_format_float(two_theta or 0.0)}",
                f"ring: {max(0, ring - 1)}",
            ]
        )
        for col, row in points:
            lines.append(f"point: x={_format_float(col)} y={_format_float(row)}")
        group_index += 1

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def refine_poni_from_clicked_ring_points(
    *,
    poni_text: str,
    detector_config: Mapping | None,
    ring_index: int,
    points_col_row: Sequence[tuple[float, float]],
    alias: str = "",
) -> str:
    points = np.asarray(points_col_row, dtype=float)
    if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2:
        raise ValueError("At least 3 clicked points are required")
    ring = max(1, int(ring_index or 1))
    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    two_theta = ring_two_theta_rad(
        wavelength_m=wavelength_m,
        d_spacing_a=AGBH_D_SPACING_A[ring - 1],
    )
    if two_theta is None:
        raise ValueError(f"Ring {ring} is not available at current wavelength")

    x = points[:, 0]
    y = points[:, 1]
    matrix = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    rhs = x * x + y * y
    center_col, center_row, offset = np.linalg.lstsq(matrix, rhs, rcond=None)[0]
    radius_sq = float(offset + center_col * center_col + center_row * center_row)
    if radius_sq <= 0.0:
        raise ValueError("Clicked points do not define a valid ring")

    import math

    pixel1, pixel2 = pixel_size_m(detector_config)
    radius_px = math.sqrt(radius_sq)
    distance_m = radius_px * ((pixel1 + pixel2) / 2.0) / math.tan(two_theta)
    if not math.isfinite(distance_m) or distance_m <= 0.0:
        raise ValueError("Clicked points produced invalid distance")
    return build_seed_poni_text(
        detector_config=detector_config,
        distance_m=distance_m,
        alias=alias,
        existing_poni_text=poni_text,
        wavelength_m=wavelength_m,
        center_px=(float(center_row), float(center_col)),
    )
