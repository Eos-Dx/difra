from pathlib import Path

import h5py
import numpy as np

from difra.gui.session_container_viewer import (
    calculate_absorption_image,
    collect_absorption_records,
    collect_analytical,
    collect_images,
    collect_measurements,
    integrate_profile,
    read_container_summary,
)


def _create_viewer_fixture(path: Path) -> Path:
    with h5py.File(path, "w") as h5f:
        h5f.attrs["specimenId"] = "SPEC_001"
        h5f.attrs["session_id"] = "session_001"
        meas = h5f.require_group("/entry/measurements/pt_001/meas_000000001")
        meas.attrs["measurement_status"] = "completed"
        det = meas.require_group("det_primary")
        det.attrs["detector_alias"] = "PRIMARY"
        det.attrs["detector_id"] = "DET_PRIMARY"
        det.attrs["integration_time_ms"] = 1000.0
        det.attrs["n_frames"] = 1
        det.create_dataset("processed_signal", data=np.arange(16, dtype=float).reshape(4, 4))

        h5f.create_dataset("/entry/images/img_001/data", data=np.zeros((4, 4, 3), dtype=np.uint8))

        ana = h5f.require_group("/entry/analytical_measurements/ana_000000001")
        ana.attrs["analysis_type"] = "attenuation"
        ana.attrs["analysis_role"] = "i0"
        ana_det = ana.require_group("det_primary")
        ana_det.create_dataset("processed_signal", data=np.ones((4, 4), dtype=float))

        ana_i = h5f.require_group("/entry/analytical_measurements/ana_000000002")
        ana_i.attrs["analysis_type"] = "attenuation"
        ana_i.attrs["analysis_role"] = "i"
        ana_i.attrs["point_ids"] = np.asarray(["pt_001"], dtype=h5py.string_dtype())
        ana_i_det = ana_i.require_group("det_primary")
        ana_i_det.attrs["detector_alias"] = "PRIMARY"
        ana_i_det.create_dataset("processed_signal", data=np.full((4, 4), 0.5, dtype=float))
    return path


def test_session_container_viewer_collects_core_records(tmp_path):
    container = _create_viewer_fixture(tmp_path / "session_test.nxs.h5")

    summary = read_container_summary(container)
    measurements = collect_measurements(container)
    images = collect_images(container)
    analytical = collect_analytical(container)
    absorption = collect_absorption_records(container)

    assert summary["measurement_count"] == 1
    assert summary["detector_count"] == 1
    assert summary["image_count"] == 1
    assert summary["analytical_count"] == 2
    assert measurements[0].dataset_path.endswith("/det_primary/processed_signal")
    assert measurements[0].alias == "PRIMARY"
    assert images[0].shape == "4x4x3"
    assert analytical[0].analysis_type == "attenuation"
    assert analytical[0].detectors == "det_primary"
    assert len(absorption) == 1
    assert absorption[0].point == "pt_001"
    assert absorption[0].mean == "0.693147"


def test_session_container_viewer_fallback_profile_integrates_2d_data():
    x, y, mode = integrate_profile(np.ones((8, 8), dtype=float), npt=4)

    assert mode == "radial pixels"
    assert len(x) == len(y)
    assert len(x) >= 2
    assert np.all(np.isfinite(y))


def test_calculate_absorption_image_uses_minus_log_ratio():
    i0 = np.ones((2, 2), dtype=float)
    i = np.full((2, 2), 0.25, dtype=float)

    absorption = calculate_absorption_image(i, i0)

    assert absorption.shape == (2, 2)
    assert np.allclose(absorption, -np.log(0.25))
