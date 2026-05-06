from pathlib import Path

import h5py
import pytest

from difra.gui.main_window_ext.technical.h5_management_loading_actions import (
    create_new_active_technical_container,
)


class _Owner:
    STATE_PENDING_PONI = "pending_poni"
    STATE_PENDING_DISTANCES = "pending_distances"

    def __init__(self, tmp_path: Path, *, initial_distance: float, refreshed_distance: float):
        self.config = {
            "technical_folder": str(tmp_path / "technical"),
            "container_version": "0.2",
        }
        self._distances = {"PRIMARY": float(initial_distance)}
        self._refreshed_distance = float(refreshed_distance)
        self.active_path = None
        self.states = []
        self.logged = []
        self.synced = []

    def _distance_map_by_alias(self):
        return dict(self._distances)

    def _list_storage_technical_containers(self, _storage_folder):
        return []

    def _active_technical_container_path_obj(self):
        return self.active_path

    def configure_detector_distances(self):
        self._distances = {"PRIMARY": self._refreshed_distance}

    def _log_technical_event(self, message):
        self.logged.append(str(message))

    def _set_active_technical_container(self, file_path):
        self.active_path = Path(file_path)

    def _set_container_state(self, path, *, state, reason):
        self.states.append((Path(path), state, reason))
        with h5py.File(path, "a") as h5f:
            h5f.attrs["container_state"] = state

    def _sync_active_technical_container_from_table(self, show_errors=False):
        self.synced.append(bool(show_errors))


@pytest.mark.parametrize(
    ("initial_distance", "refreshed_distance", "expected_token"),
    [
        (17.0, 2.0, "2cm"),
        (2.0, 17.0, "17cm"),
    ],
)
def test_new_active_technical_container_filename_uses_refreshed_root_distance(
    tmp_path,
    initial_distance,
    refreshed_distance,
    expected_token,
):
    owner = _Owner(
        tmp_path,
        initial_distance=initial_distance,
        refreshed_distance=refreshed_distance,
    )

    created = create_new_active_technical_container(owner, clear_table=True)

    assert created is not None
    assert f"_{expected_token}_" in created.name
    with h5py.File(created, "r") as h5f:
        assert float(h5f.attrs["distance_cm"]) == refreshed_distance
    assert owner.states[-1][1] == "pending_poni"
