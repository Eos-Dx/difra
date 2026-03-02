import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import h5py

SRC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

_MIXIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "gui"
    / "main_window_ext"
    / "zone_measurements"
    / "logic"
    / "process_start_mixin.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "process_start_mixin_for_tests",
    _MIXIN_PATH,
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
ZoneMeasurementsProcessStartMixin = _MODULE.ZoneMeasurementsProcessStartMixin


class _FakeLogger:
    def warning(self, *args, **kwargs):
        return None


class _FakeQMessageBox:
    info_calls = []
    warn_calls = []

    @classmethod
    def reset(cls):
        cls.info_calls = []
        cls.warn_calls = []

    @classmethod
    def information(cls, *args):
        cls.info_calls.append(args)
        return None

    @classmethod
    def warning(cls, *args):
        cls.warn_calls.append(args)
        return None


def _install_pm_stub():
    _FakeQMessageBox.reset()
    _MODULE._pm = lambda: SimpleNamespace(
        QMessageBox=_FakeQMessageBox,
        logger=_FakeLogger(),
    )


class _StubSessionManager:
    def __init__(self, active=True, locked=False):
        self.active = active
        self.locked = locked
        self.closed_count = 0

    def is_session_active(self):
        return self.active

    def is_locked(self):
        return self.locked if self.active else False

    def get_session_info(self):
        if not self.active:
            return {"active": False}
        return {
            "active": True,
            "sample_id": "SAMPLE-001",
            "session_id": "SESSION-001",
        }

    def close_session(self):
        self.closed_count += 1
        self.active = False
        self.locked = False


class _Harness(ZoneMeasurementsProcessStartMixin):
    def __init__(self, session_manager):
        self.session_manager = session_manager
        self.image_view = SimpleNamespace(current_image_path="/tmp/sample.png")
        self.opened_paths = []
        self.status_updates = 0

    def update_session_status(self):
        self.status_updates += 1

    def _handle_new_sample_image(self, image_path: str):
        self.opened_paths.append(image_path)
        self.session_manager.active = True
        self.session_manager.locked = False


class _HarnessNoSessionCreated(_Harness):
    def _handle_new_sample_image(self, image_path: str):
        self.opened_paths.append(image_path)
        self.session_manager.active = False
        self.session_manager.locked = False


def test_ensure_writable_session_allows_unlocked_session():
    _install_pm_stub()
    manager = _StubSessionManager(active=True, locked=False)
    harness = _Harness(manager)

    assert harness._ensure_writable_session_for_measurement() is True
    assert manager.closed_count == 0
    assert _FakeQMessageBox.info_calls == []


def test_ensure_writable_session_rolls_locked_session_and_notifies_operator():
    _install_pm_stub()
    manager = _StubSessionManager(active=True, locked=True)
    harness = _Harness(manager)

    assert harness._ensure_writable_session_for_measurement() is True
    assert manager.closed_count == 1
    assert harness.opened_paths == ["/tmp/sample.png"]
    assert harness.status_updates == 1
    assert len(_FakeQMessageBox.info_calls) == 1


def test_ensure_writable_session_fails_if_new_session_not_created():
    _install_pm_stub()
    manager = _StubSessionManager(active=True, locked=True)
    harness = _HarnessNoSessionCreated(manager)

    assert harness._ensure_writable_session_for_measurement() is False
    assert manager.closed_count == 1
    assert harness.opened_paths == ["/tmp/sample.png"]


def test_resolve_session_point_plan_treats_all_pending_session_as_fresh_run():
    _install_pm_stub()

    class _Schema:
        GROUP_POINTS = "/entry/points"
        ATTR_POINT_STATUS = "point_status"
        POINT_STATUS_MEASURED = "measured"
        ATTR_PHYSICAL_COORDINATES_MM = "physical_coordinates_mm"

    with tempfile.TemporaryDirectory() as tmp_dir:
        session_path = Path(tmp_dir) / "all_pending_with_uids.nxs.h5"
        point_specs = [
            (-10.0, 0.0, "1_aaaaaaaa"),
            (20.0, 0.0, "2_bbbbbbbb"),
            (-8.0, 0.0, "3_cccccccc"),
            (22.0, 0.0, "4_dddddddd"),
            (-6.0, 0.0, "5_eeeeeeee"),
            (-4.0, 0.0, "6_ffffffff"),
            (-2.0, 0.0, "7_gggggggg"),
            (0.0, 0.0, "8_hhhhhhhh"),
            (2.0, 0.0, "9_iiiiiiii"),
            (4.0, 0.0, "10_jjjjjjjj"),
        ]
        with h5py.File(session_path, "w") as h5f:
            points_group = h5f.create_group(_Schema.GROUP_POINTS)
            for idx, (x_mm, y_mm, uid) in enumerate(point_specs, start=1):
                point_group = points_group.create_group(f"pt_{idx:03d}")
                point_group.attrs[_Schema.ATTR_POINT_STATUS] = "pending"
                point_group.attrs[_Schema.ATTR_PHYSICAL_COORDINATES_MM] = [x_mm, y_mm]
                point_group.attrs["point_uid"] = uid

        manager = _StubSessionManager(active=True, locked=False)
        manager.session_path = session_path
        manager.schema = _Schema()

        harness = _Harness(manager)
        valid_measurement_points = [
            {"unique_id": uid, "point_index": idx, "x": x_mm, "y": y_mm}
            for idx, (x_mm, y_mm, uid) in enumerate(point_specs)
            if abs(x_mm) <= 14.0 and abs(y_mm) <= 14.0
        ]

        plan = harness._resolve_session_point_plan(valid_measurement_points)

    assert plan["mode"] == "new"
    assert plan["measurement_points"] == valid_measurement_points
    assert plan["session_point_indices"] == list(
        range(1, len(valid_measurement_points) + 1)
    )


def test_load_active_session_points_metadata_reads_restored_coordinates():
    _install_pm_stub()

    class _Schema:
        GROUP_POINTS = "/entry/points"
        ATTR_POINT_STATUS = "point_status"
        ATTR_PHYSICAL_COORDINATES_MM = "physical_coordinates_mm"

    with tempfile.TemporaryDirectory() as tmp_dir:
        session_path = Path(tmp_dir) / "restored_points.nxs.h5"
        with h5py.File(session_path, "w") as h5f:
            points_group = h5f.create_group(_Schema.GROUP_POINTS)
            pt1 = points_group.create_group("pt_001")
            pt1.attrs[_Schema.ATTR_POINT_STATUS] = "pending"
            pt1.attrs[_Schema.ATTR_PHYSICAL_COORDINATES_MM] = [1.25, -3.5]
            pt1.attrs["point_uid"] = "1_aaaaaaaa"

        manager = _StubSessionManager(active=True, locked=False)
        manager.session_path = session_path
        manager.schema = _Schema()

        harness = _Harness(manager)
        session_points = harness._load_active_session_points_metadata()

    assert session_points == [
        {
            "point_index": 1,
            "status": "pending",
            "point_uid": "1_aaaaaaaa",
            "physical_xy": (1.25, -3.5),
        }
    ]
