from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import difra.gui.main_window_ext.zone_measurements.logic.process_capture_mixin as capture_module
from difra.gui.main_window_ext.zone_measurements.logic.process_capture_mixin import (
    ZoneMeasurementsProcessCaptureMixin,
    _place_raw_capture_file,
)


class _StubStageController:
    def __init__(self):
        self.calls = []

    def get_home_load_positions(self):
        self.calls.append("get_home_load_positions")
        return {"load": (11.0, -5.5)}


class _FakeAttenuationController:
    def __init__(self, npy_data=None):
        self.calls = []
        self.npy_data = np.array(npy_data if npy_data is not None else [[1.0, 2.0]])

    def convert_to_container_format(self, txt_path: str, _container_version: str) -> str:
        self.calls.append(txt_path)
        npy_path = str(Path(txt_path).with_suffix(".npy"))
        np.save(npy_path, self.npy_data)
        return npy_path


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
    def __init__(self) -> None:
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


class _FakeCaptureWorker:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.finished = _FakeSignal()
        self.thread = None
        self.run_called = 0
        self.deleted = 0

    def moveToThread(self, thread) -> None:
        self.thread = thread

    def run(self) -> None:
        self.run_called += 1

    def deleteLater(self) -> None:
        self.deleted += 1


class _FakeCheckBox:
    def __init__(self, checked: bool) -> None:
        self._checked = checked

    def isChecked(self) -> bool:
        return self._checked


class _StubButton:
    def __init__(self) -> None:
        self.values = []

    def setEnabled(self, value):
        self.values.append(value)


class _StubProgressBar:
    def __init__(self) -> None:
        self.max_values = []
        self.values = []

    def setMaximum(self, value):
        self.max_values.append(value)

    def setValue(self, value):
        self.values.append(value)


class _StubLineEdit:
    def __init__(self, value):
        self._value = value

    def text(self):
        return self._value


class _StubSpin:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _StubCenter:
    def __init__(self, x, y):
        self._x = x
        self._y = y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _StubRect:
    def __init__(self, x, y):
        self._center = _StubCenter(x, y)

    def center(self):
        return self._center


class _StubPointItem:
    def __init__(self, x, y):
        self._rect = _StubRect(x, y)

    def sceneBoundingRect(self):
        return self._rect


def _patch_pm(monkeypatch):
    logger_calls = []
    warnings = []

    logger = SimpleNamespace(
        info=lambda *args, **kwargs: logger_calls.append(("info", args, kwargs)),
        warning=lambda *args, **kwargs: logger_calls.append(("warning", args, kwargs)),
        error=lambda *args, **kwargs: logger_calls.append(("error", args, kwargs)),
        debug=lambda *args, **kwargs: logger_calls.append(("debug", args, kwargs)),
    )
    pm = SimpleNamespace(
        logger=logger,
        QThread=_FakeThread,
        QMessageBox=SimpleNamespace(
            warning=lambda *args: warnings.append(args[1:3]),
        ),
    )
    monkeypatch.setattr(capture_module, "_pm", lambda: pm)
    return logger_calls, warnings


def test_place_raw_capture_file_noops_when_source_matches_target_and_copies_dsc(tmp_path: Path):
    target_txt = tmp_path / "capture.txt"
    target_dsc = tmp_path / "capture.dsc"
    target_txt.write_text("txt", encoding="utf-8")
    target_dsc.write_text("dsc", encoding="utf-8")

    _place_raw_capture_file(str(target_txt), tmp_path / "capture.txt", allow_move=True)

    assert target_txt.read_text(encoding="utf-8") == "txt"
    assert target_dsc.read_text(encoding="utf-8") == "dsc"


def test_place_raw_capture_file_moves_or_copies_files(tmp_path: Path):
    src_txt = tmp_path / "source.txt"
    src_dsc = tmp_path / "source.dsc"
    src_txt.write_text("txt", encoding="utf-8")
    src_dsc.write_text("dsc", encoding="utf-8")
    target_txt = tmp_path / "nested" / "target.txt"
    target_dsc = target_txt.with_suffix(".dsc")

    _place_raw_capture_file(str(src_txt), target_txt, allow_move=False)

    assert src_txt.exists()
    assert src_dsc.exists()
    assert target_txt.read_text(encoding="utf-8") == "txt"
    assert target_dsc.read_text(encoding="utf-8") == "dsc"


def test_current_session_point_index_uses_mapping_and_fallback():
    owner = SimpleNamespace(
        current_measurement_sorted_index=1,
        _session_point_indices=[10, 20, 30],
    )

    assert ZoneMeasurementsProcessCaptureMixin._current_session_point_index(owner) == 20

    owner = SimpleNamespace(current_measurement_sorted_index="bad", _session_point_indices=None)
    assert ZoneMeasurementsProcessCaptureMixin._current_session_point_index(owner) == 1


def test_get_loading_position_prefers_config_and_falls_back_to_stage_controller():
    owner = SimpleNamespace(
        config={"attenuation": {"loading_position": {"x": "3.5", "y": "-2.0"}}},
        stage_controller=_StubStageController(),
    )

    assert ZoneMeasurementsProcessCaptureMixin._get_loading_position(owner) == (3.5, -2.0)

    owner = SimpleNamespace(
        config={},
        stage_controller=_StubStageController(),
    )
    assert ZoneMeasurementsProcessCaptureMixin._get_loading_position(owner) == (11.0, -5.5)
    assert owner.stage_controller.calls == ["get_home_load_positions"]


def test_get_loading_position_returns_none_pair_when_unavailable():
    owner = SimpleNamespace(config={}, stage_controller=None)

    assert ZoneMeasurementsProcessCaptureMixin._get_loading_position(owner) == (None, None)


def test_record_attenuation_files_stores_files_for_current_measurement_and_dumps():
    dumped = []
    owner = SimpleNamespace(
        state_measurements={
            "measurement_points": [{"unique_id": "1_deadbeef"}],
        },
        current_measurement_sorted_index=0,
        state_path_measurements="state.json",
        _dump_state_measurements=lambda: dumped.append(True),
    )

    ZoneMeasurementsProcessCaptureMixin._record_attenuation_files(
        owner,
        "without_sample",
        {"det_a": "/tmp/a.npy"},
    )

    assert dumped == [True]
    assert owner.state_measurements["attenuation_files"] == {
        "1_deadbeef": {"without_sample": {"det_a": "/tmp/a.npy"}}
    }


def test_record_attenuation_files_noops_without_uid(tmp_path: Path):
    path = tmp_path / "state.json"
    owner = SimpleNamespace(
        state_measurements={"measurement_points": []},
        current_measurement_sorted_index=3,
        state_path_measurements=str(path),
    )

    ZoneMeasurementsProcessCaptureMixin._record_attenuation_files(
        owner,
        "with_sample",
        {"det_a": "/tmp/a.npy"},
    )

    assert not path.exists()


def test_record_attenuation_files_writes_json_when_dump_helper_is_missing(tmp_path: Path):
    path = tmp_path / "state.json"
    owner = SimpleNamespace(
        state_measurements={
            "measurement_points": [{"unique_id": "2_deadbeef"}],
        },
        current_measurement_sorted_index=0,
        state_path_measurements=str(path),
    )

    ZoneMeasurementsProcessCaptureMixin._record_attenuation_files(
        owner,
        "with_sample",
        {"det_b": "/tmp/b.npy"},
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["attenuation_files"] == {
        "2_deadbeef": {"with_sample": {"det_b": "/tmp/b.npy"}}
    }


def test_capture_attenuation_background_skips_when_loading_position_missing(monkeypatch):
    logs = []
    owner = SimpleNamespace(
        config={},
        detector_controller={},
        _get_loading_position=lambda: (None, None),
        _append_capture_log=lambda message: logs.append(message),
        fileNameLineEdit=SimpleNamespace(text=lambda: "sample"),
        measurement_folder="/tmp",
    )
    logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(capture_module, "_pm", lambda: SimpleNamespace(logger=logger))

    ZoneMeasurementsProcessCaptureMixin._capture_attenuation_background(owner)

    assert owner._attenuation_bg_files is None
    assert logs[-1] == "I0 skipped: loading position is not configured"


def test_measure_next_point_respects_stopped_and_paused_states(monkeypatch):
    logger_calls, _warnings = _patch_pm(monkeypatch)
    owner = SimpleNamespace(stopped=True, paused=False)

    ZoneMeasurementsProcessCaptureMixin.measure_next_point(owner)

    owner = SimpleNamespace(stopped=False, paused=True)
    ZoneMeasurementsProcessCaptureMixin.measure_next_point(owner)

    debug_messages = [args[0] for level, args, _kwargs in logger_calls if level == "debug"]
    assert "Measurement stopped" in debug_messages
    assert "Measurement is paused. Waiting for resume" in debug_messages


def test_measure_next_point_finishes_when_all_points_measured(monkeypatch):
    _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        stopped=False,
        paused=False,
        sorted_indices=[0],
        total_points=1,
        current_measurement_sorted_index=1,
        progressBar=_StubProgressBar(),
        start_btn=_StubButton(),
        pause_btn=_StubButton(),
        stop_btn=_StubButton(),
        skip_btn=_StubButton(),
    )

    ZoneMeasurementsProcessCaptureMixin.measure_next_point(owner)

    assert owner.progressBar.values == [1]
    assert owner.start_btn.values[-1] is True
    assert owner.pause_btn.values[-1] is False
    assert owner.stop_btn.values[-1] is False
    assert owner.skip_btn.values[-1] is False


def test_measure_next_point_routes_to_attenuation_flow_when_checkbox_enabled(monkeypatch):
    _patch_pm(monkeypatch)
    calls = []
    owner = SimpleNamespace(
        stopped=False,
        paused=False,
        current_measurement_sorted_index=0,
        sorted_indices=[0],
        total_points=1,
        progressBar=_StubProgressBar(),
        start_btn=_StubButton(),
        pause_btn=_StubButton(),
        stop_btn=_StubButton(),
        skip_btn=_StubButton(),
        fileNameLineEdit=_StubLineEdit("sample"),
        measurement_folder="/tmp",
        integration_time=1.0,
        real_x_pos_mm=_StubSpin(0.0),
        real_y_pos_mm=_StubSpin(0.0),
        include_center=(0.0, 0.0),
        pixel_to_mm_ratio=1.0,
        update_xy_pos=lambda: None,
        _append_capture_log=lambda _msg: None,
        _append_hw_log=lambda _msg: None,
        attenuationCheckBox=_FakeCheckBox(True),
        state_measurements={
            "measurement_points": [{"x": 2.5, "y": -1.0}],
        },
        state={},
        image_view=SimpleNamespace(
            points_dict={
                "generated": {"points": [_StubPointItem(0.0, 0.0)], "zones": [object()]},
                "user": {"points": [], "zones": []},
            }
        ),
        _start_attenuation_then_normal=lambda txt_base: calls.append(txt_base),
    )

    ZoneMeasurementsProcessCaptureMixin.measure_next_point(owner)

    assert len(calls) == 1


def test_start_normal_capture_returns_when_technical_imports_missing(monkeypatch):
    logger_calls, _warnings = _patch_pm(monkeypatch)
    logs = []
    owner = SimpleNamespace(
        _zone_technical_imports_available=lambda: False,
        _append_capture_log=lambda message: logs.append(message),
    )

    ZoneMeasurementsProcessCaptureMixin._start_normal_capture(owner, "/tmp/base")

    assert logs == ["Error: technical imports unavailable"]
    assert any(level == "error" for level, _args, _kwargs in logger_calls)


def test_start_normal_capture_creates_worker_thread_and_marks_session_start(monkeypatch):
    _patch_pm(monkeypatch)
    monkeypatch.setattr(capture_module, "get_container_version", lambda config: "0.2")
    session_calls = []
    session_manager = SimpleNamespace(
        is_session_active=lambda: True,
        begin_point_measurement=lambda **kwargs: session_calls.append(("begin", kwargs)),
        log_event=lambda **kwargs: session_calls.append(("log", kwargs)),
    )
    capture_logs = []
    session_logs = []
    owner = SimpleNamespace(
        _zone_technical_imports_available=lambda: True,
        _get_zone_technical_module=lambda name: _FakeCaptureWorker,
        current_measurement_sorted_index=0,
        total_points=2,
        integration_time=0.5,
        detector_controller={"det_a": object()},
        config={"detectors": []},
        hardware_client=object(),
        _x_mm=1.0,
        _y_mm=2.0,
        _append_capture_log=lambda message: capture_logs.append(message),
        _append_session_log=lambda message: session_logs.append(message),
        _current_session_point_index=lambda: 7,
        session_manager=session_manager,
        on_capture_finished=lambda success, files: None,
    )

    ZoneMeasurementsProcessCaptureMixin._start_normal_capture(owner, "/tmp/sample")

    assert isinstance(owner.capture_worker, _FakeCaptureWorker)
    assert isinstance(owner.capture_thread, _FakeThread)
    assert owner.capture_worker.kwargs["naming_mode"] == "normal"
    assert owner.capture_worker.kwargs["container_version"] == "0.2"
    assert owner.capture_thread.started_called == 1
    assert owner.capture_worker.run_called == 1
    assert session_calls and session_calls[0][0] == "begin"
    assert "Normal capture worker started" in capture_logs
    assert session_logs and "opened in session container" in session_logs[0]


def test_start_normal_capture_continues_when_session_begin_fails(monkeypatch):
    _patch_pm(monkeypatch)
    monkeypatch.setattr(capture_module, "get_container_version", lambda config: "0.2")
    capture_logs = []
    session_logs = []
    session_manager = SimpleNamespace(
        is_session_active=lambda: True,
        begin_point_measurement=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    owner = SimpleNamespace(
        _zone_technical_imports_available=lambda: True,
        _get_zone_technical_module=lambda name: _FakeCaptureWorker,
        current_measurement_sorted_index=0,
        total_points=1,
        integration_time=1.0,
        detector_controller={"det_a": object()},
        config={"detectors": []},
        hardware_client=None,
        _x_mm=0.0,
        _y_mm=0.0,
        _append_capture_log=lambda message: capture_logs.append(message),
        _append_session_log=lambda message: session_logs.append(message),
        _current_session_point_index=lambda: 1,
        session_manager=session_manager,
        on_capture_finished=lambda success, files: None,
    )

    ZoneMeasurementsProcessCaptureMixin._start_normal_capture(owner, "/tmp/sample")

    assert isinstance(owner.capture_worker, _FakeCaptureWorker)
    assert any("failed to mark session start" in message for message in session_logs)
    assert "Normal capture worker started" in capture_logs


def test_start_attenuation_then_normal_warns_if_background_missing(monkeypatch):
    _logger_calls, warnings = _patch_pm(monkeypatch)
    capture_logs = []
    owner = SimpleNamespace(
        _reuse_existing_i0_from_session=False,
        _attenuation_bg_files=None,
        _move_stage=lambda x, y, timeout_s=15: None,
        _x_mm=1.0,
        _y_mm=2.0,
        _append_hw_log=lambda message: None,
        _zone_technical_imports_available=lambda: False,
        _append_capture_log=lambda message: capture_logs.append(message),
    )

    ZoneMeasurementsProcessCaptureMixin._start_attenuation_then_normal(owner, "/tmp/base")

    assert warnings and warnings[0][0] == "Attenuation Background Missing"
    assert capture_logs[-1] == "Error: technical imports unavailable for attenuation"


def test_capture_attenuation_background_skips_when_move_to_loading_fails(monkeypatch):
    logs = []
    _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        config={},
        detector_controller={},
        _get_loading_position=lambda: (1.0, 2.0),
        _move_stage=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("stage blocked")),
        _append_capture_log=lambda message: logs.append(message),
        _append_hw_log=lambda message: None,
        fileNameLineEdit=_StubLineEdit("sample"),
        measurement_folder="/tmp",
    )

    ZoneMeasurementsProcessCaptureMixin._capture_attenuation_background(owner)

    assert owner._attenuation_bg_files is None
    assert "cannot move to loading position" in logs[-1]


def test_capture_attenuation_background_skips_when_hardware_client_is_missing(monkeypatch):
    logs = []
    monkeypatch.setattr(capture_module, "get_container_version", lambda config: "0.2")
    _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        config={},
        detector_controller={},
        _get_loading_position=lambda: (1.0, 2.0),
        _move_stage=lambda *args, **kwargs: (1.0, 2.0),
        _append_capture_log=lambda message: logs.append(message),
        _append_hw_log=lambda message: None,
        fileNameLineEdit=_StubLineEdit("sample"),
        measurement_folder="/tmp",
        hardware_client=None,
    )

    ZoneMeasurementsProcessCaptureMixin._capture_attenuation_background(owner)

    assert owner._attenuation_bg_files is None
    assert logs[-1] == "I0 skipped: hardware client unavailable"


def test_capture_attenuation_background_handles_capture_exposure_errors(monkeypatch):
    logs = []
    monkeypatch.setattr(capture_module, "get_container_version", lambda config: "0.2")
    _patch_pm(monkeypatch)
    owner = SimpleNamespace(
        config={},
        detector_controller={},
        _get_loading_position=lambda: (1.0, 2.0),
        _move_stage=lambda *args, **kwargs: (1.0, 2.0),
        _append_capture_log=lambda message: logs.append(message),
        _append_hw_log=lambda message: None,
        fileNameLineEdit=_StubLineEdit("sample"),
        measurement_folder="/tmp",
        hardware_client=SimpleNamespace(
            capture_exposure=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("capture failed"))
        ),
    )

    ZoneMeasurementsProcessCaptureMixin._capture_attenuation_background(owner)

    assert owner._attenuation_bg_files is None
    assert logs[-1] == "I0 capture failed: capture failed"


def test_capture_attenuation_background_saves_results_and_records_session(monkeypatch, tmp_path: Path):
    logs = []
    session_logs = []
    session_calls = []
    monkeypatch.setattr(capture_module, "get_container_version", lambda config: "0.2")
    _patch_pm(monkeypatch)
    raw_txt = tmp_path / "raw.txt"
    raw_dsc = tmp_path / "raw.dsc"
    raw_txt.write_text("1 2 3\n4 5 6\n", encoding="utf-8")
    raw_dsc.write_text("DSC", encoding="utf-8")
    controller = _FakeAttenuationController([[1.0, 2.0], [3.0, 4.0]])
    owner = SimpleNamespace(
        config={"detectors": [{"alias": "det_a", "id": "A1"}]},
        detector_controller={"det_a": controller},
        _get_loading_position=lambda: (3.0, -2.0),
        _move_stage=lambda *args, **kwargs: (3.0, -2.0),
        _append_capture_log=lambda message: logs.append(message),
        _append_session_log=lambda message: session_logs.append(message),
        _append_hw_log=lambda message: None,
        fileNameLineEdit=_StubLineEdit("sample"),
        measurement_folder=str(tmp_path),
        hardware_client=SimpleNamespace(
            capture_exposure=lambda **kwargs: {"det_a": str(raw_txt)}
        ),
        session_manager=SimpleNamespace(
            is_session_active=lambda: True,
            add_attenuation_measurement=lambda **kwargs: session_calls.append(kwargs),
        ),
    )

    ZoneMeasurementsProcessCaptureMixin._capture_attenuation_background(owner)

    assert "det_a" in owner._attenuation_bg_files
    out_npy = Path(owner._attenuation_bg_files["det_a"])
    assert out_npy.exists()
    assert logs[-1] == "I0 saved for 1 detector(s)"
    assert session_calls and session_calls[0]["mode"] == "without"
    assert "A1" in session_calls[0]["measurement_data"]
    assert any("I0 saved to session container" in entry for entry in session_logs)


def test_start_attenuation_then_normal_emits_callback_and_starts_normal_capture(monkeypatch):
    _patch_pm(monkeypatch)
    monkeypatch.setattr(capture_module, "get_container_version", lambda config: "0.2")
    capture_logs = []
    record_calls = []
    normal_calls = []
    owner = SimpleNamespace(
        attenFramesSpin=SimpleNamespace(value=lambda: 5),
        attenTimeSpin=SimpleNamespace(value=lambda: 0.01),
        _reuse_existing_i0_from_session=True,
        _attenuation_bg_files={"det_a": "/tmp/bg.npy"},
        _record_attenuation_files=lambda key, files: record_calls.append((key, files)),
        _move_stage=lambda x, y, timeout_s=15: None,
        _x_mm=1.0,
        _y_mm=2.0,
        _append_hw_log=lambda message: None,
        _zone_technical_imports_available=lambda: True,
        _append_capture_log=lambda message: capture_logs.append(message),
        _append_session_log=lambda message: capture_logs.append(f"SESSION:{message}"),
        _start_normal_capture=lambda txt_base: normal_calls.append(txt_base),
        _get_zone_technical_module=lambda name: _FakeCaptureWorker,
        detector_controller={"det_a": object()},
        config={"detectors": []},
        hardware_client=None,
        measurement_folder="/tmp",
        fileNameLineEdit=_StubLineEdit("sample"),
        session_manager=SimpleNamespace(is_session_active=lambda: False),
    )

    ZoneMeasurementsProcessCaptureMixin._start_attenuation_then_normal(owner, "/tmp/base")

    assert isinstance(owner._attn2_worker, _FakeCaptureWorker)
    owner._attn2_worker.finished.emit(True, {"det_a": "/tmp/with.npy"})
    assert record_calls[0][0] == "without_sample"
    assert record_calls[-1] == ("with_sample", {"det_a": "/tmp/with.npy"})
    assert normal_calls == ["/tmp/base"]
    assert "Attenuation I capture complete" in capture_logs
