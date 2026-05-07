from pathlib import Path

import numpy as np

from difra.gui.technical.pyfai_calibration import (
    build_agbh_ring_overlays,
    build_pyfai_calib2_command,
    build_seed_poni_text,
    energy_kev_to_wavelength_m,
    export_calibration_image_for_pyfai,
    HeadlessPoniFitResult,
    is_headless_agbh_fit_plausible,
    normalized_auto_poni_config,
    parse_poni_parameters,
    prepare_agbh_calib2_review,
    pyfai_detector_name,
    refine_poni_from_clicked_ring_points,
    write_agbh_control_points_npt,
    write_agbh_clicked_points_npt,
    write_agbh_points_by_ring_npt,
    write_pyfai_calib2_launcher,
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
        first_visible_ring=1,
    )

    assert review.image_path.exists()
    assert review.poni_path.exists()
    assert review.command[0] == "pyfai-calib2"
    assert "-c" in review.command
    assert "AgBh" in review.command
    assert "--dist" in review.command
    assert "-n" in review.command
    assert "0.17" in review.command
    assert str(review.image_path) == review.command[-1]


def test_prepare_agbh_calib2_review_accepts_stable_output_prefix(tmp_path: Path):
    npy = tmp_path / "agbh_PRIMARY.npy"
    np.save(npy, np.ones((8, 8), dtype=np.float32))

    review = prepare_agbh_calib2_review(
        source_image=npy,
        detector_config=_detector_config(),
        distance_m=0.17,
        alias="PRIMARY",
        output_dir=tmp_path / "autopony",
        first_visible_ring=1,
        output_prefix="PRIMARY",
    )

    assert review.image_path.name == "PRIMARY_pyfai.tif"
    assert review.poni_path.name == "PRIMARY.poni"
    assert str(tmp_path / "autopony" / "PRIMARY.npt") in review.command


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
    assert "-D Maxipix" in joined
    assert "--poni1 0.007020022187721548" in joined
    assert "--fix-rot1" in joined
    assert "--fix-rot2" in joined
    assert "--fix-rot3" in joined
    assert "--no-tilt" in joined
    assert command[-1] == "/tmp/agbh.tif"


def test_build_pyfai_calib2_command_can_unlock_rotations():
    poni = "\n".join(
        [
            "Distance: 0.17",
            "Poni1: 0.007",
            "Poni2: 0.001",
            "Rot1: 0.1",
            "Rot2: 0.2",
            "Rot3: 0.3",
            "Wavelength: 1.5406e-10",
        ]
    )

    command = build_pyfai_calib2_command(
        image_path="/tmp/agbh.tif",
        poni_text=poni,
        detector_config=_detector_config(),
        fix_rotations=False,
    )

    assert "--rot1" in command
    assert "--fix-rot1" not in command
    assert "--fix-rot2" not in command
    assert "--fix-rot3" not in command
    assert "--no-tilt" not in command


def test_build_pyfai_calib2_command_uses_custom_difra_detector_for_50um():
    detector_config = {
        "size": {"width": 256, "height": 256},
        "pixel_size_um": [50, 50],
    }

    assert pyfai_detector_name(detector_config) == "DIFRA-256-50UM"


def test_write_pyfai_launcher_registers_custom_detector(tmp_path):
    launcher = write_pyfai_calib2_launcher(
        output_dir=tmp_path,
        command=["pyfai-calib2", "-D", "DIFRA-256-50UM", "image.tif"],
        launcher_stem="run_primary",
    )

    text = launcher.read_text(encoding="utf-8")
    assert "DIFRA-256-50UM" in text
    assert "max_shape=(256, 256)" in text


def test_write_agbh_control_points_npt_uses_zero_based_ring_ids(tmp_path):
    poni = "\n".join(
        [
            "Distance: 0.17",
            "Poni1: 0.0064",
            "Poni2: 0.0005",
            "Wavelength: 1.542092020313436e-10",
        ]
    )
    detector_config = {
        "size": {"width": 256, "height": 256},
        "pixel_size_um": [50, 50],
    }

    npt = write_agbh_control_points_npt(
        poni_text=poni,
        detector_config=detector_config,
        output_path=tmp_path / "seed.npt",
        first_visible_ring=1,
        rings_to_show=2,
    )

    text = npt.read_text(encoding="utf-8")
    assert "ring: 0" in text
    assert "point: x=" in text


def test_headless_fit_plausibility_rejects_sparse_bad_geometry(tmp_path):
    seed = "\n".join(
        [
            "Distance: 0.02",
            "Poni1: 0.0064",
            "Poni2: 0.016",
        ]
    )
    fitted = "\n".join(
        [
            "Distance: 0.032",
            "Poni1: 0.0059",
            "Poni2: 0.0183",
        ]
    )
    result = HeadlessPoniFitResult(
        poni_path=tmp_path / "fit.poni",
        poni_text=fitted,
        npt_path=tmp_path / "fit.npt",
        extracted_points=6,
        refined=True,
        chi2=1e-7,
    )

    assert not is_headless_agbh_fit_plausible(
        result,
        seed_poni_text=seed,
        detector_config={
            "size": {"width": 256, "height": 256},
            "pixel_size_um": [50, 50],
        },
    )


def test_clicked_ring_points_write_npt_and_refine_poni(tmp_path):
    detector_config = {
        "size": {"width": 256, "height": 256},
        "pixel_size_um": [50, 50],
    }
    poni = build_seed_poni_text(
        detector_config=detector_config,
        distance_m=0.02,
        alias="PRIMARY",
        wavelength_m=energy_kev_to_wavelength_m(8.04),
        center_px=(128.0, 10.0),
        created_at="now",
    )
    points = [(10.0, 64.0), (74.0, 128.0), (10.0, 192.0), (-54.0, 128.0)]

    npt = write_agbh_clicked_points_npt(
        poni_text=poni,
        output_path=tmp_path / "clicked.npt",
        ring_index=2,
        points_col_row=points,
    )
    refined = refine_poni_from_clicked_ring_points(
        poni_text=poni,
        detector_config=detector_config,
        ring_index=2,
        points_col_row=points,
        alias="PRIMARY",
    )
    parsed = parse_poni_parameters(refined)

    text = npt.read_text(encoding="utf-8")
    assert "ring: 1" in text
    assert "point: x=10 y=64" in text
    assert abs(parsed["Poni1"] - 0.0064) < 1e-9
    assert abs(parsed["Poni2"] - 0.0005) < 1e-9
    assert parsed["Distance"] > 0.0


def test_points_by_ring_npt_writes_auto_points_for_multiple_rings(tmp_path):
    poni = "\n".join(
        [
            "Distance: 0.02",
            "Poni1: 0.0064",
            "Poni2: 0.0005",
            "Wavelength: 1.542092020313436e-10",
        ]
    )

    npt = write_agbh_points_by_ring_npt(
        poni_text=poni,
        output_path=tmp_path / "auto.npt",
        points_by_ring={
            1: [(10.0, 20.0), (30.0, 40.0)],
            3: [(50.0, 60.0)],
        },
    )

    text = npt.read_text(encoding="utf-8")
    assert "ring: 0" in text
    assert "ring: 2" in text
    assert "point: x=10 y=20" in text
    assert "point: x=50 y=60" in text


def test_normalized_auto_poni_config_defaults_visible_rings():
    cfg = normalized_auto_poni_config({})

    assert cfg["calibrant"] == "AgBh"
    assert cfg["energy_kev"] == 8.04
    assert cfg["rings_to_show"] == 3
    assert cfg["first_visible_ring_by_alias"]["PRIMARY"] == 2
    assert cfg["first_visible_ring_by_alias"]["SECONDARY"] == 5
    assert cfg["first_visible_ring_by_distance_cm"]["2"]["PRIMARY"] == 2
    assert cfg["first_visible_ring_by_distance_cm"]["2"]["SECONDARY"] == 5
    assert cfg["first_visible_ring_by_distance_cm"]["17"]["PRIMARY"] == 1
    assert cfg["rings_to_search_by_distance_cm"]["2"]["PRIMARY"] == 5
    assert cfg["rings_to_search_by_distance_cm"]["2"]["SECONDARY"] == 4
    assert cfg["rings_to_search_by_distance_cm"]["17"]["PRIMARY"] == 3
    assert cfg["rings_to_search_by_distance_cm"]["17"]["SECONDARY"] == 3


def test_normalized_auto_poni_config_accepts_distance_ring_overrides():
    cfg = normalized_auto_poni_config(
        {
            "auto_poni_calibration": {
                "first_visible_ring_by_distance_cm": {
                    "17": {
                        "PRIMARY": 2,
                        "SECONDARY": 4,
                    }
                }
            }
        }
    )

    assert cfg["first_visible_ring_by_distance_cm"]["17"]["PRIMARY"] == 2
    assert cfg["first_visible_ring_by_distance_cm"]["17"]["SECONDARY"] == 4


def test_normalized_auto_poni_config_reads_energy_and_converts_to_wavelength():
    cfg = normalized_auto_poni_config({"xray_energy_kev": 8.04})

    assert cfg["energy_kev"] == 8.04
    assert round(energy_kev_to_wavelength_m(cfg["energy_kev"]) * 1e10, 4) == 1.5421


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
