from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from difra.utils import technical_temp_manager as temp_manager_module


def test_create_session_dir_uses_base_temp_dir_and_tracks_active_session(tmp_path: Path):
    manager = temp_manager_module.TechnicalTempManager(base_temp_dir=str(tmp_path))

    session_dir = manager.create_session_dir("session-1")

    assert session_dir == tmp_path / "difra_technical_session-1"
    assert session_dir.is_dir()
    assert manager.get_session_dir() == session_dir


def test_stage_file_requires_active_session(tmp_path: Path):
    manager = temp_manager_module.TechnicalTempManager(base_temp_dir=str(tmp_path))

    with pytest.raises(ValueError):
        manager.stage_file(str(tmp_path / "source.npy"), "DARK_PRIMARY")


def test_stage_file_copies_source_and_get_staged_files_returns_copy(tmp_path: Path):
    source = tmp_path / "source.npy"
    source.write_bytes(b"demo")
    manager = temp_manager_module.TechnicalTempManager(base_temp_dir=str(tmp_path))
    session_dir = manager.create_session_dir("session-2")

    staged_path = Path(manager.stage_file(str(source), "DARK_PRIMARY"))

    assert staged_path == session_dir / "DARK_PRIMARY.npy"
    assert staged_path.read_bytes() == b"demo"
    staged_files = manager.get_staged_files()
    staged_files["new"] = "changed"
    assert "new" not in manager.get_staged_files()


def test_cleanup_session_removes_unpreserved_files_and_resets_state(tmp_path: Path):
    keep = tmp_path / "keep.npy"
    drop = tmp_path / "drop.npy"
    keep.write_bytes(b"keep")
    drop.write_bytes(b"drop")
    manager = temp_manager_module.TechnicalTempManager(base_temp_dir=str(tmp_path))
    manager.create_session_dir("session-3")
    staged_keep = Path(manager.stage_file(str(keep), "KEEP"))
    staged_drop = Path(manager.stage_file(str(drop), "DROP"))

    manager.cleanup_session(preserve_files=[str(staged_keep)])

    assert staged_keep.exists()
    assert not staged_drop.exists()
    assert manager.get_session_dir() is None
    assert manager.get_staged_files() == {}


def test_cleanup_session_noops_without_active_session(tmp_path: Path):
    manager = temp_manager_module.TechnicalTempManager(base_temp_dir=str(tmp_path))

    manager.cleanup_session()

    assert manager.get_session_dir() is None


def test_cleanup_old_sessions_removes_only_old_matching_directories(tmp_path: Path):
    base = tmp_path / "temp"
    base.mkdir()
    old_dir = base / "difra_technical_old"
    new_dir = base / "difra_technical_new"
    other_dir = base / "other_dir"
    old_dir.mkdir()
    new_dir.mkdir()
    other_dir.mkdir()
    now = time.time()
    old_age = now - (48 * 3600)
    fresh_age = now - 100
    os.utime(old_dir, (old_age, old_age))
    os.utime(new_dir, (fresh_age, fresh_age))
    os.utime(other_dir, (old_age, old_age))

    manager = temp_manager_module.TechnicalTempManager(base_temp_dir=str(base))

    manager.cleanup_old_sessions(max_age_hours=24)

    assert not old_dir.exists()
    assert new_dir.exists()
    assert other_dir.exists()


def test_technical_temp_manager_context_manager_cleans_up_session(tmp_path: Path):
    source = tmp_path / "source.npy"
    source.write_bytes(b"demo")

    with temp_manager_module.TechnicalTempManager(base_temp_dir=str(tmp_path)) as manager:
        session_dir = manager.create_session_dir("session-4")
        staged_path = Path(manager.stage_file(str(source), "MEASURED"))
        assert session_dir.exists()
        assert staged_path.exists()

    assert not staged_path.exists()
    assert not session_dir.exists()


def test_get_technical_temp_dir_creates_named_subdirectory(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(temp_manager_module.tempfile, "gettempdir", lambda: str(tmp_path))

    path = temp_manager_module.get_technical_temp_dir()

    assert path == tmp_path / "difra_technical"
    assert path.is_dir()
