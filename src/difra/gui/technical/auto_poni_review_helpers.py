from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def rings_to_show_for_alias(rings_to_show, alias: str) -> int:
    alias_key = str(alias or "").strip().upper()
    if isinstance(rings_to_show, dict):
        for key in (alias, alias_key):
            try:
                count = int(rings_to_show.get(key))
            except (TypeError, ValueError):
                continue
            if count > 0:
                return count
    try:
        return max(1, int(rings_to_show))
    except (TypeError, ValueError):
        return 3


def ring_positions_deg(poni_text: str, first_ring: int, count: int):
    from difra.gui.technical.pyfai_calibration import (
        AGBH_D_SPACING_A,
        DEFAULT_WAVELENGTH_M,
        parse_poni_parameters,
        ring_two_theta_rad,
    )

    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    positions = []
    start = max(1, int(first_ring or 1))
    stop = min(len(AGBH_D_SPACING_A), start + max(1, int(count or 1)) - 1)
    for ring_index in range(start, stop + 1):
        two_theta = ring_two_theta_rad(
            wavelength_m=wavelength_m,
            d_spacing_a=AGBH_D_SPACING_A[ring_index - 1],
        )
        if two_theta is None:
            continue
        positions.append((ring_index, float(np.degrees(two_theta))))
    return positions


def integrate_with_poni(review, data):
    try:
        import pyFAI

        poni_path = Path(getattr(review, "poni_path", "") or "")
        if not poni_path.exists():
            return None, None
        integrator = pyFAI.load(str(poni_path))
        cake = integrator.integrate2d(
            data,
            100,
            180,
            unit="2th_deg",
            method=("full", "histogram", "python"),
        )
        curve = integrator.integrate1d(
            data,
            100,
            unit="2th_deg",
            method=("full", "histogram", "python"),
        )
        return cake, curve
    except Exception:
        logger.warning("Failed to compute Auto PONI integrations", exc_info=True)
        return None, None


def command_with_npt(command, npt_path: Path):
    clean = []
    skip = False
    for part in list(command or []):
        if skip:
            skip = False
            continue
        if part == "-n":
            skip = True
            continue
        clean.append(part)
    if not clean:
        return []
    return [*clean[:-1], "-n", str(npt_path), clean[-1]]


def npt_path_from_command(command):
    parts = list(command or [])
    for index, part in enumerate(parts[:-1]):
        if part == "-n":
            return Path(str(parts[index + 1]))
    return None


def alias_file_token(alias: str) -> str:
    token = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in str(alias or "").strip()
    )
    return token or "detector"


def snap_to_peak(data, col: float, row: float, radius: int = 6):
    arr = np.asarray(data, dtype=float)
    height, width = arr.shape
    col_i = int(round(float(col)))
    row_i = int(round(float(row)))
    col_i = min(max(col_i, 0), width - 1)
    row_i = min(max(row_i, 0), height - 1)
    x0 = max(0, col_i - radius)
    x1 = min(width, col_i + radius + 1)
    y0 = max(0, row_i - radius)
    y1 = min(height, row_i + radius + 1)
    window = arr[y0:y1, x0:x1]
    if window.size == 0 or not np.isfinite(window).any():
        return float(col_i), float(row_i)
    safe = np.nan_to_num(window, nan=-np.inf)
    local_row, local_col = np.unravel_index(int(np.argmax(safe)), safe.shape)
    return float(x0 + local_col), float(y0 + local_row)


def points_by_ring(entries, *, manual_ring_index: int | None = None, manual_points=None):
    indexed_points = {}
    for entry in entries:
        points = list(entry.get("points", []) or [])
        if points:
            indexed_points[int(entry.get("ring_index"))] = points
    if manual_ring_index is not None and manual_points:
        ring_points = (
            list(manual_points) + list(indexed_points.get(int(manual_ring_index), []))
        )
        deduped = []
        seen = set()
        for col, row in ring_points:
            key = (round(float(col), 3), round(float(row), 3))
            if key in seen:
                continue
            seen.add(key)
            deduped.append((float(col), float(row)))
        indexed_points[int(manual_ring_index)] = deduped
    return indexed_points
