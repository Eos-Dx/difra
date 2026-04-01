"""Preview helpers for visualizing allowed PONI center regions."""

from __future__ import annotations


def _as_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_overlay_zone(rule: dict, width_px: int, height_px: int):
    """Return (x, y, w, h) for allowed center zone from JSON rule."""
    if not isinstance(rule, dict):
        return None

    row_target = _as_float(rule.get("row_target_px"))
    row_tol_px = _as_float(rule.get("row_tolerance_px"))
    if row_tol_px is None:
        row_tol_percent = _as_float(rule.get("row_tolerance_percent"))
        if row_tol_percent is not None:
            row_tol_px = (float(height_px) * float(row_tol_percent)) / 100.0
    if row_target is None:
        row_target = float(height_px) / 2.0
    if row_tol_px is None:
        row_tol_px = 0.0

    y_min = row_target - row_tol_px
    y_max = row_target + row_tol_px

    col_target = _as_float(rule.get("col_target_px"))
    col_tol_px = _as_float(rule.get("col_tolerance_px"))
    col_min_px = _as_float(rule.get("col_min_px"))
    col_max_px = _as_float(rule.get("col_max_px"))
    col_gt_px = _as_float(rule.get("col_gt_px"))
    col_lt_px = _as_float(rule.get("col_lt_px"))
    preview_pad_px = _as_float(rule.get("preview_pad_px"))
    if preview_pad_px is None:
        preview_pad_px = max(float(width_px) * 0.15, 24.0)

    if col_target is not None and col_tol_px is not None:
        x_min = col_target - col_tol_px
        x_max = col_target + col_tol_px
    else:
        x_min = col_min_px if col_min_px is not None else 0.0
        x_max = col_max_px if col_max_px is not None else float(width_px)
        if col_gt_px is not None:
            x_min = max(x_min, col_gt_px)
            if col_max_px is None and col_lt_px is None:
                x_max = max(x_max, float(col_gt_px) + float(preview_pad_px))
        if col_lt_px is not None:
            x_max = min(x_max, col_lt_px)
            if col_min_px is None and col_gt_px is None:
                x_min = min(x_min, float(col_lt_px) - float(preview_pad_px))

    if x_max <= x_min or y_max <= y_min:
        return None
    return (x_min, y_min, x_max - x_min, y_max - y_min)


def resolve_preview_limits(
    *,
    width_px: int,
    height_px: int,
    zone,
    center,
):
    """Return axis limits that keep off-detector zones/centers visible."""
    try:
        width = float(width_px)
        height = float(height_px)
    except Exception:
        width = 256.0
        height = 256.0

    x_min = 0.0
    x_max = width
    y_min = 0.0
    y_max = height

    if zone is not None:
        x0, y0, zone_w, zone_h = zone
        x_min = min(x_min, float(x0))
        x_max = max(x_max, float(x0) + float(zone_w))
        y_min = min(y_min, float(y0))
        y_max = max(y_max, float(y0) + float(zone_h))

    if isinstance(center, dict):
        x_min = min(x_min, float(center.get("col_px", 0.0)))
        x_max = max(x_max, float(center.get("col_px", 0.0)))
        y_min = min(y_min, float(center.get("row_px", 0.0)))
        y_max = max(y_max, float(center.get("row_px", 0.0)))

    pad = max(4.0, 0.03 * max(width, height))
    if x_min < 0.0:
        x_min -= pad
    if x_max > width:
        x_max += pad
    if y_min < 0.0:
        y_min -= pad
    if y_max > height:
        y_max += pad
    return (x_min, x_max, y_min, y_max)
