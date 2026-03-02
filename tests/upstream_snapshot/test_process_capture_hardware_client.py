import os
import sys
import importlib.util
from pathlib import Path
import types

SRC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

_MIXIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "gui"
    / "main_window_ext"
    / "zone_measurements"
    / "logic"
    / "process_capture_mixin.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "process_capture_mixin_for_tests",
    _MIXIN_PATH,
)
_MODULE = importlib.util.module_from_spec(_SPEC)

_container_api_stub = types.ModuleType("difra.gui.container_api")
_container_api_stub.get_container_version = lambda *_args, **_kwargs: "0.2"
_original_container_api_module = sys.modules.get("difra.gui.container_api")
sys.modules["difra.gui.container_api"] = _container_api_stub

assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
if _original_container_api_module is not None:
    sys.modules["difra.gui.container_api"] = _original_container_api_module
else:
    del sys.modules["difra.gui.container_api"]
ZoneMeasurementsProcessCaptureMixin = _MODULE.ZoneMeasurementsProcessCaptureMixin


class _StubStageController:
    def __init__(self):
        self.calls = []

    def move_stage(self, x, y, move_timeout=20):
        self.calls.append((x, y, move_timeout))
        return x, y


class _StubHardwareClient:
    def __init__(self):
        self.calls = []
        self._x = 0.0
        self._y = 0.0

    def move_to(self, position_mm, axis, timeout_s=25.0):
        self.calls.append((position_mm, axis, timeout_s))
        if axis == "x":
            self._x = float(position_mm)
        elif axis == "y":
            self._y = float(position_mm)
        else:
            raise ValueError(f"Unexpected axis: {axis}")
        return self._x, self._y


class _Harness(ZoneMeasurementsProcessCaptureMixin):
    pass


class _StubButton:
    def setEnabled(self, _value):
        return None


class _StubProgressBar:
    def setMaximum(self, _value):
        return None

    def setValue(self, _value):
        return None


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


def test_move_stage_prefers_hardware_client():
    h = _Harness()
    h.hardware_client = _StubHardwareClient()
    h.stage_controller = _StubStageController()

    out = h._move_stage(1.5, -2.0, timeout_s=7.0)
    assert out == (1.5, -2.0)
    assert h.hardware_client.calls == [
        (1.5, "x", 7.0),
        (-2.0, "y", 7.0),
    ]
    assert h.stage_controller.calls == []


def test_move_stage_falls_back_to_stage_controller():
    h = _Harness()
    h.hardware_client = None
    h.stage_controller = _StubStageController()

    out = h._move_stage(3.0, 4.0, timeout_s=9.0)
    assert out == (3.0, 4.0)
    assert h.stage_controller.calls == [(3.0, 4.0, 9.0)]


def test_measure_next_point_prefers_planned_measurement_coordinates():
    h = _Harness()
    h.stopped = False
    h.paused = False
    h.current_measurement_sorted_index = 0
    h.sorted_indices = [0]
    h.total_points = 1
    h.progressBar = _StubProgressBar()
    h.start_btn = _StubButton()
    h.pause_btn = _StubButton()
    h.stop_btn = _StubButton()
    h.fileNameLineEdit = _StubLineEdit("sample")
    h.measurement_folder = "/tmp"
    h.integration_time = 1.0
    h.real_x_pos_mm = _StubSpin(0.0)
    h.real_y_pos_mm = _StubSpin(0.0)
    h.include_center = (0.0, 0.0)
    h.pixel_to_mm_ratio = 1.0
    h.update_xy_pos = lambda: None
    h._append_capture_log = lambda _msg: None
    h._append_hw_log = lambda _msg: None
    h.attenuationCheckBox = None
    h.state_measurements = {
        "measurement_points": [
            {"x": 1.25, "y": -3.5, "unique_id": "1_aaaaaaaa"}
        ]
    }
    h.state = {}
    h.image_view = types.SimpleNamespace(
        points_dict={
            "generated": {
                "points": [_StubPointItem(50.0, 75.0)],
                "zones": [object()],
            },
            "user": {"points": [], "zones": []},
        }
    )

    move_calls = []
    capture_calls = []

    h._move_stage = lambda x, y, timeout_s=15: move_calls.append((x, y, timeout_s)) or (x, y)
    h._start_normal_capture = lambda txt_base: capture_calls.append(txt_base)

    h.measure_next_point()

    assert h._x_mm == 1.25
    assert h._y_mm == -3.5
    assert move_calls == [(1.25, -3.5, 15)]
    assert len(capture_calls) == 1
