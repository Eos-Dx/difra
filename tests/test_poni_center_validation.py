import json

from difra.gui.main_window_ext.technical.poni_center_validation import (
    parse_poni_center_px,
    validate_poni_centers,
)


def _poni_text(*, poni1: float, poni2: float, width: int = 512, height: int = 256) -> str:
    return "\n".join(
        [
            "poni_version: 2.1",
            (
                "Detector_config: "
                + json.dumps(
                    {
                        "pixel1": 5.5e-05,
                        "pixel2": 5.5e-05,
                        "max_shape": [height, width],
                        "orientation": 3,
                    }
                )
            ),
            f"Poni1: {poni1}",
            f"Poni2: {poni2}",
            "Distance: 0.17",
        ]
    )


def test_parse_poni_center_px_parses_geometry_from_detector_config():
    center = parse_poni_center_px(
        _poni_text(poni1=0.006765, poni2=0.00055, width=512, height=256)
    )
    assert center is not None
    assert center["row_px"] == 123.0
    assert center["col_px"] == 10.0
    assert center["width_px"] == 512.0
    assert center["height_px"] == 256.0


def test_validate_poni_centers_passes_for_primary_and_secondary_rules():
    cfg = {
        "enabled": True,
        "defaults": {"row_tolerance_percent": 5.0},
        "detectors": {
            "PRIMARY": {
                "row_target_px": 123,
                "row_tolerance_px": 10,
                "col_target_px": 10,
                "col_tolerance_px": 10,
                "col_max_px": 20,
            },
            "SECONDARY": {
                "row_target_px": 123,
                "row_tolerance_px": 10,
                "col_gt_px": 256,
            },
        },
    }
    poni_data = {
        "PRIMARY": _poni_text(poni1=0.006765, poni2=0.00055, width=512, height=256),
        "SECONDARY": _poni_text(poni1=0.006765, poni2=0.0150, width=512, height=256),
    }

    errors, warnings = validate_poni_centers(
        poni_text_by_alias=poni_data,
        detector_sizes_by_alias={"PRIMARY": (512, 256), "SECONDARY": (512, 256)},
        validation_config=cfg,
    )
    assert errors == []
    assert warnings == []


def test_validate_poni_centers_fails_when_secondary_not_right_of_256():
    cfg = {
        "enabled": True,
        "detectors": {"SECONDARY": {"row_target_px": 123, "row_tolerance_px": 10, "col_gt_px": 256}},
    }
    poni_data = {
        "SECONDARY": _poni_text(poni1=0.006765, poni2=0.000825, width=512, height=256)
    }

    errors, _warnings = validate_poni_centers(
        poni_text_by_alias=poni_data,
        detector_sizes_by_alias={"SECONDARY": (512, 256)},
        validation_config=cfg,
    )
    assert errors
    assert "col > 256.00" in errors[0]


def test_validate_poni_centers_is_noop_when_disabled():
    errors, warnings = validate_poni_centers(
        poni_text_by_alias={"PRIMARY": _poni_text(poni1=0.006, poni2=0.001)},
        detector_sizes_by_alias={"PRIMARY": (256, 256)},
        validation_config={"enabled": False, "detectors": {"PRIMARY": {"col_gt_px": 999}}},
    )
    assert errors == []
    assert warnings == []
