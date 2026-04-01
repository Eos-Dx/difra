from pathlib import Path
from types import SimpleNamespace

from difra.gui.main_window_ext.technical.capture_mixin import TechnicalCaptureMixin


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _FakeThread:
    def __init__(self):
        self.started = _FakeSignal()

    def start(self):
        return None

    def quit(self):
        return None

    def deleteLater(self):
        return None


class _FakeWorker:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.finished = _FakeSignal()
        _FakeWorker.instances.append(self)

    def moveToThread(self, _thread):
        return None

    def run(self):
        return None

    def deleteLater(self):
        return None


class _FakeCheckBox:
    def __init__(self, checked):
        self._checked = bool(checked)

    def isChecked(self):
        return self._checked


class _FakeSpin:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _FakeStageController:
    def __init__(self):
        self.moves = []

    def move_stage(self, x, y, move_timeout=20):
        self.moves.append((x, y, move_timeout))
        return x, y

    def get_xy_position(self):
        return 1.0, 2.0


class _FakeHardwareClient:
    def __init__(self, stage_controller):
        self.stage_controller = stage_controller


class _Harness(TechnicalCaptureMixin):
    AUX_COL_FILE = 0

    def __init__(self, *, checked=True):
        self.config = {}
        self.hardware_controller = None
        self.stage_controller = None
        self.hardware_client = _FakeHardwareClient(_FakeStageController())
        self.moveContinuousCheck = _FakeCheckBox(checked)
        self.movementRadiusSpin = _FakeSpin(2.5)
        self.integrationTimeSpin = _FakeSpin(3.0)
        self.captureFramesSpin = _FakeSpin(4)
        self.detector_controller = {"SAXS": object()}
        self.continuous_movement_controller = None
        self._capture_workers = []
        self.logged_events = []

    def _technical_imports_available(self):
        return True

    def _log_technical_event(self, message):
        self.logged_events.append(str(message))

    def _current_technical_output_folder(self):
        return "/tmp"

    def _file_base(self, typ):
        return f"{typ.lower()}_base"

    def _get_technical_module(self, name):
        if name == "validate_folder":
            return lambda folder: Path(folder)
        if name == "CaptureWorker":
            return _FakeWorker
        raise AssertionError(f"Unexpected technical module request: {name}")

    def _on_capture_done(self, *args, **kwargs):
        return None


def _patch_tm(monkeypatch):
    fake_tm = SimpleNamespace(QThread=_FakeThread, QMessageBox=SimpleNamespace(warning=lambda *a, **k: None))
    monkeypatch.setattr(
        "difra.gui.main_window_ext.technical.capture_mixin._tm",
        lambda: fake_tm,
    )


def test_start_capture_enables_continuous_movement_for_agbh(monkeypatch):
    _FakeWorker.instances.clear()
    _patch_tm(monkeypatch)
    harness = _Harness(checked=True)

    harness._start_capture("AGBH")

    assert len(_FakeWorker.instances) == 1
    worker = _FakeWorker.instances[0]
    assert worker.kwargs["enable_continuous_movement"] is True
    assert worker.kwargs["stage_controller"] is harness.hardware_client.stage_controller
    assert worker.kwargs["continuous_movement_controller"] is not None
    assert (
        worker.kwargs["continuous_movement_controller"].stage_controller
        is harness.hardware_client.stage_controller
    )


def test_start_capture_does_not_enable_continuous_movement_for_non_agbh(monkeypatch):
    _FakeWorker.instances.clear()
    _patch_tm(monkeypatch)
    harness = _Harness(checked=True)

    harness._start_capture("DARK")

    assert len(_FakeWorker.instances) == 1
    worker = _FakeWorker.instances[0]
    assert worker.kwargs["enable_continuous_movement"] is False
