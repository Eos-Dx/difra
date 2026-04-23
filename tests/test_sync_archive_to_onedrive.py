from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(name, None)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(name, None)


def test_resolve_sync_roots_uses_windows_archive_and_mirror_from_config(tmp_path):
    module = _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "sync_archive_to_onedrive.py",
        "test_sync_archive_to_onedrive_resolve",
    )

    config_path = tmp_path / "main_win.json"
    config_path.write_text(
        (
            '{'
            '"measurements_archive_folder": "D:\\\\Data\\\\Archive\\\\measurements", '
            '"technical_archive_folder": "D:\\\\Data\\\\Archive\\\\technical", '
            '"measurements_archive_mirror_folder": '
            '"C:\\\\Users\\\\Ulster_matur\\\\OneDrive - Matur\\\\General - Ulster\\\\Measurements_Grant_1_2_and_4"'
            '}'
        ),
        encoding="utf-8",
    )

    source_root, mirror_root = module.resolve_sync_roots(config_path=config_path)

    assert source_root == Path("D:/Data/Archive")
    assert mirror_root == Path(
        "C:/Users/Ulster_matur/OneDrive - Matur/General - Ulster/Measurements_Grant_1_2_and_4"
    )


def test_resolve_sync_roots_from_config_matches_file_based_resolution():
    module = _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "sync_archive_to_onedrive.py",
        "test_sync_archive_to_onedrive_resolve_from_config",
    )

    source_root, mirror_root = module.resolve_sync_roots_from_config(
        {
            "measurements_archive_folder": "D:\\Data\\Archive\\measurements",
            "measurements_archive_mirror_folder": (
                "C:\\Users\\Ulster_matur\\OneDrive - Matur\\General - Ulster\\Measurements_Grant_1_2_and_4"
            ),
        }
    )

    assert source_root == Path("D:/Data/Archive")
    assert mirror_root == Path(
        "C:/Users/Ulster_matur/OneDrive - Matur/General - Ulster/Measurements_Grant_1_2_and_4"
    )


def test_sync_archive_tree_copies_and_updates_files(tmp_path):
    module = _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "sync_archive_to_onedrive.py",
        "test_sync_archive_to_onedrive_sync",
    )

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    source_file = source_root / "measurements" / "folder_a" / "capture.txt"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("v1", encoding="utf-8")

    summary = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    mirrored_file = mirror_root / "Archive" / "measurements" / "folder_a" / "capture.txt"
    assert mirrored_file.exists() is True
    assert mirrored_file.read_text(encoding="utf-8") == "v1"
    assert summary.copied_files == 1
    assert summary.updated_files == 0
    assert summary.skipped_files == 0

    source_file.write_text("v2", encoding="utf-8")
    summary = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    assert mirrored_file.read_text(encoding="utf-8") == "v2"
    assert summary.copied_files == 0
    assert summary.updated_files == 1


def test_main_reports_dry_run_without_copying(monkeypatch, capsys, tmp_path):
    module = _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "sync_archive_to_onedrive.py",
        "test_sync_archive_to_onedrive_main",
    )

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    (source_root / "technical" / "demo.h5").parent.mkdir(parents=True, exist_ok=True)
    (source_root / "technical" / "demo.h5").write_text("demo", encoding="utf-8")

    monkeypatch.setattr(
        module,
        "_build_parser",
        lambda: SimpleNamespace(
            parse_args=lambda: SimpleNamespace(
                config=str(tmp_path / "missing.json"),
                source_root=str(source_root),
                mirror_root=str(mirror_root),
                dry_run=True,
            )
        ),
    )

    assert module.main() == 0
    out = capsys.readouterr().out
    assert "Source archive root:" in out
    assert "Dry run only" in out
    assert (mirror_root / "Archive" / "technical" / "demo.h5").exists() is False
