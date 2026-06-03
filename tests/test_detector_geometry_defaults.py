from __future__ import annotations

from difra.gui.technical.pyfai_calibration_common import (
    DEFAULT_DETECTOR_PIXEL_SIZE_UM,
    DEFAULT_DETECTOR_SIZE_PX,
    build_seed_poni_text,
    detector_size_px,
    parse_poni_parameters,
    pixel_size_m,
    pixel_size_um,
)


def test_default_detector_geometry_is_xena_55um_256_square():
    assert DEFAULT_DETECTOR_PIXEL_SIZE_UM == (55.0, 55.0)
    assert DEFAULT_DETECTOR_SIZE_PX == (256, 256)
    assert detector_size_px({}) == (256, 256)
    assert pixel_size_um({}) == (55.0, 55.0)
    assert pixel_size_m({}) == (55e-6, 55e-6)


def test_seed_poni_uses_55um_when_detector_config_has_no_pixel_size():
    poni_text = build_seed_poni_text(detector_config={}, distance_m=0.17)
    parsed = parse_poni_parameters(poni_text)
    detector_config = parsed["Detector_config"]

    assert detector_config["pixel1"] == 55e-6
    assert detector_config["pixel2"] == 55e-6
    assert detector_config["max_shape"] == [256, 256]


def test_pixel_size_um_preserves_explicit_detector_config():
    assert pixel_size_um({"pixel_size_um": [100, 110]}) == (100.0, 110.0)
    assert pixel_size_m({"pixel_size_um": [100, 110]}) == (100e-6, 110e-6)
