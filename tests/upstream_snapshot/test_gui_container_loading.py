"""GUI-level tests for loading technical and session containers."""

import os
import json
from collections import Counter
from pathlib import Path

import h5py
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QGraphicsScene,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from container.v0_2 import schema, technical_container, writer as session_writer
from container.v0_2.container_manager import lock_container
from difra.gui.main_window_ext import session_mixin, technical_measurements
from difra.gui.main_window_ext.technical import h5_management_mixin
from difra.gui.main_window_ext import state_saver_extension
from difra.gui.session_manager import SessionManager


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _patch_non_blocking_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))


def _make_technical_container(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)

    detector_config = [
        {
            "id": "det_primary",
            "alias": "PRIMARY",
            "type": "Advacam",
            "size": {"width": 8, "height": 8},
            "pixel_size_um": [55.0, 55.0],
        },
        {
            "id": "det_secondary",
            "alias": "SECONDARY",
            "type": "Advacam",
            "size": {"width": 8, "height": 8},
            "pixel_size_um": [55.0, 55.0],
        },
    ]

    aux_measurements = {}
    for technical_type in ("DARK", "EMPTY", "BACKGROUND", "AGBH"):
        aux_measurements[technical_type] = {}
        for alias in ("PRIMARY", "SECONDARY"):
            npy_path = folder / f"{technical_type.lower()}_001_20260213_120000_{alias}.npy"
            np.save(npy_path, np.full((8, 8), len(technical_type), dtype=np.float32))
            aux_measurements[technical_type][alias] = str(npy_path)

    poni_content = (
        "# synthetic poni\n"
        "Distance: 0.17\n"
        "PixelSize1: 5.5e-05\n"
        "PixelSize2: 5.5e-05\n"
        "Poni1: 0.01\n"
        "Poni2: 0.02\n"
        "Rot1: 0\n"
        "Rot2: 0\n"
        "Rot3: 0\n"
        "Wavelength: 1.5406e-10\n"
    )
    poni_data = {
        "PRIMARY": (poni_content, "primary.poni"),
        "SECONDARY": (poni_content, "secondary.poni"),
    }

    _container_id, file_path = technical_container.generate_from_aux_table(
        folder=folder,
        aux_measurements=aux_measurements,
        poni_data=poni_data,
        detector_config=detector_config,
        active_detector_ids=["det_primary", "det_secondary"],
        distances_cm={"PRIMARY": 17.0, "SECONDARY": 17.0},
    )
    return Path(file_path)


class _TechnicalLoadHarness(QMainWindow, technical_measurements.TechnicalMeasurementsMixin):
    def __init__(self, config: dict, work_dir: Path):
        super().__init__()
        self.config = config
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

        self.detector_controller = {}
        self.hardware_controller = None
        self.stage_controller = None
        self.masks = {}
        self.ponis = {}
        self.poni_files = {}
        self._detector_distances = {}
        self.measurement_logs = []

        self._build_ui()

    def _append_measurement_log(self, message: str):
        self.measurement_logs.append(message)

    def _build_ui(self):
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)

        self.integrationTimeSpin = QDoubleSpinBox()
        self.integrationTimeSpin.setValue(1.0)
        layout.addWidget(self.integrationTimeSpin)

        self.captureFramesSpin = QSpinBox()
        self.captureFramesSpin.setValue(1)
        layout.addWidget(self.captureFramesSpin)

        self.moveContinuousCheck = QCheckBox("Move Continuous")
        layout.addWidget(self.moveContinuousCheck)

        self.movementRadiusSpin = QDoubleSpinBox()
        self.movementRadiusSpin.setValue(2.0)
        layout.addWidget(self.movementRadiusSpin)

        self.folderLE = QLineEdit(str(self.work_dir))
        layout.addWidget(self.folderLE)

        self.auxNameLE = QLineEdit("aux")
        layout.addWidget(self.auxNameLE)

        self.auxTable = QTableWidget()
        self.auxTable.setColumnCount(4)
        self.auxTable.setHorizontalHeaderLabels(["Primary", "File", "Type", "Alias"])
        layout.addWidget(self.auxTable)


class _SessionLoadHarness(QMainWindow, session_mixin.SessionMixin):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.session_manager = SessionManager(config=config)
        self.status_updates = 0

    def update_session_status(self):
        self.status_updates += 1


class _FakeImageView:
    def __init__(self):
        self.scene = QGraphicsScene()
        self.shapes = []
        self.points_dict = {
            "generated": {"points": [], "zones": []},
            "user": {"points": [], "zones": []},
        }
        self.current_image_path = None
        self.image_item = None
        self.rotation_angle = 0
        self.crop_rect = None

    def set_image(self, pixmap, image_path=None):
        self.scene.clear()
        self.image_item = self.scene.addPixmap(pixmap)
        self.current_image_path = image_path


class _SessionRestoreHarness(
    QMainWindow, session_mixin.SessionMixin, state_saver_extension.StateSaverMixin
):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.session_manager = SessionManager(config=config)
        self.status_updates = 0
        self.image_view = _FakeImageView()
        self.state = {}
        self.next_point_id = 1
        self.pixel_to_mm_ratio = 2.0
        self.folderLineEdit = QLineEdit(str(config.get("measurements_folder", "")))
        self.integrationSpinBox = QDoubleSpinBox()
        self.integrationSpinBox.setValue(1.0)

    def update_session_status(self):
        self.status_updates += 1

    def update_points_table(self):
        pass

    def update_shape_table(self):
        pass

    def update_coordinates(self):
        pass


class _MeasurementHistoryWidget:
    def __init__(self):
        self.measurements = []

    def add_measurement(self, results, timestamp):
        self.measurements.append((results, timestamp))


class _SessionRestoreHistoryHarness(_SessionRestoreHarness):
    def __init__(self, config: dict):
        super().__init__(config=config)
        self.measurement_widgets = {}

    def add_measurement_widget_to_panel(self, point_uid: str, point_display_id=None):
        self.measurement_widgets.setdefault(point_uid, _MeasurementHistoryWidget())

    def _get_point_identity_from_row(self, row: int):
        return f"{row + 1}_row_uid", row + 1


class _SessionAwareTechnicalLoadHarness(
    _TechnicalLoadHarness, session_mixin.SessionMixin
):
    def __init__(self, config: dict, work_dir: Path):
        _TechnicalLoadHarness.__init__(self, config=config, work_dir=work_dir)
        self.session_manager = SessionManager(config=config)
        self.status_updates = 0

    def update_session_status(self):
        self.status_updates += 1


def _get_primary_checkbox(table: QTableWidget, row: int) -> QCheckBox:
    container = table.cellWidget(row, 0)
    assert container is not None
    checkbox = container.findChild(QCheckBox)
    assert checkbox is not None
    return checkbox


def test_load_technical_h5_sets_primary_and_types(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    technical_path = _make_technical_container(tmp_path / "technical_h5_source")
    config = {
        "DEV": True,
        "detectors": [
            {"id": "det_primary", "alias": "PRIMARY"},
            {"id": "det_secondary", "alias": "SECONDARY"},
        ],
        "dev_active_detectors": ["det_primary", "det_secondary"],
        "active_detectors": ["det_primary", "det_secondary"],
    }
    harness = _TechnicalLoadHarness(config=config, work_dir=tmp_path / "ui_h5")
    harness.show()
    qapp.processEvents()

    monkeypatch.setattr(
        h5_management_mixin.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(technical_path), "NeXus HDF5 Files (*.nxs.h5)")),
    )

    harness.load_technical_h5()
    qapp.processEvents()

    assert harness.auxTable.rowCount() == 8

    type_counts = Counter()
    for row in range(harness.auxTable.rowCount()):
        checkbox = _get_primary_checkbox(harness.auxTable, row)
        assert checkbox.isChecked() is True

        type_cb = harness.auxTable.cellWidget(row, 2)
        technical_type = type_cb.currentText()
        assert technical_type in {"DARK", "EMPTY", "BACKGROUND", "AGBH"}
        type_counts[technical_type] += 1

        file_item = harness.auxTable.item(row, 1)
        assert file_item is not None
        source_value = file_item.data(Qt.UserRole)
        assert isinstance(source_value, str)
        assert source_value.startswith("h5ref://")
        assert "/processed_signal" in source_value

    assert type_counts == {"DARK": 2, "EMPTY": 2, "BACKGROUND": 2, "AGBH": 2}


def test_load_technical_files_without_container(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    raw_dir = tmp_path / "technical_raw_only"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_files = []
    for technical_type in ("DARK", "EMPTY", "BACKGROUND", "AGBH"):
        npy_path = raw_dir / f"{technical_type.lower()}_001_20260213_120100_PRIMARY.npy"
        np.save(npy_path, np.ones((8, 8), dtype=np.float32))
        raw_files.append(str(npy_path))

    config = {
        "DEV": True,
        "detectors": [{"id": "det_primary", "alias": "PRIMARY"}],
        "dev_active_detectors": ["det_primary"],
        "active_detectors": ["det_primary"],
        "technical_folder": str(tmp_path / "technical_storage"),
        "technical_temp_folder": str(tmp_path / "technical_temp"),
    }
    harness = _TechnicalLoadHarness(config=config, work_dir=tmp_path / "ui_raw")
    harness.show()
    qapp.processEvents()

    monkeypatch.setattr(
        harness,
        "configure_detector_distances",
        lambda: setattr(harness, "_detector_distances", {"det_primary": 17.0}),
    )
    monkeypatch.setattr(
        harness,
        "update_active_technical_container_poni",
        lambda: True,
    )
    monkeypatch.setattr(
        technical_measurements.QFileDialog,
        "getOpenFileNames",
        staticmethod(lambda *a, **k: (raw_files, "NumPy Arrays (*.npy)")),
    )

    harness.load_technical_files()
    qapp.processEvents()

    assert harness.auxTable.rowCount() == 4


def test_open_existing_session_container_updates_state(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    session_dir = tmp_path / "session_source"
    session_id, session_path = session_writer.create_session_container(
        folder=session_dir,
        sample_id="SAMPLE_LOAD_001",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-13",
    )
    lock_container(Path(session_path), user_id="sad")

    harness = _SessionLoadHarness(config={"operator_id": "sad"})
    harness.show()
    qapp.processEvents()

    monkeypatch.setattr(
        session_mixin.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(session_path), "NeXus HDF5 Files (*.nxs.h5)")),
    )

    harness.on_restore_session()
    qapp.processEvents()

    assert harness.session_manager.session_path == Path(session_path)
    assert harness.session_manager.sample_id == "SAMPLE_LOAD_001"
    assert harness.session_manager.session_id == session_id
    assert harness.status_updates == 1


def test_technical_event_log_is_persisted_to_active_technical_container(qapp, tmp_path):
    technical_path = _make_technical_container(tmp_path / "technical_log_sink")
    config = {
        "DEV": True,
        "detectors": [
            {"id": "det_primary", "alias": "PRIMARY"},
            {"id": "det_secondary", "alias": "SECONDARY"},
        ],
        "dev_active_detectors": ["det_primary", "det_secondary"],
        "active_detectors": ["det_primary", "det_secondary"],
    }
    harness = _TechnicalLoadHarness(config=config, work_dir=tmp_path / "ui_log_sink")
    harness._set_active_technical_container(str(technical_path))

    harness._log_technical_event("persist-runtime-log")

    logs_txt_path = f"{schema.GROUP_RUNTIME}/difra_logs_txt"
    with h5py.File(technical_path, "r") as h5f:
        assert logs_txt_path in h5f
        ds = h5f[logs_txt_path]
        value = ds[()]
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        else:
            value = str(value)
        assert "persist-runtime-log" in value
        assert " | TECH | " in value
        assert str(ds.attrs.get("format", "")) == "txt"


def test_session_logs_forwarded_to_technical_runtime_sink():
    harness = _SessionLoadHarness(config={"operator_id": "sad"})
    forwarded = []
    harness._append_runtime_log_to_active_technical_container = (
        lambda message, **kwargs: (forwarded.append((message, kwargs)), True)[1]
    )

    harness._append_session_log("session-msg")
    harness._append_technical_log("tech-msg")

    assert len(forwarded) == 2
    assert forwarded[0][0] == "[SESSION] session-msg"
    assert forwarded[0][1].get("channel") == "SESSION"
    assert forwarded[1][0] == "[TECH] tech-msg"
    assert forwarded[1][1].get("channel") == "TECH"


def test_restore_session_enables_attenuation_checkbox_when_i0_exists(
    qapp, tmp_path, monkeypatch
):
    _patch_non_blocking_dialogs(monkeypatch)

    technical_folder = tmp_path / "technical_restore_i0"
    technical_path = _make_technical_container(technical_folder)
    lock_container(technical_path, user_id="sad")

    config = {
        "technical_folder": str(technical_folder),
        "operator_id": "sad",
        "site_id": "ULSTER",
        "machine_name": "DIFRA_TEST",
        "beam_energy_kev": 17.5,
    }
    manager = SessionManager(config=config)
    _session_id, session_path = manager.create_session(
        folder=tmp_path / "sessions_restore_i0",
        distance_cm=17.0,
        sample_id="SAMPLE_RESTORE_I0",
        study_name="RESTORE_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-25",
    )
    manager.add_points(
        [{"pixel_coordinates": [10.0, 12.0], "physical_coordinates_mm": [0.0, 0.0]}]
    )
    manager.add_attenuation_measurement(
        measurement_data={
            "det_primary": np.random.randint(100, 200, (8, 8), dtype=np.uint16)
        },
        detector_metadata={"det_primary": {"integration_time_ms": 50.0}},
        poni_alias_map={"PRIMARY": "det_primary"},
        mode="without",
    )
    manager.close_session()

    harness = _SessionLoadHarness(config=config)
    harness.attenuationCheckBox = QCheckBox("Attenuation")
    harness.attenuationCheckBox.setChecked(False)
    harness.show()
    qapp.processEvents()

    monkeypatch.setattr(
        session_mixin.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(session_path), "NeXus HDF5 Files (*.nxs.h5)")),
    )

    harness.on_restore_session()
    qapp.processEvents()

    assert harness.session_manager.i0_counter is not None
    assert harness.attenuationCheckBox.isChecked() is True


def test_aux_state_roundtrip_uses_current_columns(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    raw_dir = tmp_path / "aux_state_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    file_path = raw_dir / "dark_001_20260213_121000_PRIMARY.npy"
    np.save(file_path, np.ones((8, 8), dtype=np.float32))

    config = {
        "DEV": True,
        "detectors": [{"id": "det_primary", "alias": "PRIMARY"}],
        "dev_active_detectors": ["det_primary"],
        "active_detectors": ["det_primary"],
    }
    harness = _TechnicalLoadHarness(config=config, work_dir=tmp_path / "ui_state")
    harness.show()
    qapp.processEvents()

    harness._add_aux_item_to_list("PRIMARY", str(file_path))
    row = 0
    type_cb = harness.auxTable.cellWidget(row, 2)
    type_cb.setCurrentText("DARK")
    primary_cb = _get_primary_checkbox(harness.auxTable, row)
    primary_cb.setChecked(True)

    state = harness.build_aux_state()
    assert len(state) == 1
    assert state[0]["file_path"] == str(file_path)
    assert state[0]["type"] == "DARK"
    assert state[0]["alias"] == "PRIMARY"
    assert state[0]["is_primary"] is True

    harness.restore_technical_aux_rows(state)
    qapp.processEvents()
    assert harness.auxTable.rowCount() == 1
    restored_type_cb = harness.auxTable.cellWidget(0, 2)
    assert restored_type_cb.currentText() == "DARK"
    restored_primary_cb = _get_primary_checkbox(harness.auxTable, 0)
    assert restored_primary_cb.isChecked() is True


def test_restore_session_recovers_image_zones_and_points(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    technical_folder = tmp_path / "technical_restore"
    technical_path = _make_technical_container(technical_folder)
    lock_container(technical_path, user_id="sad")

    config = {
        "technical_folder": str(technical_folder),
        "operator_id": "sad",
        "site_id": "ULSTER",
        "machine_name": "DIFRA_TEST",
        "beam_energy_kev": 17.5,
    }
    manager = SessionManager(config=config)
    _session_id, session_path = manager.create_session(
        folder=tmp_path / "sessions_restore",
        distance_cm=17.0,
        sample_id="RESTORE_SAMPLE",
        study_name="RESTORE_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-13",
    )
    manager.add_sample_image(
        image_data=np.full((32, 32), 7, dtype=np.uint8),
        image_index=1,
        image_type="sample",
    )
    manager.add_zone(
        zone_index=1,
        zone_role="sample_holder",
        geometry_px=[8, 8, 16, 16],
        shape="circle",
        holder_diameter_mm=8.0,
    )
    manager.add_zone(
        zone_index=2,
        zone_role="exclude",
        geometry_px=[12, 12, 4, 4],
        shape="circle",
    )
    manager.add_points(
        [
            {
                "pixel_coordinates": [10 + i, 11 + i],
                "physical_coordinates_mm": [0.0, 0.0],
                "point_status": "pending",
            }
            for i in range(5)
        ]
    )
    manager.close_session()

    harness = _SessionRestoreHistoryHarness(config=config)
    harness.show()
    qapp.processEvents()

    monkeypatch.setattr(
        session_mixin.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(session_path), "NeXus HDF5 Files (*.nxs.h5)")),
    )

    harness.on_restore_session()
    qapp.processEvents()

    assert harness.session_manager.session_path == Path(session_path)
    assert harness.session_manager.sample_id == "RESTORE_SAMPLE"
    assert harness.session_manager.study_name == "RESTORE_STUDY"
    assert harness.status_updates == 1

    assert harness.image_view.image_item is not None
    assert len(harness.image_view.shapes) == 2
    assert [int(shape.get("id")) for shape in harness.image_view.shapes] == [1, 2]
    assert len(harness.image_view.points_dict["generated"]["points"]) == 5
    assert len(harness.state.get("shapes", [])) == 2
    assert len(harness.state.get("zone_points", [])) == 5


def test_sync_workspace_snapshot_to_unlocked_session(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    technical_folder = tmp_path / "technical_sync"
    technical_path = _make_technical_container(technical_folder)
    lock_container(technical_path, user_id="sad")

    config = {
        "technical_folder": str(technical_folder),
        "operator_id": "sad",
        "site_id": "ULSTER",
        "machine_name": "DIFRA_TEST",
        "beam_energy_kev": 17.5,
    }
    harness = _SessionRestoreHistoryHarness(config=config)
    harness.show()
    qapp.processEvents()

    _session_id, session_path = harness.session_manager.create_session(
        folder=tmp_path / "sessions_sync",
        distance_cm=17.0,
        sample_id="SYNC_SAMPLE",
        study_name="SYNC_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-13",
    )

    class _Spin:
        def __init__(self, value):
            self._value = float(value)

        def value(self):
            return float(self._value)

        def setValue(self, value):
            self._value = float(value)

    harness.include_center = (100.0, 200.0)
    harness.real_x_pos_mm = _Spin(1.5)
    harness.real_y_pos_mm = _Spin(-2.0)

    image_calls = {"count": 0}

    def _image_source():
        image_calls["count"] += 1
        if image_calls["count"] == 1:
            return np.full((24, 24), 123, dtype=np.uint8)
        return np.full((24, 24), 200, dtype=np.uint8)

    monkeypatch.setattr(harness, "_extract_current_image_array", _image_source)
    harness.state = {
        "shapes": [
            {
                "id": 1,
                "type": "circle",
                "role": "include",
                "geometry": {"x": 4, "y": 4, "width": 12, "height": 12},
            },
            {
                "id": 2,
                "type": "circle",
                "role": "exclude",
                "geometry": {"x": 8, "y": 8, "width": 4, "height": 4},
            },
        ],
        "zone_points": [
            {"id": i + 1, "x": 5 + i, "y": 6 + i, "type": "generated", "radius": 5}
            for i in range(5)
        ],
    }

    harness.sync_workspace_to_session_container(state=harness.state)
    harness.sync_workspace_to_session_container(state=harness.state)

    with h5py.File(session_path, "r") as h5f:
        assert "/entry/images/img_001" in h5f
        assert len(h5f["/entry/images/zones"].keys()) == 2
        assert len(h5f["/entry/points"].keys()) == 5
        image_data = h5f["/entry/images/img_001/data"][()]
        assert int(image_data[0, 0]) == 123

        mapping_raw = h5f["/entry/images/mapping/mapping"][()]
        if isinstance(mapping_raw, bytes):
            mapping_raw = mapping_raw.decode("utf-8")
        mapping = json.loads(mapping_raw)
        conversion = mapping.get("pixel_to_mm_conversion", {})
        assert float(conversion["ratio"]) == pytest.approx(2.0)
        assert conversion.get("include_center_px") == [100.0, 200.0]
        assert conversion.get("stage_reference_mm") == [1.5, -2.0]

        pt_001 = h5f["/entry/points/pt_001"]
        px = pt_001.attrs["pixel_coordinates"]
        mm = pt_001.attrs["physical_coordinates_mm"]
        expected_x = 1.5 - (float(px[0]) - 100.0) / 2.0
        expected_y = -2.0 - (float(px[1]) - 200.0) / 2.0
        assert float(mm[0]) == pytest.approx(expected_x)
        assert float(mm[1]) == pytest.approx(expected_y)

    assert image_calls["count"] == 1


def test_restore_session_reapplies_mapping_origin_from_container(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    technical_folder = tmp_path / "technical_restore_mapping"
    technical_path = _make_technical_container(technical_folder)
    lock_container(technical_path, user_id="sad")

    config = {
        "technical_folder": str(technical_folder),
        "operator_id": "sad",
        "site_id": "ULSTER",
        "machine_name": "DIFRA_TEST",
        "beam_energy_kev": 17.5,
    }
    harness = _SessionRestoreHistoryHarness(config=config)
    harness.show()
    qapp.processEvents()

    _session_id, session_path = harness.session_manager.create_session(
        folder=tmp_path / "sessions_restore_mapping",
        distance_cm=17.0,
        sample_id="RESTORE_MAP_SAMPLE",
        study_name="RESTORE_MAP_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-13",
    )

    class _Spin:
        def __init__(self, value):
            self._value = float(value)

        def value(self):
            return float(self._value)

        def setValue(self, value):
            self._value = float(value)

    harness.include_center = (123.0, 234.0)
    harness.real_x_pos_mm = _Spin(7.25)
    harness.real_y_pos_mm = _Spin(-3.75)
    harness.pixel_to_mm_ratio = 9.5
    monkeypatch.setattr(
        harness,
        "_extract_current_image_array",
        lambda: np.full((8, 8), 50, dtype=np.uint8),
    )
    harness.state = {
        "shapes": [
            {
                "id": 1,
                "type": "circle",
                "role": "include",
                "geometry": {"x": 10, "y": 20, "width": 30, "height": 30},
            }
        ],
        "zone_points": [
            {"id": 1, "x": 11, "y": 22, "type": "generated", "radius": 5},
            {"id": 2, "x": 33, "y": 44, "type": "generated", "radius": 5},
        ],
    }
    harness.sync_workspace_to_session_container(state=harness.state)

    # Overwrite UI values to verify restore applies container mapping.
    harness.include_center = (0.0, 0.0)
    harness.real_x_pos_mm.setValue(0.0)
    harness.real_y_pos_mm.setValue(0.0)
    harness.pixel_to_mm_ratio = 1.0

    harness._restore_session_workspace_from_container(Path(session_path))

    assert harness.pixel_to_mm_ratio == pytest.approx(9.5)
    assert harness.include_center == pytest.approx((123.0, 234.0))
    assert harness.real_x_pos_mm.value() == pytest.approx(7.25)
    assert harness.real_y_pos_mm.value() == pytest.approx(-3.75)


def test_loading_technical_updates_active_unlocked_session(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    technical_a = _make_technical_container(tmp_path / "technical_a")
    technical_b = _make_technical_container(tmp_path / "technical_b")
    lock_container(technical_a, user_id="sad")

    config = {
        "DEV": True,
        "detectors": [
            {"id": "det_primary", "alias": "PRIMARY"},
            {"id": "det_secondary", "alias": "SECONDARY"},
        ],
        "dev_active_detectors": ["det_primary", "det_secondary"],
        "active_detectors": ["det_primary", "det_secondary"],
        "technical_folder": str(tmp_path / "technical_a"),
        "operator_id": "sad",
    }
    harness = _SessionAwareTechnicalLoadHarness(
        config=config,
        work_dir=tmp_path / "ui_session_tech",
    )
    harness.show()
    qapp.processEvents()

    _session_id, session_path = harness.session_manager.create_session(
        folder=tmp_path / "sessions_update",
        distance_cm=17.0,
        sample_id="TECH_SWAP",
        study_name="TECH_SWAP_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-13",
    )

    monkeypatch.setattr(
        h5_management_mixin.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(technical_b), "NeXus HDF5 Files (*.nxs.h5)")),
    )

    harness.load_technical_h5()
    qapp.processEvents()

    with h5py.File(session_path, "r") as h5f:
        source_file = h5f[schema.GROUP_CALIBRATION_SNAPSHOT].attrs.get("source_file", "")
        if isinstance(source_file, bytes):
            source_file = source_file.decode("utf-8")
        assert source_file == str(technical_b)

    assert harness.session_manager.technical_container_path == Path(technical_b)


def test_restore_session_recovers_incomplete_point_from_files(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    technical_folder = tmp_path / "technical_recover_complete"
    technical_path = _make_technical_container(technical_folder)
    lock_container(technical_path, user_id="sad")

    measurement_folder = tmp_path / "session_measurements"
    measurement_folder.mkdir(parents=True, exist_ok=True)

    config = {
        "technical_folder": str(technical_folder),
        "measurements_folder": str(measurement_folder),
        "operator_id": "sad",
        "site_id": "ULSTER",
        "machine_name": "DIFRA_TEST",
        "beam_energy_kev": 17.5,
        "detectors": [{"id": "det_primary", "alias": "PRIMARY"}],
        "active_detectors": ["det_primary"],
    }
    manager = SessionManager(config=config)
    _session_id, session_path = manager.create_session(
        folder=tmp_path / "sessions_recover_complete",
        distance_cm=17.0,
        sample_id="RECOVER_OK_SAMPLE",
        study_name="RECOVER_OK_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-13",
    )
    manager.add_points(
        [
            {
                "pixel_coordinates": [10.0, 11.0],
                "physical_coordinates_mm": [1.23, 4.56],
                "point_status": "pending",
            }
        ]
    )
    measurement_path = manager.begin_point_measurement(
        point_index=1,
        timestamp_start="2026-02-13 12:34:56",
    )

    np.save(
        measurement_folder / "RECOVER_OK_SAMPLE_1.23_4.56_20260213_123456_PRIMARY.npy",
        np.full((8, 8), 5, dtype=np.float32),
    )
    manager.close_session()

    harness = _SessionRestoreHistoryHarness(config=config)
    harness.show()
    qapp.processEvents()

    monkeypatch.setattr(
        session_mixin.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(session_path), "NeXus HDF5 Files (*.nxs.h5)")),
    )

    harness.on_restore_session()
    qapp.processEvents()

    with h5py.File(session_path, "r") as h5f:
        measurement_group = h5f[measurement_path]
        assert measurement_group.attrs[schema.ATTR_MEASUREMENT_STATUS] == schema.STATUS_COMPLETED
        assert schema.ATTR_TIMESTAMP_END in measurement_group.attrs
        assert "det_primary" in measurement_group
        point_group = h5f[f"{schema.GROUP_POINTS}/pt_001"]
        assert point_group.attrs[schema.ATTR_POINT_STATUS] == schema.POINT_STATUS_MEASURED

    widget = harness.measurement_widgets.get("1_row_uid")
    assert widget is not None
    assert len(widget.measurements) == 1
    _results, restored_timestamp = widget.measurements[0]
    assert restored_timestamp
    assert restored_timestamp != "from container"


def test_restore_session_marks_incomplete_point_for_remeasure_on_user_choice(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda *a, **k: QMessageBox.No
            if len(a) > 1 and a[1] == "Recover Incomplete Point"
            else QMessageBox.Yes
        ),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))

    technical_folder = tmp_path / "technical_recover_remeasure"
    technical_path = _make_technical_container(technical_folder)
    lock_container(technical_path, user_id="sad")

    measurement_folder = tmp_path / "session_measurements_remeasure"
    measurement_folder.mkdir(parents=True, exist_ok=True)

    config = {
        "technical_folder": str(technical_folder),
        "measurements_folder": str(measurement_folder),
        "operator_id": "sad",
        "site_id": "ULSTER",
        "machine_name": "DIFRA_TEST",
        "beam_energy_kev": 17.5,
        "detectors": [{"id": "det_primary", "alias": "PRIMARY"}],
        "active_detectors": ["det_primary"],
    }
    manager = SessionManager(config=config)
    _session_id, session_path = manager.create_session(
        folder=tmp_path / "sessions_recover_remeasure",
        distance_cm=17.0,
        sample_id="RECOVER_REMEASURE_SAMPLE",
        study_name="RECOVER_REMEASURE_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-13",
    )
    manager.add_points(
        [
            {
                "pixel_coordinates": [20.0, 21.0],
                "physical_coordinates_mm": [2.34, 5.67],
                "point_status": "pending",
            }
        ]
    )
    measurement_path = manager.begin_point_measurement(
        point_index=1,
        timestamp_start="2026-02-13 12:44:56",
    )

    np.save(
        measurement_folder / "RECOVER_REMEASURE_SAMPLE_2.34_5.67_20260213_124456_PRIMARY.npy",
        np.full((8, 8), 9, dtype=np.float32),
    )
    manager.close_session()

    harness = _SessionRestoreHarness(config=config)
    harness.show()
    qapp.processEvents()

    monkeypatch.setattr(
        session_mixin.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(session_path), "NeXus HDF5 Files (*.nxs.h5)")),
    )

    harness.on_restore_session()
    qapp.processEvents()

    with h5py.File(session_path, "r") as h5f:
        measurement_group = h5f[measurement_path]
        assert measurement_group.attrs[schema.ATTR_MEASUREMENT_STATUS] == schema.STATUS_ABORTED
        assert measurement_group.attrs[schema.ATTR_FAILURE_REASON] == "user_selected_remeasure"
        assert len([name for name in measurement_group.keys() if name.startswith("det_")]) == 0
        point_group = h5f[f"{schema.GROUP_POINTS}/pt_001"]
        assert point_group.attrs[schema.ATTR_POINT_STATUS] == schema.POINT_STATUS_PENDING


def test_container_backed_aux_rows_open_on_single_click(qapp, tmp_path, monkeypatch):
    config = {
        "measurements_folder": str(tmp_path / "measurements"),
        "detectors": [],
    }
    harness = _TechnicalLoadHarness(config=config, work_dir=tmp_path / "measurements")
    harness.auxTable.insertRow(0)

    file_item = technical_measurements.QTableWidgetItem("PRIMARY: from_container")
    file_item.setData(
        technical_measurements.Qt.UserRole,
        "h5ref:///tmp/test_session.nxs.h5#/entry/measurements/meas_001",
    )
    harness.auxTable.setItem(0, harness.AUX_COL_FILE, file_item)

    opened = []
    monkeypatch.setattr(
        harness,
        "_open_measurement_from_table",
        lambda row, col: opened.append((row, col)),
    )

    harness._handle_aux_table_cell_clicked(0, harness.AUX_COL_FILE)
    harness._handle_aux_table_cell_double_clicked(0, harness.AUX_COL_FILE)

    assert opened == [(0, harness.AUX_COL_FILE)]


def test_file_backed_aux_rows_still_require_double_click(qapp, tmp_path, monkeypatch):
    config = {
        "measurements_folder": str(tmp_path / "measurements"),
        "detectors": [],
    }
    harness = _TechnicalLoadHarness(config=config, work_dir=tmp_path / "measurements")
    harness.auxTable.insertRow(0)

    file_item = technical_measurements.QTableWidgetItem("PRIMARY: file.npy")
    file_item.setData(technical_measurements.Qt.UserRole, str(tmp_path / "file.npy"))
    harness.auxTable.setItem(0, harness.AUX_COL_FILE, file_item)

    opened = []
    monkeypatch.setattr(
        harness,
        "_open_measurement_from_table",
        lambda row, col: opened.append((row, col)),
    )

    harness._handle_aux_table_cell_clicked(0, harness.AUX_COL_FILE)
    harness._handle_aux_table_cell_double_clicked(0, harness.AUX_COL_FILE)

    assert opened == [(0, harness.AUX_COL_FILE)]


def test_on_new_technical_container_requests_empty_container(qapp, tmp_path, monkeypatch):
    _patch_non_blocking_dialogs(monkeypatch)

    config = {
        "DEV": True,
        "detectors": [{"id": "det_primary", "alias": "PRIMARY"}],
        "dev_active_detectors": ["det_primary"],
        "active_detectors": ["det_primary"],
        "operator_id": "sad",
    }
    harness = _SessionAwareTechnicalLoadHarness(config=config, work_dir=tmp_path / "ui_new")
    harness._detector_distances = {"det_primary": 17.0}
    harness.show()
    qapp.processEvents()

    calls = []

    def _fake_create(*, clear_table=False):
        calls.append(bool(clear_table))
        return tmp_path / "technical_new.nxs.h5"

    monkeypatch.setattr(harness, "_create_new_active_technical_container", _fake_create)

    harness.on_new_technical_container()
    qapp.processEvents()

    assert calls == [True]


def test_replacement_archives_technical_container_to_archive_folder(qapp, tmp_path):
    technical_folder = tmp_path / "technical_replace"
    archive_folder = tmp_path / "archive" / "technical"
    tech_path = _make_technical_container(technical_folder)
    lock_container(tech_path, user_id="sad")

    harness = _TechnicalLoadHarness(
        config={"technical_archive_folder": str(archive_folder)},
        work_dir=technical_folder,
    )

    archived_path = harness._archive_existing_technical_container_for_replacement(
        tech_path
    )

    assert not tech_path.exists()
    assert archived_path.exists()
    assert archived_path.parent.parent == archive_folder


def test_replacement_archive_remaps_aux_table_h5refs_to_archived_file(qapp, tmp_path):
    technical_folder = tmp_path / "technical_replace_refs"
    archive_folder = tmp_path / "archive" / "technical"
    tech_path = _make_technical_container(technical_folder)
    lock_container(tech_path, user_id="sad")

    harness = _TechnicalLoadHarness(
        config={"technical_archive_folder": str(archive_folder)},
        work_dir=technical_folder,
    )

    harness.auxTable.insertRow(0)
    file_item = technical_measurements.QTableWidgetItem("PRIMARY: from_container")
    dataset_path = "/runtime/technical_aux_rows/row_000001/processed_signal"
    file_item.setData(
        technical_measurements.Qt.UserRole,
        f"h5ref://{tech_path}#{dataset_path}",
    )
    file_item.setData(
        harness._aux_source_info_role(),
        {
            "source_kind": "container",
            "source_path": "",
            "container_path": str(tech_path),
            "dataset_path": dataset_path,
            "row_id": "row_000001",
        },
    )
    harness.auxTable.setItem(0, harness.AUX_COL_FILE, file_item)

    archived_path = harness._archive_existing_technical_container_for_replacement(
        tech_path
    )

    new_ref = str(file_item.data(technical_measurements.Qt.UserRole) or "")
    assert new_ref == f"h5ref://{archived_path}#{dataset_path}"

    source_info = file_item.data(harness._aux_source_info_role())
    assert isinstance(source_info, dict)
    assert source_info.get("container_path") == str(archived_path)


def test_archive_active_container_locks_then_archives_container_and_related_files(
    qapp, tmp_path, monkeypatch
):
    _patch_non_blocking_dialogs(monkeypatch)

    technical_folder = tmp_path / "technical_archive_action"
    archive_folder = tmp_path / "archive" / "technical"
    tech_path = _make_technical_container(technical_folder)

    related_txt = technical_folder / "archive_me.txt"
    related_poni = technical_folder / "archive_me.poni"
    related_txt.write_text("raw", encoding="utf-8")
    related_poni.write_text("Distance: 0.17\n", encoding="utf-8")

    harness = _TechnicalLoadHarness(
        config={
            "technical_archive_folder": str(archive_folder),
            "technical_archive_patterns": ["*.txt", "*.poni"],
            "operator_id": "sad",
        },
        work_dir=technical_folder,
    )
    harness._set_active_technical_container(str(tech_path))
    lock_invocations = []
    monkeypatch.setattr(
        harness,
        "lock_active_technical_container",
        lambda: (lock_invocations.append(True), True)[1],
    )

    archived = harness.archive_active_technical_container()

    assert archived is True
    assert lock_invocations == [True]
    assert not tech_path.exists()
    assert str(getattr(harness, "_active_technical_container_path", "") or "").strip() == ""

    archived_h5 = list(archive_folder.rglob(tech_path.name))
    assert len(archived_h5) == 1
    archive_subdir = archived_h5[0].parent
    assert (archive_subdir / related_txt.name).exists()
    assert (archive_subdir / related_poni.name).exists()


def test_new_technical_container_forces_active_session_archive_and_upload(
    qapp, tmp_path, monkeypatch
):
    technical_folder = tmp_path / "technical_force_close"
    measurements_folder = tmp_path / "measurements_force_close"
    measurements_archive = tmp_path / "archive" / "measurements"

    tech_path = _make_technical_container(technical_folder)
    lock_container(tech_path, user_id="sad")

    config = {
        "technical_folder": str(technical_folder),
        "measurements_folder": str(measurements_folder),
        "measurements_archive_folder": str(measurements_archive),
        "operator_id": "sad",
        "site_id": "ULSTER",
        "machine_name": "DIFRA_TEST",
        "beam_energy_kev": 17.5,
        "detectors": [{"id": "det_primary", "alias": "PRIMARY"}],
        "active_detectors": ["det_primary"],
    }
    harness = _SessionAwareTechnicalLoadHarness(config=config, work_dir=technical_folder)
    warnings = []

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.Yes),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: warnings.append(a[2]) or QMessageBox.Ok),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *a, **k: QMessageBox.Ok),
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        staticmethod(lambda *a, **k: QMessageBox.Ok),
    )

    _session_id, session_path = harness.session_manager.create_session(
        folder=measurements_folder,
        distance_cm=17.0,
        technical_container_path=str(tech_path),
        sample_id="FORCED_CLOSE_SAMPLE",
        study_name="FORCED_CLOSE_STUDY",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-03-04",
    )
    (measurements_folder / "FORCED_CLOSE_SAMPLE_state.json").write_text("{}")
    (measurements_folder / "capture.npy").write_text("placeholder")

    assert harness._finalize_active_session_for_new_technical_container() is True
    assert harness.session_manager.is_session_active() is False
    assert not session_path.exists()
    assert warnings == []

    archived_sessions = sorted(measurements_archive.rglob("session_*.nxs.h5"))
    assert len(archived_sessions) == 1
    archived_session = archived_sessions[0]
    assert harness.session_manager.container_manager.get_transfer_status(archived_session) == "sent"
    assert harness.session_manager.container_manager.is_container_locked(archived_session)
    with h5py.File(archived_session, "r") as h5f:
        assert h5f.attrs.get("upload_status") == "success"
        assert str(h5f.attrs.get("upload_session_id", "")).startswith("upload_")
