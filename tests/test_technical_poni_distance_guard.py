from pathlib import Path
import os
import shutil

import h5py
import numpy as np
import pytest

from container.v0_2 import technical_container
from difra.gui.main_window_ext.technical.h5_management_mixin import H5ManagementMixin
from difra.gui.main_window_ext.technical.poni_agbh_peak_qc import evaluate_peak_alignment


def _poni(distance_m: float) -> str:
    return (
        "# test poni\n"
        'Detector_config: {"pixel1": 5.5e-05, "pixel2": 5.5e-05, "max_shape": [256, 256]}\n'
        f"Distance: {distance_m}\n"
        "Poni1: 0.001\n"
        "Poni2: 0.002\n"
        "Rot1: 0\n"
        "Rot2: 0\n"
        "Rot3: 0\n"
        "Wavelength: 1.542e-10\n"
    )


class _SyncHarness(H5ManagementMixin):
    def __init__(self, path: Path):
        self.config = {
            "detectors": [
                {"id": "det_primary", "alias": "PRIMARY"},
            ],
        }
        self._active_technical_container_path = str(path)
        self.ponis = {"PRIMARY": "# ignored stale memory cache"}
        self.poni_file_path = path.parent / "stale_17cm.poni"
        self.poni_file_path.write_text(_poni(0.158), encoding="utf-8")
        self.poni_files = {
            "PRIMARY": {
                "path": str(self.poni_file_path),
                "name": self.poni_file_path.name,
            }
        }
        self.logged = []
        self.states = []

    def _active_technical_container_path_obj(self):
        return Path(self._active_technical_container_path)

    def _get_active_detector_ids(self):
        return ["det_primary"]

    def _set_container_state(self, path, *, state, reason):
        self.states.append((Path(path), state, reason))

    def _sync_container_state(self, path, *, reason):
        self.states.append((Path(path), "synced", reason))

    def _log_technical_event(self, message):
        self.logged.append(str(message))


class _LockHarness(H5ManagementMixin):
    def __init__(self):
        self.config = {
            "detectors": [
                {"id": "det_primary", "alias": "PRIMARY"},
            ],
        }


def _real_archive_config():
    return {
        "detectors": [
            {"id": "MiniPIX G08-W0299", "alias": "PRIMARY"},
            {"id": "MiniPIX G05-W0339", "alias": "SECONDARY"},
        ],
        "poni_distance_validation": {
            "nominal_ranges_cm": [
                {
                    "nominal_cm": 2.0,
                    "min_cm": 1.8,
                    "max_cm": 3.0,
                    "match_tolerance_cm": 0.35,
                },
                {
                    "nominal_cm": 17.0,
                    "min_cm": 16.5,
                    "max_cm": 18.0,
                    "match_tolerance_cm": 0.85,
                },
            ]
        },
        "poni_metadata_validation": {
            "enabled": True,
            "expected_energy_keV": 8.04,
            "energy_tolerance_keV": 0.1,
            "expected_pixel_size_um": [55, 55],
            "pixel_tolerance_um": 0.25,
            "expected_shape": [256, 256],
        },
        "agbh_peak_qc": {
            "enabled": True,
            "show_dialog": False,
            "calibrant": "AgBh",
            "npt": 300,
            "peak_window_nm_inv": 0.2,
            "peak_shift_warning_nm_inv": 0.25,
            "min_checked_peaks": 4,
            "q_ranges_by_alias": {
                "PRIMARY": [1.0, 18.0],
                "SECONDARY": [4.5, 23.0],
            },
        },
    }


def _real_archive_technical_path(pattern: str) -> Path | None:
    archive_root = Path(
        os.environ.get(
            "DIFRA_TEST_ARCHIVE_TECHNICAL",
            "/Users/sad/dev/Data/difra/archive/technical",
        )
    )
    if not archive_root.exists():
        return None
    return next(iter(sorted(archive_root.glob(pattern))), None)


def test_real_archive_technical_containers_validate_on_copies(tmp_path):
    source_paths = [
        _real_archive_technical_path(
            "technical_fe2c66f4975b42d1_2cm_20260514_nxs_jennifer_nicell_20260514_144718/"
            "technical_fe2c66f4975b42d1_2cm_20260514.nxs.h5"
        )
        or _real_archive_technical_path("*2cm*/*.nxs.h5"),
        _real_archive_technical_path(
            "technical_d4ac6be5aa0b4e23_17cm_20260528_nxs_jennifer_nicell_20260528_104130/"
            "technical_d4ac6be5aa0b4e23_17cm_20260528.nxs.h5"
        )
        or _real_archive_technical_path("*17cm*/*.nxs.h5"),
    ]
    if any(path is None or not path.exists() for path in source_paths):
        pytest.skip("real DiFRA technical archive is not available")

    harness = _LockHarness()
    harness.config = _real_archive_config()

    for source_path in source_paths:
        copied_path = tmp_path / source_path.name
        shutil.copy2(source_path, copied_path)

        assert harness._embedded_poni_distance_validation_errors(copied_path) == []
        assert harness._embedded_poni_metadata_validation_errors(copied_path) == []
        peak_warnings = harness._embedded_agbh_peak_qc_warnings(copied_path)
        assert isinstance(peak_warnings, list)


def test_poni_distance_validation_catches_stale_geometry():
    errors = H5ManagementMixin._poni_distance_validation_errors(
        {"PRIMARY": (_poni(0.158), "primary_17cm.poni")},
        {"PRIMARY": 2.0},
    )

    assert errors
    assert "15.800 cm" in errors[0]
    assert "2.000 cm" in errors[0]


def test_poni_distance_validation_allows_configured_nominal_2cm_range():
    errors = H5ManagementMixin._poni_distance_validation_errors(
        {"PRIMARY": (_poni(0.0242), "primary_real_2p42cm.poni")},
        {"PRIMARY": 2.0},
        validation_config={
            "nominal_ranges_cm": [
                {
                    "nominal_cm": 2.0,
                    "min_cm": 1.8,
                    "max_cm": 3.0,
                    "match_tolerance_cm": 0.35,
                }
            ]
        },
    )

    assert errors == []


def test_poni_distance_validation_rejects_17cm_poni_for_nominal_2cm_range():
    errors = H5ManagementMixin._poni_distance_validation_errors(
        {"PRIMARY": (_poni(0.17), "primary_17cm.poni")},
        {"PRIMARY": 2.0},
        validation_config={
            "nominal_ranges_cm": [
                {
                    "nominal_cm": 2.0,
                    "min_cm": 1.8,
                    "max_cm": 3.0,
                    "match_tolerance_cm": 0.35,
                }
            ]
        },
    )

    assert errors
    assert "outside allowed range" in errors[0]


def test_agbh_peak_alignment_warns_when_late_peaks_drift():
    theoretical_q = np.asarray([1.0, 2.0, 3.0, 4.0, 5.0], dtype=float)
    q = np.linspace(0.5, 5.5, 500)
    intensity = np.zeros_like(q)
    observed = [1.0, 2.0, 3.0, 4.35, 5.45]
    for peak_q in observed:
        intensity += np.exp(-((q - peak_q) ** 2) / (2 * 0.025**2))

    result = evaluate_peak_alignment(
        q,
        intensity,
        theoretical_q,
        peak_shift_warning_nm_inv=0.18,
        peak_window_nm_inv=0.5,
        min_checked_peaks=4,
    )

    assert result["ok"] is False
    assert result["bad"] >= 2
    assert result["max_shift"] > 0.3


def test_embedded_poni_distance_validation_catches_bad_locked_candidate(tmp_path):
    _container_id, file_path = technical_container.create_technical_container(
        folder=tmp_path,
        distance_cm=17.0,
    )
    path = Path(file_path)
    with h5py.File(path, "a") as h5f:
        det_group = h5f.require_group("/entry/technical/tech_evt_000/det_primary")
        det_group.attrs["detector_alias"] = "PRIMARY"
        det_group.attrs["detector_id"] = "det_primary"
        det_group.attrs["distance_cm"] = 17.0
        poni_group = h5f.require_group("/entry/technical/poni")
        ds = poni_group.create_dataset("poni_primary", data=_poni(0.158))
        ds.attrs["detector_alias"] = "PRIMARY"
        ds.attrs["detector_id"] = "det_primary"

    errors = _LockHarness()._embedded_poni_distance_validation_errors(path)

    assert errors
    assert "15.800 cm" in errors[0]
    assert "17.000 cm" in errors[0]


def test_sync_blocks_stale_poni_for_new_distance(tmp_path):
    _container_id, file_path = technical_container.create_technical_container(
        folder=tmp_path,
        distance_cm=2.0,
    )
    path = Path(file_path)
    harness = _SyncHarness(path)

    ok = harness._write_runtime_rows_to_active_container(
        path,
        [
            {
                "index": 0,
                "alias": "PRIMARY",
                "technical_type": "AGBH",
                "is_primary": True,
                "data": np.ones((4, 4), dtype=np.float32),
                "source_ref": "",
                "source_path": "",
                "row_id": "row_1",
                "metadata": {},
            }
        ],
        show_errors=False,
    )

    assert ok is False
    assert any(reason == "poni_distance_mismatch" for _path, _state, reason in harness.states)
    assert any("PONI distance mismatch" in message for message in harness.logged)
    with h5py.File(path, "r") as h5f:
        assert "/entry/technical/poni/poni_primary" not in h5f


def test_sync_reads_poni_from_file_not_memory_cache(tmp_path):
    _container_id, file_path = technical_container.create_technical_container(
        folder=tmp_path,
        distance_cm=2.0,
    )
    path = Path(file_path)
    harness = _SyncHarness(path)
    external_dir = tmp_path / "external_poni"
    external_dir.mkdir()
    external_poni = external_dir / "primary_2cm.poni"
    external_poni.write_text(_poni(0.02), encoding="utf-8")
    harness.poni_files["PRIMARY"] = {
        "path": str(external_poni),
        "name": external_poni.name,
    }
    harness.ponis["PRIMARY"] = _poni(0.158)

    ok = harness._write_runtime_rows_to_active_container(
        path,
        [
            {
                "index": 0,
                "alias": "PRIMARY",
                "technical_type": "AGBH",
                "is_primary": True,
                "data": np.ones((4, 4), dtype=np.float32),
                "source_ref": "",
                "source_path": "",
                "row_id": "row_1",
                "metadata": {},
            }
        ],
        show_errors=False,
    )

    assert ok is True
    copied_poni = path.parent / external_poni.name
    assert copied_poni.exists()
    with h5py.File(path, "r") as h5f:
        value = h5f["/entry/technical/poni/poni_primary"][()]
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        assert "Distance: 0.02" in str(value)
        assert "Distance: 0.158" not in str(value)


def test_sync_blocks_poni_with_wrong_pixel_size(tmp_path):
    _container_id, file_path = technical_container.create_technical_container(
        folder=tmp_path,
        distance_cm=2.0,
    )
    path = Path(file_path)
    harness = _SyncHarness(path)
    harness.config["poni_metadata_validation"] = {
        "enabled": True,
        "expected_energy_keV": 8.04,
        "expected_pixel_size_um": [55, 55],
        "expected_shape": [256, 256],
    }
    external_poni = tmp_path / "primary_wrong_pixel.poni"
    external_poni.write_text(
        _poni(0.02).replace(
            '"pixel1": 5.5e-05, "pixel2": 5.5e-05',
            '"pixel1": 5e-05, "pixel2": 5e-05',
        ),
        encoding="utf-8",
    )
    harness.poni_files["PRIMARY"] = {
        "path": str(external_poni),
        "name": external_poni.name,
    }

    ok = harness._write_runtime_rows_to_active_container(
        path,
        [
            {
                "index": 0,
                "alias": "PRIMARY",
                "technical_type": "AGBH",
                "is_primary": True,
                "data": np.ones((4, 4), dtype=np.float32),
                "source_ref": "",
                "source_path": "",
                "row_id": "row_1",
                "metadata": {},
            }
        ],
        show_errors=False,
    )

    assert ok is False
    assert any(reason == "poni_metadata_mismatch" for _path, _state, reason in harness.states)
    assert any("PONI metadata mismatch" in message for message in harness.logged)
    with h5py.File(path, "r") as h5f:
        assert "/entry/technical/poni/poni_primary" not in h5f


def test_poni_metadata_validation_catches_wrong_pixel_size(tmp_path):
    harness = _SyncHarness(tmp_path / "technical.nxs.h5")
    harness.config["poni_metadata_validation"] = {
        "enabled": True,
        "expected_energy_keV": 8.04,
        "expected_pixel_size_um": [55, 55],
        "expected_shape": [256, 256],
    }

    errors = harness._poni_metadata_validation_errors(
        {
            "PRIMARY": (
                _poni(0.02).replace(
                    '"pixel1": 5.5e-05, "pixel2": 5.5e-05',
                    '"pixel1": 5e-05, "pixel2": 5e-05',
                ),
                "bad_pixel.poni",
            )
        },
    )

    assert errors
    assert "pixel size" in errors[0]


def test_embedded_poni_metadata_validation_catches_wrong_shape(tmp_path):
    _container_id, file_path = technical_container.create_technical_container(
        folder=tmp_path,
        distance_cm=2.0,
    )
    path = Path(file_path)
    with h5py.File(path, "a") as h5f:
        poni_group = h5f.require_group("/entry/technical/poni")
        ds = poni_group.create_dataset(
            "poni_primary",
            data=_poni(0.02).replace('"max_shape": [256, 256]', '"max_shape": [512, 256]'),
        )
        ds.attrs["detector_alias"] = "PRIMARY"
        ds.attrs["detector_id"] = "det_primary"

    harness = _LockHarness()
    harness.config["poni_metadata_validation"] = {
        "enabled": True,
        "expected_energy_keV": 8.04,
        "expected_pixel_size_um": [55, 55],
        "expected_shape": [256, 256],
    }

    errors = harness._embedded_poni_metadata_validation_errors(path)

    assert errors
    assert "detector shape" in errors[0]
