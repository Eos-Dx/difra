from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_main_window_basic_module():
    path = REPO_ROOT / "src" / "difra" / "gui" / "views" / "main_window_basic.py"
    spec = importlib.util.spec_from_file_location("test_main_window_basic_archive_sync", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    previous_camera = sys.modules.get("difra.hardware.camera_capture_dialog")
    stub_camera_module = ModuleType("difra.hardware.camera_capture_dialog")
    stub_camera_module.CameraCaptureDialog = object
    sys.modules["difra.hardware.camera_capture_dialog"] = stub_camera_module
    sys.modules.pop("test_main_window_basic_archive_sync", None)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("test_main_window_basic_archive_sync", None)
        if previous_camera is None:
            sys.modules.pop("difra.hardware.camera_capture_dialog", None)
        else:
            sys.modules["difra.hardware.camera_capture_dialog"] = previous_camera


def test_archive_mirror_sync_starts_background_zip_process(monkeypatch, tmp_path):
    module = _load_main_window_basic_module()
    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    source_root.mkdir(parents=True, exist_ok=True)
    mirror_root.mkdir(parents=True, exist_ok=True)

    calls = []
    session_logs = []

    monkeypatch.setattr(
        module,
        "resolve_sync_roots_from_config",
        lambda config: (source_root, mirror_root),
    )

    def _start_archive_zip_sync_process(*, source_root, mirror_root, dry_run):
        calls.append((source_root, mirror_root, dry_run))
        return SimpleNamespace(
            pid=1234,
            poll=lambda: 0,
        )

    monkeypatch.setattr(
        module,
        "start_archive_zip_sync_process",
        _start_archive_zip_sync_process,
    )

    owner = SimpleNamespace(
        config={
            "measurements_archive_folder": str(source_root / "measurements"),
            "measurements_archive_mirror_folder": str(mirror_root),
        },
        _archive_mirror_sync_running=False,
        _archive_mirror_sync_process=None,
        _append_session_log=session_logs.append,
        _format_archive_sync_bytes=module.MainWindowBasic._format_archive_sync_bytes,
    )

    module.MainWindowBasic._run_archive_mirror_sync(owner)

    assert calls == [(source_root, mirror_root, False)]
    assert owner._archive_mirror_sync_running is False
    assert session_logs[0].startswith("Archive zip sync started: from ")
    assert str(source_root) in session_logs[0]
    assert str(mirror_root / "Archive") in session_logs[0]
    assert session_logs[1] == "Archive zip sync process started: pid=1234"


def test_archive_mirror_sync_skips_when_background_process_running(monkeypatch, tmp_path):
    module = _load_main_window_basic_module()
    calls = []

    owner = SimpleNamespace(
        config={},
        _archive_mirror_sync_running=False,
        _archive_mirror_sync_process=SimpleNamespace(poll=lambda: None),
        _append_session_log=lambda _msg: None,
    )
    monkeypatch.setattr(
        module,
        "start_archive_zip_sync_process",
        lambda **_kwargs: calls.append("started"),
    )

    module.MainWindowBasic._run_archive_mirror_sync(owner)

    assert calls == []


def test_archive_mirror_sync_reports_failed_process_before_retry(monkeypatch, tmp_path):
    module = _load_main_window_basic_module()
    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    logs = []
    replacement = SimpleNamespace(pid=4321, poll=lambda: None)

    owner = SimpleNamespace(
        config={},
        _archive_mirror_sync_running=False,
        _archive_mirror_sync_process=SimpleNamespace(poll=lambda: 2),
        _append_session_log=logs.append,
    )
    monkeypatch.setattr(
        module,
        "resolve_sync_roots_from_config",
        lambda _config: (source_root, mirror_root),
    )
    monkeypatch.setattr(
        module,
        "start_archive_zip_sync_process",
        lambda **_kwargs: replacement,
    )

    module.MainWindowBasic._run_archive_mirror_sync(owner)

    assert logs[0] == "Archive zip sync failed: process exit code 2"
    assert owner._archive_mirror_sync_process is replacement


def test_archive_mirror_sync_logs_when_not_configured():
    module = _load_main_window_basic_module()
    session_logs = []

    owner = SimpleNamespace(
        config={},
        _append_session_log=session_logs.append,
    )

    module.MainWindowBasic.setup_archive_mirror_sync(owner)

    assert session_logs == [
        "Archive sync disabled: Could not resolve archive source root from config."
    ]


def test_main_window_basic_init_does_not_start_archive_sync_before_ui(monkeypatch):
    module = _load_main_window_basic_module()
    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    calls = []

    monkeypatch.setattr(module.MainWindowBasic, "load_config", lambda self: {})
    monkeypatch.setattr(module.MainWindowBasic, "create_actions", lambda self: None)
    monkeypatch.setattr(module.MainWindowBasic, "create_menus", lambda self: None)
    monkeypatch.setattr(module.MainWindowBasic, "create_tool_bar", lambda self: None)
    monkeypatch.setattr(module.MainWindowBasic, "update_dev_visuals", lambda self: None)
    monkeypatch.setattr(
        module.MainWindowBasic,
        "setup_archive_mirror_sync",
        lambda self: calls.append("started"),
    )

    window = module.MainWindowBasic()
    try:
        assert calls == []
    finally:
        window.close()


def test_augment_setup_config_for_editor_includes_archive_sync_keys():
    module = _load_main_window_basic_module()

    payload = module.MainWindowBasic._augment_setup_config_for_editor(
        {"name": "Ulster (Xena)"},
        {
            "measurements_archive_mirror_folder": "C:\\OneDrive\\Ulster",
            "archive_mirror_sync_interval_ms": 300000,
        },
    )

    assert payload["measurements_archive_mirror_folder"] == "C:\\OneDrive\\Ulster"
    assert payload["archive_mirror_sync_interval_ms"] == 300000


def test_load_config_uses_windows_mirror_path_from_main_win(monkeypatch, tmp_path):
    module = _load_main_window_basic_module()

    class _FakeSettings:
        def __init__(self):
            self.values = {}

        def value(self, key, default=None, type=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

    settings = _FakeSettings()
    monkeypatch.setattr(QtCore, "QSettings", lambda *args, **kwargs: settings)
    monkeypatch.setattr(module.os, "name", "nt", raising=False)
    monkeypatch.setattr(sys, "argv", ["pytest", "--setup", "Ulster (Xena)"])

    config_dir = tmp_path / "config"
    setups_dir = config_dir / "setups"
    setups_dir.mkdir(parents=True, exist_ok=True)
    global_path = config_dir / "global.json"
    legacy_path = config_dir / "main_win.json"
    setup_path = setups_dir / "Ulster (Xena).json"

    global_path.write_text(
        '{"default_setup": "Ulster (Xena)", "measurements_archive_mirror_folder": null}',
        encoding="utf-8",
    )
    setup_path.write_text('{"name": "Ulster (Xena)"}', encoding="utf-8")
    legacy_path.write_text(
        (
            '{'
            '"measurements_archive_folder": "D:\\\\Data\\\\Archive\\\\measurements", '
            '"measurements_archive_mirror_folder": '
            '"C:\\\\Users\\\\Ulster_matur\\\\OneDrive - Matur\\\\General - Ulster\\\\Measurements_Grant_1_2_and_4"'
            '}'
        ),
        encoding="utf-8",
    )

    owner = module.MainWindowBasic.__new__(module.MainWindowBasic)
    owner._config_dir = config_dir
    owner._global_path = global_path
    owner._setups_dir = setups_dir
    owner._legacy_main_path = legacy_path

    config = module.MainWindowBasic.load_config(owner)

    assert config["measurements_archive_mirror_folder"] == (
        "C:\\Users\\Ulster_matur\\OneDrive - Matur\\General - Ulster\\Measurements_Grant_1_2_and_4"
    )
