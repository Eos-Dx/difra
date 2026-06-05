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
    def __init__(self, figure):
        self.figure = figure
        self.drawn = False

    def draw_idle(self):
        self.drawn = True


class _FakeFigure:
    def __init__(self, figsize=None):
        self.figsize = figsize
        self.axes = []

    def add_subplot(self, *args):
        axes = _FakeAxes()
        axes.subplot_args = args
        self.axes.append(axes)
        return axes


class _FakeDialog:
    def __init__(self, parent):
        self.parent = parent
        self.title = None
        self.size = None
        self.shown = False
        self.closed = False

    def setWindowTitle(self, title):
        self.title = title

    def resize(self, width, height):
        self.size = (width, height)

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True


class _FakeLayout:
    def __init__(self, parent):
        self.parent = parent
        self.margins = None
        self.widgets = []

    def setContentsMargins(self, *margins):
        self.margins = margins

    def addWidget(self, widget):
        self.widgets.append(widget)


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
    runtime = SimpleNamespace(figures=[], canvases=[], dialogs=[], layouts=[])

    def fake_figure(*args, **kwargs):
        figure = _FakeFigure(*args, **kwargs)
        runtime.figures.append(figure)
        return figure

    def fake_canvas(figure):
        canvas = _FakeCanvas(figure)
        runtime.canvases.append(canvas)
        return canvas

    def fake_dialog(parent):
        dialog = _FakeDialog(parent)
        runtime.dialogs.append(dialog)
        return dialog

    def fake_layout(parent):
        layout = _FakeLayout(parent)
        runtime.layouts.append(layout)
        return layout

    monkeypatch.setattr(realtime_mixin, "Figure", fake_figure)
    monkeypatch.setattr(realtime_mixin, "FigureCanvas", fake_canvas)
    monkeypatch.setattr(realtime_mixin, "QDialog", fake_dialog)
    monkeypatch.setattr(realtime_mixin, "QVBoxLayout", fake_layout)
    monkeypatch.setattr(
        realtime_mixin,
        "_tm",
        lambda: SimpleNamespace(QTimer=_FakeTimer, QMessageBox=None),
    )
    return runtime


def test_realtime_initializes_detector_mirror_without_technical_container(monkeypatch):
    runtime = _patch_runtime(monkeypatch)
    controller = _FakeController()
    owner = _Harness()
    owner.hardware_client = _FakeHardwareClient({"PRIMARY": controller})

    owner._start_realtime()

    assert owner.hardware_client.initialize_calls == 1
    assert owner.detector_controller == {"PRIMARY": controller}
    assert controller.start_calls[0]["exposure"] == 0.2
    assert controller.start_calls[0]["frames"] == 3
    assert runtime.figures[0].figsize == (5, 5)
    assert runtime.figures[0].axes[0].subplot_args == (1, 1, 1)
    assert runtime.figures[0].axes[0].title == "PRIMARY"
    assert runtime.dialogs[0].shown is True
    assert runtime.layouts[0].widgets == [runtime.canvases[0]]

    owner._stop_realtime()

    assert controller.stop_calls == 1
    assert runtime.dialogs[0].closed is True


def test_realtime_opens_placeholder_when_no_detectors_are_available(monkeypatch):
    runtime = _patch_runtime(monkeypatch)
    owner = _Harness()

    owner._start_realtime()

    assert runtime.figures[0].figsize == (5, 5)
    assert runtime.figures[0].axes[0].subplot_args == (1, 1, 1)
    assert runtime.figures[0].axes[0].text_calls
    assert "No detector initialized" in runtime.figures[0].axes[0].text_calls[0][0]
    assert owner.logged == ["Real-time display opened without active detectors"]

    owner._stop_realtime()

    assert runtime.dialogs[0].closed is True
