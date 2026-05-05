from pathlib import Path

import numpy as np

from difra.gui.technical.pyfai_calibration import (
    build_agbh_ring_overlays,
    build_pyfai_calib2_command,
    build_seed_poni_text,
    export_calibration_image_for_pyfai,
    normalized_auto_poni_config,
    parse_poni_parameters,
    prepare_agbh_calib2_review,
)


def _detector_config():
    return {
        "alias": "PRIMARY",
        "id": "MiniPIX G08-W0299",
        "size": {"width": 256, "height": 256},
        "pixel_size_um": [55, 55],
    }


def test_build_seed_poni_text_preserves_existing_center_and_sets_distance():
    existing = "\n".join(
        [
            "poni_version: 2.1",
            'Detector_config: {"pixel1":5.5e-05,"pixel2":5.5e-05,"max_shape":[256,256],"orientation":3}',
            "Distance: 0.02",
            "Poni1: 0.00702",
            "Poni2: 0.00086",
            "Rot1: 0.01",
            "Rot2: 0.02",
            "Rot3: 0.03",
            "Wavelength: 1.5406e-10",
        ]
    )

    poni = build_seed_poni_text(
        detector_config=_detector_config(),
        distance_m=0.17,
        alias="PRIMARY",
        existing_poni_text=existing,
        created_at="now",
    )
    parsed = parse_poni_parameters(poni)

    assert parsed["Distance"] == 0.17
    assert parsed["Poni1"] == 0.00702
    assert parsed["Poni2"] == 0.00086
    assert parsed["Rot1"] == 0.01
    assert parsed["Rot2"] == 0.02
    assert parsed["Rot3"] == 0.03
    assert parsed["Wavelength"] == 1.5406e-10
    detector_payload = parsed["Detector_config"]
    assert detector_payload["max_shape"] == [256, 256]
    assert detector_payload["pixel1"] == 55e-6


def test_build_seed_poni_text_preserves_nondefault_existing_wavelength():
    existing = "\n".join(
        [
            "Distance: 0.47",
            "Poni1: 0.034",
            "Poni2: 0.005",
            "Wavelength: 5.6382082052387574e-11",
        ]
    )

    poni = build_seed_poni_text(
        detector_config=_detector_config(),
        distance_m=0.47,
        alias="PRIMARY",
        existing_poni_text=existing,
    )

    assert parse_poni_parameters(poni)["Wavelength"] == 5.638208205238757e-11


def test_export_calibration_image_for_pyfai_writes_tiff_from_npy(tmp_path: Path):
    npy = tmp_path / "agbh_PRIMARY.npy"
    np.save(npy, np.arange(16, dtype=np.float32).reshape(4, 4))

    exported = export_calibration_image_for_pyfai(npy, alias="PRIMARY")

    assert exported.suffix == ".tif"
    assert exported.exists()


def test_prepare_agbh_calib2_review_writes_seed_and_command(tmp_path: Path):
    npy = tmp_path / "agbh_PRIMARY.npy"
    np.save(npy, np.ones((8, 8), dtype=np.float32))

    review = prepare_agbh_calib2_review(
        source_image=npy,
        detector_config=_detector_config(),
        distance_m=0.17,
        alias="PRIMARY",
        output_dir=tmp_path / "pyfai",
    )

    assert review.image_path.exists()
    assert review.poni_path.exists()
    assert review.command[0] == "pyfai-calib2"
    assert "-c" in review.command
    assert "AgBh" in review.command
    assert "--dist" in review.command
    assert "0.17" in review.command
    assert str(review.image_path) == review.command[-1]


def test_build_pyfai_calib2_command_uses_poni_geometry():
    poni = "\n".join(
        [
            "Distance: 0.17239906043601042",
            "Poni1: 0.007020022187721548",
            "Poni2: 0.0008600585417045749",
            "Rot1: 0.0",
            "Rot2: 0.0",
            "Rot3: 0.0",
            "Wavelength: 1.5406e-10",
        ]
    )

    command = build_pyfai_calib2_command(
        image_path="/tmp/agbh.tif",
        poni_text=poni,
        detector_config=_detector_config(),
    )

    joined = " ".join(command)
    assert "-w 1.5406" in joined
    assert "-p 55,55" in joined
    assert "--poni1 0.007020022187721548" in joined
    assert command[-1] == "/tmp/agbh.tif"


def test_normalized_auto_poni_config_defaults_visible_rings():
    cfg = normalized_auto_poni_config({})

    assert cfg["calibrant"] == "AgBh"
    assert cfg["first_visible_ring_by_alias"]["PRIMARY"] == 3
    assert cfg["first_visible_ring_by_alias"]["SECONDARY"] == 5


def test_build_agbh_ring_overlays_starts_at_first_visible_ring():
    poni = "\n".join(
        [
            "Distance: 0.47137986964295986",
            "Poni1: 0.0344496495672326",
            "Poni2: 0.005190990511805168",
            "Wavelength: 5.6382082052387574e-11",
        ]
    )
    detector_config = {
        "size": {"width": 768, "height": 512},
        "pixel_size_um": [135, 135],
    }

    overlays = build_agbh_ring_overlays(
        poni_text=poni,
        detector_config=detector_config,
        first_visible_ring=3,
        rings_to_show=4,
    )

    assert [item["ring_index"] for item in overlays] == [3, 4, 5, 6]
    assert overlays[0]["center_row_px"] > 250
    assert overlays[0]["center_col_px"] > 38
    assert overlays[0]["radius_px"] > 100
