from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


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


def test_archive_mirror_sync_uses_only_new_or_updated_files(monkeypatch, tmp_path):
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

    def _sync_archive_tree(*, source_root, mirror_root, dry_run):
        calls.append((source_root, mirror_root, dry_run))
        return SimpleNamespace(
            source_root=source_root,
            destination_root=mirror_root / source_root.name,
            scanned_files=3,
            copied_files=1,
            updated_files=0,
            skipped_files=2,
            transferred_bytes=2048,
        )

    monkeypatch.setattr(module, "sync_archive_tree", _sync_archive_tree)

    owner = SimpleNamespace(
        config={
            "measurements_archive_folder": str(source_root / "measurements"),
            "measurements_archive_mirror_folder": str(mirror_root),
        },
        _archive_mirror_sync_running=False,
        _append_session_log=session_logs.append,
        _format_archive_sync_bytes=module.MainWindowBasic._format_archive_sync_bytes,
    )

    module.MainWindowBasic._run_archive_mirror_sync(owner)

    assert calls == [(source_root, mirror_root, False)]
    assert owner._archive_mirror_sync_running is False
    assert session_logs[0].startswith("Archive sync started: from ")
    assert str(source_root) in session_logs[0]
    assert str(mirror_root / source_root.name) in session_logs[0]
    assert "Archive sync completed:" in session_logs[1]
    assert f"from {source_root} to {mirror_root / source_root.name}" in session_logs[1]
    assert "scanned 3 file(s)" in session_logs[1]
    assert "1 file(s) transferred" in session_logs[1]
    assert "2 skipped" in session_logs[1]
    assert "2.0 KB" in session_logs[1]
