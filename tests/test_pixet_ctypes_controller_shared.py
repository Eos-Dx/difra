from pathlib import Path

from difra.hardware import detector_pixet_ctypes_controller as module
from difra.hardware import pixet_ctypes_api


class FakeDevice:
    index = 0
    name = "MiniPIX TEST"
    width = 256
    height = 256


class FakeAPI:
    init_calls = 0
    shutdown_calls = 0

    def __init__(self, sdk_path):
        self.sdk_path = Path(sdk_path)
        self.initialized = False

    def initialize(self):
        type(self).init_calls += 1
        self.initialized = True

    def get_version(self):
        return "fake"

    def list_devices(self):
        return [FakeDevice()]

    def shutdown(self):
        type(self).shutdown_calls += 1
        self.initialized = False


def test_pixet_ctypes_api_is_shared_across_detector_aliases(tmp_path, monkeypatch):
    monkeypatch.setattr(pixet_ctypes_api, "PixetCtypesAPI", FakeAPI)
    FakeAPI.init_calls = 0
    FakeAPI.shutdown_calls = 0
    pixet_ctypes_api._process_api = None

    sdk_path = tmp_path / "Pixet64"
    sdk_path.mkdir()
    primary = module.PixetDetectorController(
        "PRIMARY",
        config={"id": "MiniPIX", "pixet_sdk_path": str(sdk_path)},
    )
    secondary = module.PixetDetectorController(
        "SECONDARY",
        config={"id": "MiniPIX", "pixet_sdk_path": str(sdk_path)},
    )

    assert primary.init_detector()
    assert secondary.init_detector()
    assert primary.init_detector()
    assert FakeAPI.init_calls == 1

    primary.deinit_detector()
    assert FakeAPI.shutdown_calls == 0
    secondary.deinit_detector()
    assert FakeAPI.shutdown_calls == 0
    pixet_ctypes_api._process_api.shutdown()
    assert FakeAPI.shutdown_calls == 1
    pixet_ctypes_api._process_api = None
