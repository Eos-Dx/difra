from __future__ import annotations

from difra.gui.main_window_ext.technical.poni_center_preview import (
    resolve_overlay_zone,
    resolve_preview_limits,
)


def test_overlay_zone_keeps_secondary_off_detector_region_visible():
    zone = resolve_overlay_zone(
        {
            "row_target_px": 128,
            "row_tolerance_px": 13,
            "col_gt_px": 256,
        },
        width_px=256,
        height_px=256,
    )

    assert zone is not None
    x0, y0, zone_w, zone_h = zone
    assert x0 >= 256
    assert x0 + zone_w > 256
    assert y0 < 128 < y0 + zone_h


def test_preview_limits_expand_to_include_off_detector_center_and_zone():
    zone = (256.0, 115.0, 32.0, 26.0)
    center = {"row_px": 128.0, "col_px": 280.0}

    x_min, x_max, y_min, y_max = resolve_preview_limits(
        width_px=256,
        height_px=256,
        zone=zone,
        center=center,
    )

    assert x_min <= 0.0
    assert x_max > 280.0
    assert y_min <= 0.0
    assert y_max >= 256.0
