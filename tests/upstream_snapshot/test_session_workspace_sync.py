"""Session workspace sync/restore regression tests."""

import json
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np

from container.v0_2 import schema, technical_container
from container.v0_2.container_manager import lock_container
from difra.gui.main_window_ext.session_mixin import SessionMixin
from difra.gui.main_window_ext import session_workspace_restore
from difra.gui.session_manager import SessionManager


class _SpinStub:
    def __init__(self, value):
        self._value = float(value)

    def value(self):
        return float(self._value)

    def setValue(self, value):
        self._value = float(value)


class _Harness(SessionMixin):
    def __init__(self, config):
        self.config = config
        self.session_manager = SessionManager(config=config)
        self.image_view = SimpleNamespace(current_image_path=None)
        self.state = {}
        self.pixel_to_mm_ratio = 1.0
        self.include_center = (0.0, 0.0)
        self.real_x_pos_mm = _SpinStub(0.0)
        self.real_y_pos_mm = _SpinStub(0.0)
        self.logs = []

    def _append_session_log(self, message: str):
        self.logs.append(str(message))

    def update_points_table(self):
        return None

    def update_shape_table(self):
        return None

    def update_coordinates(self):
        return None


def _make_locked_technical_container(folder):
    folder.mkdir(parents=True, exist_ok=True)
    detector_config = [
        {
            "id": "det_primary",
            "alias": "PRIMARY",
            "type": "Advacam",
            "size": {"width": 8, "height": 8},
            "pixel_size_um": [55.0, 55.0],
        }
    ]

    dark_npy = folder / "dark_primary.npy"
    np.save(dark_npy, np.full((8, 8), 5, dtype=np.float32))
    empty_npy = folder / "empty_primary.npy"
    np.save(empty_npy, np.full((8, 8), 7, dtype=np.float32))
    bkg_npy = folder / "background_primary.npy"
    np.save(bkg_npy, np.full((8, 8), 9, dtype=np.float32))
    agbh_npy = folder / "agbh_primary.npy"
    np.save(agbh_npy, np.full((8, 8), 11, dtype=np.float32))

    aux_measurements = {
        "DARK": {"PRIMARY": str(dark_npy)},
        "EMPTY": {"PRIMARY": str(empty_npy)},
        "BACKGROUND": {"PRIMARY": str(bkg_npy)},
        "AGBH": {"PRIMARY": str(agbh_npy)},
    }
    poni_content = (
        "Detector: Detector\n"
        "PixelSize1: 5.500e-05\n"
        "PixelSize2: 5.500e-05\n"
        "Distance: 0.170000\n"
        "Poni1: 0.01\n"
        "Poni2: 0.02\n"
        "Rot1: 0\n"
        "Rot2: 0\n"
        "Rot3: 0\n"
        "Wavelength: 1.5406e-10\n"
    )
    poni_data = {"PRIMARY": (poni_content, "primary.poni")}

    _cid, path = technical_container.generate_from_aux_table(
        folder=folder,
        aux_measurements=aux_measurements,
        poni_data=poni_data,
        detector_config=detector_config,
        active_detector_ids=["det_primary"],
        distances_cm=17.0,
    )
    lock_container(path, user_id="sad")
    return path


def test_workspace_sync_writes_physical_coordinates_and_keeps_image_immutable(tmp_path):
    technical_path = _make_locked_technical_container(tmp_path / "technical")
    config = {"technical_folder": str(Path(technical_path).parent), "operator_id": "sad"}
    harness = _Harness(config=config)

    _sid, session_path = harness.session_manager.create_session(
        folder=tmp_path / "sessions",
        distance_cm=17.0,
        sample_id="SYNC_SAMPLE",
        study_name="SYNC_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-25",
    )

    harness.pixel_to_mm_ratio = 2.0
    harness.include_center = (100.0, 200.0)
    harness.real_x_pos_mm.setValue(1.5)
    harness.real_y_pos_mm.setValue(-2.0)

    image_calls = {"count": 0}

    def _image_source():
        image_calls["count"] += 1
        if image_calls["count"] == 1:
            return np.full((12, 12), 11, dtype=np.uint8)
        return np.full((12, 12), 99, dtype=np.uint8)

    harness._extract_current_image_array = _image_source
    harness.state = {
        "shapes": [
            {
                "id": 1,
                "type": "circle",
                "role": "include",
                "geometry": {"x": 10, "y": 10, "width": 40, "height": 40},
            }
        ],
        "zone_points": [
            {"id": 1, "x": 110.0, "y": 220.0, "type": "generated", "radius": 5},
            {"id": 2, "x": 90.0, "y": 180.0, "type": "generated", "radius": 5},
        ],
    }

    harness.sync_workspace_to_session_container(state=harness.state)
    harness.sync_workspace_to_session_container(state=harness.state)

    with h5py.File(session_path, "r") as h5f:
        image_data = h5f[f"{schema.GROUP_IMAGES}/img_001/data"][()]
        assert int(image_data[0, 0]) == 11
        assert (
            h5f[f"{schema.GROUP_IMAGES_ZONES}/zone_001"].attrs[schema.ATTR_ZONE_ROLE]
            == "sample_holder"
        )

        mapping_raw = h5f[f"{schema.GROUP_IMAGES_MAPPING}/mapping"][()]
        if isinstance(mapping_raw, bytes):
            mapping_raw = mapping_raw.decode("utf-8")
        mapping = json.loads(mapping_raw)
        conversion = mapping.get("pixel_to_mm_conversion", {})
        assert float(conversion["ratio"]) == 2.0
        assert conversion.get("include_center_px") == [100.0, 200.0]
        assert conversion.get("stage_reference_mm") == [1.5, -2.0]

        pt_001 = h5f[f"{schema.GROUP_POINTS}/pt_001"]
        mm = pt_001.attrs[schema.ATTR_PHYSICAL_COORDINATES_MM]
        assert float(mm[0]) == 1.5 - (110.0 - 100.0) / 2.0
        assert float(mm[1]) == -2.0 - (220.0 - 200.0) / 2.0

    assert image_calls["count"] == 1


def test_workspace_sync_preserves_sample_holder_include_and_exclude_roles(tmp_path):
    technical_path = _make_locked_technical_container(tmp_path / "technical_roles")
    config = {"technical_folder": str(Path(technical_path).parent), "operator_id": "sad"}
    harness = _Harness(config=config)

    _sid, session_path = harness.session_manager.create_session(
        folder=tmp_path / "sessions_roles",
        distance_cm=17.0,
        sample_id="ROLE_SAMPLE",
        study_name="ROLE_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-25",
    )

    harness.pixel_to_mm_ratio = 10.0
    harness.sample_holder_center_px = (50.0, 60.0)
    harness.include_center = (50.0, 60.0)
    harness._extract_current_image_array = lambda: np.full((16, 16), 5, dtype=np.uint8)
    harness.state = {
        "shapes": [
            {
                "id": 1,
                "type": "circle",
                "role": "holder circle",
                "physical_size_mm": 15.0,
                "geometry": {"x": 20, "y": 30, "width": 60, "height": 60},
            },
            {
                "id": 2,
                "type": "circle",
                "role": "include",
                "geometry": {"x": 24, "y": 34, "width": 40, "height": 40},
            },
            {
                "id": 3,
                "type": "circle",
                "role": "exclude",
                "geometry": {"x": 30, "y": 40, "width": 10, "height": 10},
            },
        ],
        "zone_points": [],
    }

    harness.sync_workspace_to_session_container(state=harness.state)

    with h5py.File(session_path, "r") as h5f:
        zone_ids = sorted(h5f[schema.GROUP_IMAGES_ZONES].keys())
        roles = [
            h5f[f"{schema.GROUP_IMAGES_ZONES}/{zone_id}"].attrs[schema.ATTR_ZONE_ROLE]
            for zone_id in zone_ids
        ]
        assert roles == ["sample_holder", "include", "exclude"]
        assert (
            h5f[f"{schema.GROUP_IMAGES_ZONES}/zone_001"].attrs["holder_diameter_mm"]
            == 15.0
        )

        mapping_raw = h5f[f"{schema.GROUP_IMAGES_MAPPING}/mapping"][()]
        if isinstance(mapping_raw, bytes):
            mapping_raw = mapping_raw.decode("utf-8")
        mapping = json.loads(mapping_raw)
        assert mapping.get("sample_holder_zone_id") == "zone_001"


def test_workspace_restore_reapplies_mapping_origin(tmp_path):
    technical_path = _make_locked_technical_container(tmp_path / "technical_restore")
    config = {"technical_folder": str(Path(technical_path).parent), "operator_id": "sad"}
    harness = _Harness(config=config)

    _sid, session_path = harness.session_manager.create_session(
        folder=tmp_path / "sessions_restore",
        distance_cm=17.0,
        sample_id="RESTORE_SAMPLE",
        study_name="RESTORE_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-25",
    )

    harness._extract_current_image_array = lambda: None
    harness.pixel_to_mm_ratio = 9.5
    harness.include_center = (123.0, 234.0)
    harness.real_x_pos_mm.setValue(7.25)
    harness.real_y_pos_mm.setValue(-3.75)
    harness.state = {
        "shapes": [
            {
                "id": 1,
                "type": "circle",
                "role": "include",
                "geometry": {"x": 1, "y": 2, "width": 3, "height": 4},
            }
        ],
        "zone_points": [
            {"id": 1, "x": 11.0, "y": 22.0, "type": "generated", "radius": 5}
        ],
    }
    harness.sync_workspace_to_session_container(state=harness.state)

    harness.pixel_to_mm_ratio = 1.0
    harness.include_center = (0.0, 0.0)
    harness.real_x_pos_mm.setValue(0.0)
    harness.real_y_pos_mm.setValue(0.0)

    harness._restore_session_workspace_from_container(Path(session_path))

    assert harness.pixel_to_mm_ratio == 9.5
    assert harness.include_center == (123.0, 234.0)
    assert harness.real_x_pos_mm.value() == 7.25
    assert harness.real_y_pos_mm.value() == -3.75


def test_workspace_sync_uses_holder_center_and_beam_anchor_when_rotation_confirmed(tmp_path):
    technical_path = _make_locked_technical_container(tmp_path / "technical_rotated")
    config = {"technical_folder": str(Path(technical_path).parent), "operator_id": "sad"}
    harness = _Harness(config=config)

    _sid, session_path = harness.session_manager.create_session(
        folder=tmp_path / "sessions_rotated",
        distance_cm=17.0,
        sample_id="ROT_SAMPLE",
        study_name="ROT_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-25",
    )

    harness.pixel_to_mm_ratio = 10.0
    harness.include_center = (0.0, 0.0)
    harness.sample_holder_center_px = (100.0, 200.0)
    harness.sample_photo_rotation_confirmed = True
    harness.sample_photo_rotation_deg = 180
    harness.sample_photo_beam_center_mm = (6.15, -9.15)
    harness._extract_current_image_array = lambda: np.array(
        [[1, 2, 3], [4, 5, 6]], dtype=np.uint8
    )
    harness.state = {
        "shapes": [
            {
                "id": 1,
                "type": "circle",
                "role": "holder circle",
                "physical_size_mm": 15.18,
                "center_px": [100.0, 200.0],
                "geometry": {"x": 24.1, "y": 124.1, "width": 151.8, "height": 151.8},
            }
        ],
        "zone_points": [
            {"id": 1, "x": 110.0, "y": 210.0, "type": "generated", "radius": 5}
        ],
    }

    harness.sync_workspace_to_session_container(state=harness.state)

    with h5py.File(session_path, "r") as h5f:
        raw_image = h5f[f"{schema.GROUP_IMAGES}/img_001/data"][()]
        rotated_image = h5f[f"{schema.GROUP_IMAGES}/img_002/data"][()]
        assert raw_image.tolist() == [[1, 2, 3], [4, 5, 6]]
        assert rotated_image.tolist() == [[6, 5, 4], [3, 2, 1]]
        assert (
            h5f[f"{schema.GROUP_IMAGES}/img_002"].attrs[schema.ATTR_IMAGE_TYPE]
            == "sample_rotated"
        )

        mapping_raw = h5f[f"{schema.GROUP_IMAGES_MAPPING}/mapping"][()]
        if isinstance(mapping_raw, bytes):
            mapping_raw = mapping_raw.decode("utf-8")
        mapping = json.loads(mapping_raw)
        conversion = mapping.get("pixel_to_mm_conversion", {})
        assert conversion.get("holder_circle_center_px") == [100.0, 200.0]
        assert conversion.get("beam_center_mm") == [6.15, -9.15]
        assert conversion.get("rotation_deg") == 180
        assert conversion.get("rotation_confirmed") is True
        assert conversion.get("workspace_image_type") == "sample_rotated"

        pt_001 = h5f[f"{schema.GROUP_POINTS}/pt_001"]
        mm = pt_001.attrs[schema.ATTR_PHYSICAL_COORDINATES_MM]
        assert float(mm[0]) == 6.15 + (110.0 - 100.0) / 10.0
        assert float(mm[1]) == -9.15 + (210.0 - 200.0) / 10.0


def test_workspace_restore_prefers_rotated_image_dataset_when_rotation_confirmed(tmp_path):
    technical_path = _make_locked_technical_container(tmp_path / "technical_restore_rot")
    config = {"technical_folder": str(Path(technical_path).parent), "operator_id": "sad"}
    harness = _Harness(config=config)

    _sid, session_path = harness.session_manager.create_session(
        folder=tmp_path / "sessions_restore_rot",
        distance_cm=17.0,
        sample_id="RESTORE_ROT_SAMPLE",
        study_name="RESTORE_ROT_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-25",
    )

    displayed = {}

    def _capture_image(image_array):
        displayed["image"] = np.array(image_array)
        return True

    harness._set_image_from_array = _capture_image
    harness._extract_current_image_array = lambda: np.array(
        [[11, 12, 13], [21, 22, 23]], dtype=np.uint8
    )
    harness.pixel_to_mm_ratio = 10.0
    harness.sample_holder_center_px = (100.0, 200.0)
    harness.sample_photo_rotation_confirmed = True
    harness.sample_photo_rotation_deg = 180
    harness.state = {
        "shapes": [],
        "zone_points": [],
    }

    harness.sync_workspace_to_session_container(state=harness.state)

    harness.sample_photo_rotation_confirmed = False
    harness.sample_photo_rotation_deg = 0
    harness._restore_session_workspace_from_container(Path(session_path))

    assert displayed["image"].tolist() == [[23, 22, 21], [13, 12, 11]]
    assert harness.sample_photo_rotation_confirmed is True
    assert harness.sample_photo_workspace_image_type == "sample_rotated"


def test_restore_measurement_history_creates_widgets_while_restoring_state(tmp_path):
    technical_path = _make_locked_technical_container(tmp_path / "technical_restore_profiles")
    config = {
        "technical_folder": str(Path(technical_path).parent),
        "operator_id": "sad",
        "detectors": [{"id": "det_primary", "alias": "PRIMARY"}],
    }
    manager = SessionManager(config=config)
    _sid, session_path = manager.create_session(
        folder=tmp_path / "sessions_restore_profiles",
        distance_cm=17.0,
        sample_id="RESTORE_PROFILES_SAMPLE",
        study_name="RESTORE_PROFILES_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-25",
    )
    manager.add_points(
        [{"pixel_coordinates": [10.0, 12.0], "physical_coordinates_mm": [0.0, 0.0]}]
    )
    manager.add_measurement(
        point_index=1,
        measurement_data={"PRIMARY": np.ones((8, 8), dtype=np.float32)},
        detector_metadata={"PRIMARY": {"integration_time_ms": 50.0}},
        poni_alias_map={"PRIMARY": "PRIMARY"},
    )

    class _Widget:
        def __init__(self):
            self.measurements = []
            self.profiles = []

        def add_measurement(self, results, timestamp):
            self.measurements.append((results, timestamp))

        def set_detector_profile(self, alias, profile_values):
            self.profiles.append((alias, profile_values))

    class _Owner:
        def __init__(self):
            self.config = config
            self.measurement_widgets = {}
            self._restoring_state = True
            self._restoring_measurement_history_widgets = False
            self.pointsTable = None

        def _decode_attr(self, value):
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return value

        def _get_point_identity_from_row(self, row):
            return ("1_uid", row + 1)

        def add_measurement_widget_to_panel(self, point_uid, point_display_id=None):
            if self._restoring_state and not self._restoring_measurement_history_widgets:
                return
            self.measurement_widgets[str(point_uid)] = _Widget()

        def _update_profile_previews_from_result_files(self, result_files, point_uid=None):
            widget = self.measurement_widgets.get(str(point_uid or ""))
            if widget is None:
                return
            for alias, measurement_ref in (result_files or {}).items():
                widget.set_detector_profile(alias, measurement_ref)

    owner = _Owner()

    session_workspace_restore.restore_measurement_history_from_session(owner, Path(session_path))

    assert "1_uid" in owner.measurement_widgets
    widget = owner.measurement_widgets["1_uid"]
    assert len(widget.measurements) == 1
    assert len(widget.profiles) == 1
