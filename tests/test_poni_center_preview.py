from __future__ import annotations

from difra.gui.main_window_ext.technical.poni_center_preview import (
    normalize_zone,
    resolve_overlay_zone,
    resolve_preview_limits,
    rule_with_zone,
)
from difra.gui.main_window_ext.technical.poni_center_validation import (
    evaluate_poni_centers,
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


def test_normalize_zone_flips_negative_dimensions():
    assert normalize_zone((20, 30, -5, -8)) == (15.0, 22.0, 5.0, 8.0)


def test_rule_with_zone_replaces_column_target_style_with_explicit_bounds():
    updated = rule_with_zone(
        {
            "row_target_px": 128,
            "row_tolerance_px": 13,
            "col_target_px": 10,
            "col_tolerance_px": 10,
            "col_max_px": 20,
        },
        (4, 100, 22, 40),
    )

    assert updated["row_target_px"] == 120.0
    assert updated["row_tolerance_px"] == 20.0
    assert updated["col_min_px"] == 4.0
    assert updated["col_max_px"] == 26.0
    assert "col_target_px" not in updated
    assert "col_tolerance_px" not in updated


def test_evaluate_poni_centers_reports_detector_statuses_with_tolerant_edge_check():
    poni_text = "\n".join(
        [
            "poni_version: 2.1",
            'Detector_config: {"pixel1": 1.0e-04, "pixel2": 1.0e-04, "max_shape": [256, 256], "orientation": 3}',
            "Poni1: 0.0128",
            "Poni2: 0.0256",
        ]
    )
    results = evaluate_poni_centers(
        poni_text_by_alias={"SECONDARY": poni_text},
        detector_sizes_by_alias={"SECONDARY": (256, 256)},
        validation_config={
            "enabled": True,
            "detectors": {
                "SECONDARY": {
                    "row_target_px": 128,
                    "row_tolerance_px": 13,
                    "col_gt_px": 256,
                }
            },
        },
    )

    assert len(results) == 1
    assert results[0]["alias"] == "SECONDARY"
    assert results[0]["in_zone"] is True
    assert results[0]["errors"] == []
    assert any("col > 256.00" in item for item in results[0]["rule_summary"])
