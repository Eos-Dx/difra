from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
import difra.gui.main_window_ext.zone_measurements.logic.process_results_mixin as results_module
from difra.gui.technical.widgets import DetectorProfilePreview
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


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, *args, **kwargs) -> None:
        for callback in list(self.callbacks):
            try:
                callback(*args, **kwargs)
            except TypeError:
                callback()


class _FakeThread:
    def __init__(self, *args, **kwargs) -> None:
        self.started = _FakeSignal()
        self.finished = _FakeSignal()
        self.started_called = 0
        self.quit_called = 0
        self.deleted = 0

    def start(self) -> None:
        self.started_called += 1
        self.started.emit()

    def quit(self) -> None:
        self.quit_called += 1

    def deleteLater(self) -> None:
        self.deleted += 1


class _FakeColor:
    def __init__(self, r: int, g: int, b: int) -> None:
        self.rgb = (r, g, b)
        self.alpha = None

    def setAlphaF(self, value: float) -> None:
        self.alpha = value


class _FakeQTimer:
    calls = []

    @classmethod
    def singleShot(cls, delay_ms: int, callback) -> None:
        cls.calls.append((delay_ms, callback))


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
        self.values = []

    def setValue(self, value: int) -> None:
        self.values.append(value)


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


class _FakeBrushItem:
    def __init__(self) -> None:
        self.brushes = []

    def setBrush(self, brush) -> None:
        self.brushes.append(brush)


class _FakeMeasurementWorker:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.measurement_ready = _FakeSignal()
        self.thread = None
        self.run_called = 0
        self.deleted = 0

    def moveToThread(self, thread) -> None:
        self.thread = thread

    def run(self) -> None:
        self.run_called += 1

    def deleteLater(self) -> None:
        self.deleted += 1


class _ResultsHarness(ZoneMeasurementsProcessResultsMixin):
    pass


def _patch_pm(monkeypatch, *, with_ui: bool = False):
    logger = _FakeLogger()
    warnings = []
    pm = SimpleNamespace(
        logger=logger,
        QMessageBox=SimpleNamespace(
            warning=lambda *args: warnings.append(args[1:3]),
        ),
    )
    if with_ui:
        _FakeQTimer.calls.clear()
        pm.QThread = _FakeThread
        pm.QColor = _FakeColor
        pm.QTimer = _FakeQTimer
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


def test_update_profile_previews_targets_specific_point_uid():
    calls = []
    owner = SimpleNamespace(
        _extract_profile_from_measurement=lambda measurement_ref: [1.0, 2.0, 3.0],
        update_detector_profile_preview=lambda alias, profile, point_uid=None: calls.append(
            (alias, tuple(profile), point_uid)
        ),
    )

    ZoneMeasurementsProcessResultsMixin._update_profile_previews_from_result_files(
        owner,
        {"PRIMARY": "/tmp/a.npy", "SECONDARY": "/tmp/b.npy"},
        point_uid="7_deadbeef",
    )

    assert calls == [
        ("PRIMARY", (1.0, 2.0, 3.0), "7_deadbeef"),
        ("SECONDARY", (1.0, 2.0, 3.0), "7_deadbeef"),
    ]


def test_update_profile_previews_falls_back_to_legacy_updater_signature():
    calls = []
    owner = SimpleNamespace(
        _extract_profile_from_measurement=lambda measurement_ref: [1.0, 2.0],
        update_detector_profile_preview=lambda alias, profile: calls.append(
            (alias, tuple(profile))
        ),
    )

    ZoneMeasurementsProcessResultsMixin._update_profile_previews_from_result_files(
        owner,
        {"PRIMARY": "/tmp/a.npy"},
        point_uid="9_feedbeef",
    )

    assert calls == [("PRIMARY", (1.0, 2.0))]


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


def test_spawn_measurement_thread_returns_when_technical_imports_missing(monkeypatch):
    logger, _warnings = _patch_pm(monkeypatch)
    owner = _ResultsHarness()
    owner._zone_technical_imports_available = lambda: False

    owner.spawn_measurement_thread(row=1, file_map={"det_a": "/tmp/a.npy"})

    assert any(level == "error" for level, _args, _kwargs in logger.calls)


def test_spawn_measurement_thread_wires_worker_and_starts_thread(monkeypatch):
    _patch_pm(monkeypatch, with_ui=True)
    owner = _ResultsHarness()
    owner._zone_technical_imports_available = lambda: True
    owner._get_zone_technical_module = lambda name: _FakeMeasurementWorker
    owner.masks = {"m": 1}
    owner.ponis = {"p": 2}

    owner.spawn_measurement_thread(row=4, file_map={"det_a": "/tmp/a.npy"})

    assert len(owner._measurement_threads) == 1
    thread, worker = owner._measurement_threads[0]
    assert isinstance(thread, _FakeThread)
    assert isinstance(worker, _FakeMeasurementWorker)
    assert worker.kwargs["row"] == 4
    assert worker.kwargs["filenames"] == {"det_a": "/tmp/a.npy"}
    assert worker.run_called == 1


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


def test_measurement_finished_advances_progress_and_calls_next_point(monkeypatch):
    _patch_pm(monkeypatch)
    next_calls = []
    owner = _ResultsHarness()
    owner.stopped = False
    owner.paused = False
    owner.current_measurement_sorted_index = 0
    owner.total_points = 3
    owner.progressBar = _FakeProgressBar()
    owner.measurementStartTime = time.time() - 6
    owner.timeRemainingLabel = _FakeLabel()
    owner.pause_btn = _FakeButton()
    owner.stop_btn = _FakeButton()
    owner.start_btn = _FakeButton()
    owner.measure_next_point = lambda: next_calls.append(True)

    owner.measurement_finished()

    assert owner.current_measurement_sorted_index == 1
    assert owner.progressBar.values[-1] == 1
    assert next_calls == [True]
    assert "done" in owner.timeRemainingLabel.text


def test_measurement_finished_completes_sequence_and_disables_controls(monkeypatch):
    _patch_pm(monkeypatch)
    logs = []
    owner = _ResultsHarness()
    owner.stopped = False
    owner.paused = False
    owner.current_measurement_sorted_index = 0
    owner.total_points = 1
    owner.progressBar = _FakeProgressBar()
    owner.measurementStartTime = time.time() - 2
    owner.timeRemainingLabel = _FakeLabel()
    owner.pause_btn = _FakeButton()
    owner.stop_btn = _FakeButton()
    owner.start_btn = _FakeButton()
    owner.skip_btn = _FakeButton()
    owner._append_measurement_log = lambda message: logs.append(message)
    owner.measure_next_point = lambda: (_ for _ in ()).throw(RuntimeError("should not run"))

    owner.measurement_finished()

    assert owner.current_measurement_sorted_index == 1
    assert owner.pause_btn.enabled is False
    assert owner.stop_btn.enabled is False
    assert owner.start_btn.enabled is True
    assert owner.skip_btn.enabled is False
    assert "[CAPTURE] Measurement sequence complete" in logs


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
    assert owner.progressBar.values[-1] == 0
    assert owner.timeRemainingLabel.text == "Measurement stopped."
    assert owner.start_btn.enabled is True
    assert owner.pause_btn.text == "Pause"
    assert owner.pause_btn.enabled is False
    assert owner.stop_btn.enabled is False
    assert owner.skip_btn.enabled is False
    assert any(level == "info" for level, _args, _kwargs in logger.calls)


def test_on_capture_finished_failure_marks_session_point(monkeypatch):
    _patch_pm(monkeypatch, with_ui=True)
    logs = []
    failed_calls = []
    stopped = []
    owner = _ResultsHarness()
    owner.current_measurement_sorted_index = 0
    owner._current_session_point_index = lambda: 5
    owner._stop_capture_progress_logging = lambda: stopped.append(True)
    owner.session_manager = SimpleNamespace(
        is_session_active=lambda: True,
        fail_point_measurement=lambda **kwargs: failed_calls.append(kwargs),
    )
    owner._append_measurement_log = lambda message: logs.append(message)

    owner.on_capture_finished(False, {"det_a": "/tmp/a.npy"})

    assert failed_calls and failed_calls[0]["point_index"] == 5
    assert failed_calls[0]["reason"] == "capture_failed"
    assert stopped == [True]
    assert "[CAPTURE] Point 5: capture failed" in logs
    assert "[SESSION] Point 5: marked failed in session container" in logs


def test_on_capture_finished_success_without_session_spawns_postprocess(monkeypatch):
    _patch_pm(monkeypatch, with_ui=True)
    spawned = []
    owner = _ResultsHarness()
    owner.current_measurement_sorted_index = 0
    owner.sorted_indices = [12]
    owner.session_manager = None
    owner.config = {
        "detectors": [
            {
                "alias": "det_a",
                "id": "A1",
                "type": "pixet",
                "size": [256, 256],
                "pixel_size_um": 55,
                "faulty_pixels": [],
            }
        ]
    }
    owner.state_measurements = {
        "measurements_meta": {},
        "measurement_points": [{"unique_id": "1_deadbeef"}],
    }
    owner._x_mm = 1.5
    owner._y_mm = -2.5
    owner._base_name = "sample"
    owner.integration_time = 0.25
    owner.calibration_group_hash = "group-hash"
    owner._dump_state_measurements = lambda: None
    owner.state_path_measurements = "/tmp/state.json"
    owner.spawn_measurement_thread = lambda row, file_map: spawned.append((row, dict(file_map)))
    owner._point_item = _FakeBrushItem()
    owner._zone_item = _FakeBrushItem()
    owner.measurement_finished = lambda: None
    owner._append_measurement_log = lambda message: None

    owner.on_capture_finished(True, {"det_a": "/tmp/det_a.npy"})

    assert "det_a.npy" in owner.state_measurements["measurements_meta"]
    meta = owner.state_measurements["measurements_meta"]["det_a.npy"]
    assert meta["unique_id"] == "1_deadbeef"
    assert meta["CALIBRATION_GROUP_HASH"] == "group-hash"
    assert spawned == [(12, {"det_a": "/tmp/det_a.npy"})]
    assert owner._point_item.brushes
    assert owner._zone_item.brushes
    assert _FakeQTimer.calls and _FakeQTimer.calls[-1][0] == 1000


def test_on_capture_finished_session_write_failure_marks_point_failed(monkeypatch):
    _patch_pm(monkeypatch, with_ui=True)
    spawned = []
    failed_calls = []
    session_manager = SimpleNamespace(
        session_path="/tmp/session.h5",
        is_session_active=lambda: True,
        fail_point_measurement=lambda **kwargs: failed_calls.append(kwargs),
    )
    owner = _ResultsHarness()
    owner.current_measurement_sorted_index = 0
    owner._current_session_point_index = lambda: 3
    owner.sorted_indices = [0]
    owner.session_manager = session_manager
    owner.config = {"detectors": [{"alias": "det_a", "id": "A1"}]}
    owner.detector_controller = {}
    owner.state_measurements = {
        "measurements_meta": {},
        "measurement_points": [{"unique_id": "3_deadbeef"}],
    }
    owner._x_mm = 0.0
    owner._y_mm = 0.0
    owner._base_name = "sample"
    owner.integration_time = 1.0
    owner._timestamp = "20260305_120000"
    owner._dump_state_measurements = lambda: None
    owner.state_path_measurements = "/tmp/state.json"
    owner.spawn_measurement_thread = lambda row, file_map: spawned.append((row, dict(file_map)))
    owner._point_item = _FakeBrushItem()
    owner._zone_item = _FakeBrushItem()
    owner.measurement_finished = lambda: None
    owner._append_measurement_log = lambda message: None

    owner.on_capture_finished(True, {"det_a": "/tmp/missing_file.npy"})

    reasons = [call["reason"] for call in failed_calls]
    assert "capture_success_without_payload" in reasons
    assert any(reason.startswith("h5_write_failed:") for reason in reasons)
    assert spawned == [(0, {"det_a": "/tmp/missing_file.npy"})]


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


def test_detector_profile_preview_uses_log_normalization():
    normalized = DetectorProfilePreview._normalize_profile_log([1.0, 10.0, 100.0])

    assert len(normalized) == 3
    assert normalized[0] == pytest.approx(0.0)
    assert normalized[1] == pytest.approx(0.5)
    assert normalized[2] == pytest.approx(1.0)


def test_detector_profile_preview_clamps_non_positive_values_to_smallest_positive():
    normalized = DetectorProfilePreview._normalize_profile_log([0.0, 1.0, 100.0])

    assert len(normalized) == 3
    assert normalized[0] == pytest.approx(0.0)
    assert normalized[1] == pytest.approx(0.0)
    assert normalized[2] == pytest.approx(1.0)
