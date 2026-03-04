from __future__ import annotations

from types import SimpleNamespace

import difra.gui.main_window_ext.zone_measurements.logic.process_results_mixin as results_module
from difra.gui.main_window_ext.zone_measurements.logic.process_results_mixin import (
    ZoneMeasurementsProcessResultsMixin,
)


class _FakeLogger:
    def __init__(self) -> None:
        self.calls = []

    def info(self, *args, **kwargs) -> None:
        self.calls.append(("info", args, kwargs))

    def warning(self, *args, **kwargs) -> None:
        self.calls.append(("warning", args, kwargs))

    def error(self, *args, **kwargs) -> None:
        self.calls.append(("error", args, kwargs))

    def debug(self, *args, **kwargs) -> None:
        self.calls.append(("debug", args, kwargs))


class _FakeTableItem:
    def __init__(self, text: str = "", data_map=None) -> None:
        self._text = text
        self._data = dict(data_map or {})

    def data(self, role):
        return self._data.get(role)

    def text(self) -> str:
        return self._text

    def setData(self, role, value) -> None:
        self._data[role] = value


class _FakePointsTable:
    def __init__(self, items=None) -> None:
        self._items = dict(items or {})

    def item(self, row: int, col: int):
        return self._items.get((row, col))


class _FakePointItem:
    def __init__(self, data_map=None) -> None:
        self._data = dict(data_map or {})

    def data(self, role):
        return self._data.get(role)

    def setData(self, role, value) -> None:
        self._data[role] = value


class _FakeButton:
    def __init__(self) -> None:
        self.enabled = None
        self.text = None

    def setEnabled(self, value: bool) -> None:
        self.enabled = value

    def setText(self, value: str) -> None:
        self.text = value


class _FakeProgressBar:
    def __init__(self) -> None:
        self.value = None

    def setValue(self, value: int) -> None:
        self.value = value


class _FakeLabel:
    def __init__(self) -> None:
        self.text = None

    def setText(self, value: str) -> None:
        self.text = value


class _FakeTreeItem:
    def __init__(self) -> None:
        self.calls = []

    def setText(self, column: int, text: str) -> None:
        self.calls.append((column, text))


class _MeasurementWidget:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.hidden = False
        self.mm_coordinates = []
        self.measurements = []
        self.window_titles = []

    def isHidden(self):
        return self.hidden

    def set_mm_coordinates(self, x_mm: float, y_mm: float) -> None:
        self.mm_coordinates.append((x_mm, y_mm))

    def add_measurement(self, results, timestamp) -> None:
        self.measurements.append((results, timestamp))

    def setWindowTitle(self, title: str) -> None:
        self.window_titles.append(title)


def _patch_pm(monkeypatch):
    logger = _FakeLogger()
    warnings = []
    pm = SimpleNamespace(
        logger=logger,
        QMessageBox=SimpleNamespace(
            warning=lambda *args: warnings.append(args[1:3]),
        ),
    )
    monkeypatch.setattr(results_module, "_pm", lambda: pm)
    return logger, warnings


def test_append_log_helpers_prefix_messages():
    logs = []
    owner = SimpleNamespace(_append_measurement_log=lambda message: logs.append(message))

    ZoneMeasurementsProcessResultsMixin._append_capture_log(owner, "saved")
    ZoneMeasurementsProcessResultsMixin._append_session_log(owner, "written")

    assert logs == ["[CAPTURE] saved", "[SESSION] written"]


def test_append_log_helpers_swallow_logging_errors():
    owner = SimpleNamespace(_append_measurement_log=lambda message: (_ for _ in ()).throw(RuntimeError("boom")))

    ZoneMeasurementsProcessResultsMixin._append_capture_log(owner, "saved")
    ZoneMeasurementsProcessResultsMixin._append_session_log(owner, "written")


def test_build_session_measurement_result_refs_handles_missing_context():
    owner = SimpleNamespace()
    refs = {"det_a": "/tmp/a.npy"}

    assert (
        ZoneMeasurementsProcessResultsMixin._build_session_measurement_result_refs(
            owner,
            session_manager=None,
            measurement_path="/measurements/pt_001/meas_1",
            result_files=refs,
            detector_lookup={},
        )
        == refs
    )


def test_build_session_measurement_result_refs_formats_roles_from_schema():
    owner = SimpleNamespace()
    schema = SimpleNamespace(
        DATASET_PROCESSED_SIGNAL="processed_signal",
        format_detector_role=lambda alias: f"role_{alias}",
    )
    session_manager = SimpleNamespace(
        session_path="/tmp/session.h5",
        schema=schema,
    )

    refs = ZoneMeasurementsProcessResultsMixin._build_session_measurement_result_refs(
        owner,
        session_manager=session_manager,
        measurement_path="/measurements/pt_001/meas_0001",
        result_files={"det_a": "/tmp/a.npy", "det_b": ""},
        detector_lookup={},
    )

    assert refs["det_a"] == (
        "h5ref:///tmp/session.h5#/measurements/pt_001/meas_0001/role_det_a/processed_signal"
    )
    assert refs["det_b"] == ""


def test_build_session_measurement_result_refs_falls_back_to_detector_metadata():
    owner = SimpleNamespace()
    session_manager = SimpleNamespace(
        session_path="/tmp/session.h5",
        schema=SimpleNamespace(DATASET_PROCESSED_SIGNAL="signal"),
    )

    refs = ZoneMeasurementsProcessResultsMixin._build_session_measurement_result_refs(
        owner,
        session_manager=session_manager,
        measurement_path="/measurements/pt_002/meas_0002",
        result_files={"DetX": "/tmp/x.npy"},
        detector_lookup={"DetX": {"id": "X1"}},
    )

    assert refs["DetX"] == "h5ref:///tmp/session.h5#/measurements/pt_002/meas_0002/det_x1/signal"


def test_get_point_identity_uses_explicit_getter_when_available():
    owner = SimpleNamespace(_get_point_identity_from_row=lambda row: ("uid-1", 7))

    assert ZoneMeasurementsProcessResultsMixin._get_point_identity_from_table_row(owner, 0) == (
        "uid-1",
        7,
    )


def test_get_point_identity_falls_back_to_table_item_data():
    user_role = results_module.Qt.UserRole
    item = _FakeTableItem(
        text="12",
        data_map={
            user_role: 12,
            user_role + 1: "12_deadbeef",
        },
    )
    owner = SimpleNamespace(pointsTable=_FakePointsTable({(0, 0): item}))

    assert ZoneMeasurementsProcessResultsMixin._get_point_identity_from_table_row(owner, 0) == (
        "12_deadbeef",
        12,
    )


def test_get_point_identity_builds_missing_uid_from_point_item():
    point_item = _FakePointItem({1: 5})
    owner = SimpleNamespace(
        pointsTable=_FakePointsTable({(0, 0): _FakeTableItem(text="5")}),
        image_view=SimpleNamespace(
            points_dict={
                "generated": {"points": [point_item]},
                "user": {"points": []},
            }
        ),
        _new_measurement_point_uid=lambda point_id: f"{point_id}_generated",
    )

    point_uid, point_id = ZoneMeasurementsProcessResultsMixin._get_point_identity_from_table_row(
        owner, 0
    )

    assert (point_uid, point_id) == ("5_generated", 5)
    assert point_item.data(2) == "5_generated"


def test_get_point_id_from_table_row_returns_only_id():
    owner = SimpleNamespace(_get_point_identity_from_table_row=lambda row: ("uid-9", 9))

    assert ZoneMeasurementsProcessResultsMixin._get_point_id_from_table_row(owner, 3) == 9


def test_get_or_create_measurement_widget_prefers_existing_widget(monkeypatch):
    _patch_pm(monkeypatch)
    widget = _MeasurementWidget()
    owner = SimpleNamespace(measurement_widgets={"uid-1": widget})

    result = ZoneMeasurementsProcessResultsMixin._get_or_create_measurement_widget(
        owner,
        "uid-1",
    )

    assert result is widget


def test_get_or_create_measurement_widget_uses_panel_insertion_when_available(monkeypatch):
    _patch_pm(monkeypatch)
    owner = SimpleNamespace(measurement_widgets={})

    def _add_to_panel(point_uid, point_display_id=None):
        owner.measurement_widgets[point_uid] = _MeasurementWidget()

    owner.add_measurement_widget_to_panel = _add_to_panel

    result = ZoneMeasurementsProcessResultsMixin._get_or_create_measurement_widget(
        owner,
        "uid-2",
        point_display_id=2,
    )

    assert result is owner.measurement_widgets["uid-2"]


def test_get_or_create_measurement_widget_builds_widget_from_technical_module(monkeypatch):
    _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        measurement_widgets={},
        masks={"mask": True},
        ponis={"det": "poni"},
        _zone_technical_imports_available=lambda: True,
        _get_zone_technical_module=lambda name: _MeasurementWidget,
    )

    result = ZoneMeasurementsProcessResultsMixin._get_or_create_measurement_widget(
        owner,
        "uid-3",
        point_display_id=3,
    )

    assert result is owner.measurement_widgets["uid-3"]
    assert result.kwargs["point_id"] == 3


def test_get_or_create_measurement_widget_returns_none_when_imports_missing(monkeypatch):
    logger, _warnings = _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        measurement_widgets={},
        _zone_technical_imports_available=lambda: False,
    )

    result = ZoneMeasurementsProcessResultsMixin._get_or_create_measurement_widget(
        owner,
        "uid-4",
    )

    assert result is None
    assert any(level == "error" for level, _args, _kwargs in logger.calls)


def test_add_measurement_to_table_returns_when_point_uid_is_missing(monkeypatch):
    logger, _warnings = _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        _get_point_identity_from_table_row=lambda row: (None, None),
    )

    ZoneMeasurementsProcessResultsMixin.add_measurement_to_table(
        owner,
        row=0,
        results={"ok": True},
        timestamp="2026-03-04 10:00:00",
    )

    assert any(level == "warning" for level, _args, _kwargs in logger.calls)


def test_add_measurement_to_table_updates_widget_and_tree_item(monkeypatch):
    _patch_pm(monkeypatch)
    widget = _MeasurementWidget()
    top_item = _FakeTreeItem()
    owner = SimpleNamespace(
        pointsTable=_FakePointsTable(
            {
                (2, 3): _FakeTableItem("1.25"),
                (2, 4): _FakeTableItem("-3.50"),
            }
        ),
        _get_point_identity_from_table_row=lambda row: ("7_deadbeef", 7),
        _get_or_create_measurement_widget=lambda point_uid, point_display_id=None: widget,
        add_measurement_widget_to_panel=lambda point_uid, point_display_id=None: None,
        _measurement_items={"7_deadbeef": (top_item, None, None)},
        _timestamp="2026-03-04 10:00:00",
    )

    ZoneMeasurementsProcessResultsMixin.add_measurement_to_table(
        owner,
        row=2,
        results={"value": 42},
        timestamp=None,
    )

    assert widget.mm_coordinates == [(1.25, -3.5)]
    assert widget.measurements == [({"value": 42}, "2026-03-04 10:00:00")]
    assert top_item.calls == [(0, "Point #7 1.25:-3.50 mm")]


def test_add_measurement_to_table_falls_back_to_window_title_without_mm_setter(monkeypatch):
    _patch_pm(monkeypatch)

    class _TitleOnlyWidget:
        def __init__(self) -> None:
            self.window_titles = []
            self.measurements = []

        def setWindowTitle(self, title: str) -> None:
            self.window_titles.append(title)

        def add_measurement(self, results, timestamp) -> None:
            self.measurements.append((results, timestamp))

    widget = _TitleOnlyWidget()
    owner = SimpleNamespace(
        pointsTable=_FakePointsTable({}),
        _get_point_identity_from_table_row=lambda row: ("uid-5", 5),
        _get_or_create_measurement_widget=lambda point_uid, point_display_id=None: widget,
        _timestamp="stamp",
    )

    ZoneMeasurementsProcessResultsMixin.add_measurement_to_table(
        owner,
        row=1,
        results={"value": 1},
        timestamp="manual",
    )

    assert widget.window_titles == ["Measurement History: Point #5"]
    assert widget.measurements == [({"value": 1}, "manual")]


def test_pause_measurements_toggles_pause_state_and_resumes(monkeypatch):
    logger, _warnings = _patch_pm(monkeypatch)
    resumed = []
    owner = SimpleNamespace(
        paused=False,
        pause_btn=_FakeButton(),
        measure_next_point=lambda: resumed.append(True),
    )

    ZoneMeasurementsProcessResultsMixin.pause_measurements(owner)
    assert owner.paused is True
    assert owner.pause_btn.text == "Resume"

    ZoneMeasurementsProcessResultsMixin.pause_measurements(owner)
    assert owner.paused is False
    assert owner.pause_btn.text == "Pause"
    assert resumed == [True]
    assert len(logger.calls) >= 2


def test_skip_current_point_returns_early_for_stopped_or_invalid_bounds(monkeypatch):
    _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        stopped=True,
        total_points=1,
        current_measurement_sorted_index=0,
        sorted_indices=[0],
    )

    ZoneMeasurementsProcessResultsMixin.skip_current_point(owner)

    owner = SimpleNamespace(
        stopped=False,
        total_points=0,
        current_measurement_sorted_index=0,
        sorted_indices=[],
    )
    ZoneMeasurementsProcessResultsMixin.skip_current_point(owner)


def test_skip_current_point_skips_when_user_cancels(monkeypatch):
    _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        stopped=False,
        total_points=2,
        current_measurement_sorted_index=0,
        sorted_indices=[5, 6],
        _skip_point_by_row=lambda row, reason: (_ for _ in ()).throw(RuntimeError("should not be called")),
    )
    from PyQt5.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("", False))

    ZoneMeasurementsProcessResultsMixin.skip_current_point(owner)


def test_skip_current_point_records_skip_reason_and_log(monkeypatch):
    _patch_pm(monkeypatch)
    calls = []
    logs = []
    owner = SimpleNamespace(
        stopped=False,
        total_points=2,
        current_measurement_sorted_index=1,
        sorted_indices=[3, 8],
        _skip_point_by_row=lambda row, reason: calls.append((row, reason)) or True,
        _append_capture_log=lambda message: logs.append(message),
    )
    from PyQt5.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("because", True))

    ZoneMeasurementsProcessResultsMixin.skip_current_point(owner)

    assert calls == [(8, "because")]
    assert logs == ["Point 2: skipped (because)"]


def test_stop_measurements_resets_ui_state(monkeypatch):
    logger, _warnings = _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        stopped=False,
        paused=True,
        current_measurement_sorted_index=5,
        progressBar=_FakeProgressBar(),
        timeRemainingLabel=_FakeLabel(),
        start_btn=_FakeButton(),
        pause_btn=_FakeButton(),
        stop_btn=_FakeButton(),
        skip_btn=_FakeButton(),
    )

    ZoneMeasurementsProcessResultsMixin.stop_measurements(owner)

    assert owner.stopped is True
    assert owner.paused is False
    assert owner.current_measurement_sorted_index == 0
    assert owner.progressBar.value == 0
    assert owner.timeRemainingLabel.text == "Measurement stopped."
    assert owner.start_btn.enabled is True
    assert owner.pause_btn.text == "Pause"
    assert owner.pause_btn.enabled is False
    assert owner.stop_btn.enabled is False
    assert owner.skip_btn.enabled is False
    assert any(level == "info" for level, _args, _kwargs in logger.calls)


def test_confirm_poni_settings_warns_when_active_detector_is_missing_calibration(monkeypatch):
    _logger, warnings = _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        hardware_controller=SimpleNamespace(active_detector_aliases=["det_a", "det_b"]),
        ponis={"det_a": "poni"},
        poni_files={},
        config={"DEV": False, "detectors": []},
    )

    result = ZoneMeasurementsProcessResultsMixin._confirm_poni_settings_before_measurement(owner)

    assert result is False
    assert warnings and warnings[0][0] == "Missing PONI Calibration"


def test_confirm_poni_settings_uses_config_fallback_and_passes_when_complete(monkeypatch):
    _logger, warnings = _patch_pm(monkeypatch)

    class _BrokenHardwareController:
        @property
        def active_detector_aliases(self):
            raise RuntimeError("offline")

    owner = SimpleNamespace(
        hardware_controller=_BrokenHardwareController(),
        ponis={"det_a": "poni"},
        poni_files={},
        config={
            "DEV": False,
            "active_detectors": ["A"],
            "detectors": [{"id": "A", "alias": "det_a"}],
        },
    )

    result = ZoneMeasurementsProcessResultsMixin._confirm_poni_settings_before_measurement(owner)

    assert result is True
    assert warnings == []
