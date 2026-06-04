from types import SimpleNamespace

from difra.gui.main_window_ext.technical import realtime_mixin
from difra.gui.main_window_ext.technical.realtime_mixin import TechnicalRealtimeMixin


class _FakeSpin:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _FakeTimeout:
    def connect(self, callback):
        self.callback = callback


class _FakeTimer:
    def __init__(self, _parent):
        self.timeout = _FakeTimeout()
        self.interval = None
        self.started = False
        self.stopped = False

    def setInterval(self, interval):
        self.interval = interval

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _FakeImage:
    def set_data(self, frame):
        self.frame = frame

    def set_clim(self, low, high):
        self.clim = (low, high)


class _FakeAxes:
    transAxes = object()

    def __init__(self):
        self.text_calls = []
        self.title = None
        self.axis_off = False

    def imshow(self, *_args, **_kwargs):
        return _FakeImage()

    def set_title(self, title):
        self.title = title

    def text(self, *args, **kwargs):
        self.text_calls.append((args, kwargs))

    def set_axis_off(self):
        self.axis_off = True


class _FakeCanvas:
    def draw_idle(self):
        self.drawn = True


class _FakeFigure:
    def __init__(self):
        self.canvas = _FakeCanvas()


class _FakePyplot:
    def __init__(self):
        self.show_calls = []
        self.closed = []
        self.subplots_calls = []
        self.last_axes = None

    def ion(self):
        return None

    def subplots(self, rows, cols, figsize=None):
        self.subplots_calls.append((rows, cols, figsize))
        fig = _FakeFigure()
        if cols == 1:
            axes = _FakeAxes()
        else:
            axes = [_FakeAxes() for _ in range(cols)]
        self.last_axes = axes
        return fig, axes

    def show(self, **kwargs):
        self.show_calls.append(kwargs)

    def close(self, fig):
        self.closed.append(fig)


class _FakeController:
    size = (4, 4)

    def __init__(self):
        self.start_calls = []
        self.stop_calls = 0

    def start_stream(self, **kwargs):
        self.start_calls.append(kwargs)

    def stop_stream(self):
        self.stop_calls += 1


class _FakeHardwareClient:
    def __init__(self, controllers):
        self.detector_controllers = {}
        self._controllers = controllers
        self.initialize_calls = 0

    def initialize_detector(self):
        self.initialize_calls += 1
        self.detector_controllers = dict(self._controllers)
        return True


class _Harness(TechnicalRealtimeMixin):
    def __init__(self):
        self.integrationTimeSpin = _FakeSpin(0.2)
        self.framesSpin = _FakeSpin(3)
        self.detector_controller = {}
        self.logged = []

    def _log_technical_event(self, message):
        self.logged.append(str(message))


def _patch_runtime(monkeypatch):
    fake_plt = _FakePyplot()
    monkeypatch.setattr(realtime_mixin, "plt", fake_plt)
    monkeypatch.setattr(
        realtime_mixin,
        "_tm",
        lambda: SimpleNamespace(QTimer=_FakeTimer, QMessageBox=None),
    )
    return fake_plt


def test_realtime_initializes_detector_mirror_without_technical_container(monkeypatch):
    fake_plt = _patch_runtime(monkeypatch)
    controller = _FakeController()
    owner = _Harness()
    owner.hardware_client = _FakeHardwareClient({"PRIMARY": controller})

    owner._start_realtime()

    assert owner.hardware_client.initialize_calls == 1
    assert owner.detector_controller == {"PRIMARY": controller}
    assert controller.start_calls[0]["exposure"] == 0.2
    assert controller.start_calls[0]["frames"] == 3
    assert fake_plt.subplots_calls == [(1, 1, (5, 5))]
    assert fake_plt.show_calls == [{"block": False}]

    owner._stop_realtime()

    assert controller.stop_calls == 1
    assert len(fake_plt.closed) == 1


def test_realtime_opens_placeholder_when_no_detectors_are_available(monkeypatch):
    fake_plt = _patch_runtime(monkeypatch)
    owner = _Harness()

    owner._start_realtime()

    assert fake_plt.subplots_calls == [(1, 1, (5, 5))]
    assert fake_plt.show_calls == [{"block": False}]
    assert fake_plt.last_axes.text_calls
    assert "No detector initialized" in fake_plt.last_axes.text_calls[0][0]
    assert owner.logged == ["Real-time display opened without active detectors"]

    owner._stop_realtime()

    assert len(fake_plt.closed) == 1
