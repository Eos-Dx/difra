from pathlib import Path

import h5py
import numpy as np

from container.v0_2 import technical_container
from difra.gui.main_window_ext.technical.h5_management_mixin import H5ManagementMixin


def _poni(distance_m: float) -> str:
    return (
        "# test poni\n"
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


def test_poni_distance_validation_catches_stale_geometry():
    errors = H5ManagementMixin._poni_distance_validation_errors(
        {"PRIMARY": (_poni(0.158), "primary_17cm.poni")},
        {"PRIMARY": 2.0},
    )

    assert errors
    assert "15.800 cm" in errors[0]
    assert "2.000 cm" in errors[0]


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
