from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(name, None)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(name, None)


def _sync_module(name: str):
    return _load_module(
        REPO_ROOT / "src" / "difra" / "scripts" / "sync_archive_to_onedrive.py",
        name,
    )


def test_resolve_sync_roots_uses_windows_archive_and_mirror_from_config(tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_resolve")

    config_path = tmp_path / "main_win.json"
    config_path.write_text(
        (
            "{"
            '"measurements_archive_folder": "D:\\\\Data\\\\Archive\\\\measurements", '
            '"technical_archive_folder": "D:\\\\Data\\\\Archive\\\\technical", '
            '"measurements_archive_mirror_folder": '
            '"C:\\\\Users\\\\Ulster_matur\\\\OneDrive - Matur\\\\General - Ulster\\\\Measurements_Grant_1_2_and_4"'
            "}"
        ),
        encoding="utf-8",
    )

    source_root, mirror_root = module.resolve_sync_roots(config_path=config_path)

    assert source_root == Path("D:/Data/Archive")
    assert mirror_root == Path(
        "C:/Users/Ulster_matur/OneDrive - Matur/General - Ulster/Measurements_Grant_1_2_and_4"
    )


def test_sync_archive_tree_exports_daily_zips_and_text_manifests(tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_zip")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    measurement_day = source_root / "measurements" / "SESSION_A_20260622_115300"
    technical_day = source_root / "technical" / "TECH_20260622_120000"
    measurement_day.mkdir(parents=True)
    technical_day.mkdir(parents=True)
    (measurement_day / "sample_20260622.nxs.h5").write_text("measurement", encoding="utf-8")
    (measurement_day / "capture.txt").write_text("capture", encoding="utf-8")
    (technical_day / "agbh_20260622.npy").write_text("technical", encoding="utf-8")
    old_folder = mirror_root / "Archive" / "measurements" / "old_raw_folder"
    old_folder.mkdir(parents=True)
    (old_folder / "raw.h5").write_text("old", encoding="utf-8")
    (mirror_root / "Archive" / "measurements" / "old.txt").write_text("old", encoding="utf-8")

    summary = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    measurements_root = mirror_root / "Archive" / "measurements"
    technical_root = mirror_root / "Archive" / "technical"
    measurement_zip = measurements_root / "measurements_20260622.zip"
    technical_zip = technical_root / "technical_20260622.zip"

    assert measurement_zip.exists() is True
    assert technical_zip.exists() is True
    assert old_folder.exists() is False
    assert (measurements_root / "old.txt").exists() is False
    assert not [path for path in measurements_root.iterdir() if path.is_dir()]
    assert not [path for path in technical_root.iterdir() if path.is_dir()]
    assert {path.suffix for path in measurements_root.iterdir()} <= {".zip", ".txt"}
    assert {path.suffix for path in technical_root.iterdir()} <= {".zip", ".txt"}
    assert (measurements_root / "measurements_20260622.txt").exists() is True
    assert (measurements_root / "measurements_manifest.txt").exists() is True
    assert (technical_root / "technical_20260622.txt").exists() is True
    assert (technical_root / "technical_manifest.txt").exists() is True
    assert summary.created_zip_files == 2
    assert summary.removed_destination_items == 2

    with zipfile.ZipFile(measurement_zip, "r") as archive:
        names = set(archive.namelist())
    assert "20260622/SESSION_A_20260622_115300/sample_20260622.nxs.h5" in names
    assert "20260622/SESSION_A_20260622_115300/capture.txt" in names

    manifest = (measurements_root / "measurements_20260622.txt").read_text(
        encoding="utf-8"
    )
    assert "Kind: measurements" in manifest
    assert "SESSION_A_20260622_115300" in manifest


def test_sync_archive_tree_skips_existing_matching_artifacts(tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_incremental")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    day = source_root / "measurements" / "SESSION_A_20260622_115300"
    day.mkdir(parents=True)
    (day / "capture.txt").write_text("capture", encoding="utf-8")

    first = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)
    second = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    assert first.copied_files > 0
    assert second.copied_files == 0
    assert second.updated_files == 0
    assert second.skipped_files >= 1


def test_sync_archive_tree_does_not_delete_extra_items_after_manifest_exists(tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_no_repeat_delete")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    day = source_root / "measurements" / "SESSION_A_20260622_115300"
    day.mkdir(parents=True)
    (day / "capture.txt").write_text("capture", encoding="utf-8")

    first = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)
    measurements_root = mirror_root / "Archive" / "measurements"
    extra_folder = measurements_root / "manual_extra_folder"
    extra_folder.mkdir()
    (extra_folder / "keep.txt").write_text("keep", encoding="utf-8")

    second = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    assert first.removed_destination_items == 0
    assert second.removed_destination_items == 0
    assert extra_folder.exists() is True
    assert (extra_folder / "keep.txt").exists() is True


def test_main_reports_dry_run_without_copying(monkeypatch, capsys, tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_main")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    (source_root / "technical" / "TECH_20260622").mkdir(parents=True)
    (source_root / "technical" / "TECH_20260622" / "demo.h5").write_text(
        "demo",
        encoding="utf-8",
    )

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
    assert "Created day ZIPs:" in out
    assert (mirror_root / "Archive" / "technical").exists() is False


def test_lifecycle_mirror_request_starts_zip_sync_without_raw_copy(monkeypatch, tmp_path):
    from difra.gui.session_lifecycle_service import SessionLifecycleService
    import difra.scripts.sync_archive_to_onedrive as sync_module

    archive_root = tmp_path / "Archive"
    source_folder = archive_root / "measurements" / "SESSION_A_20260622"
    mirror_root = tmp_path / "OneDriveRoot"
    source_folder.mkdir(parents=True)
    (source_folder / "capture.txt").write_text("capture", encoding="utf-8")

    calls = []
    monkeypatch.setattr(
        sync_module,
        "start_archive_zip_sync_process",
        lambda **kwargs: calls.append(kwargs),
    )

    destination = SessionLifecycleService.copy_archive_item_to_mirror(
        source_folder,
        config={
            "measurements_archive_folder": str(archive_root / "measurements"),
            "measurements_archive_mirror_folder": str(mirror_root),
        },
        archive_kind="measurements",
    )

    assert destination == mirror_root / "Archive" / "measurements"
    assert calls == [
        {
            "source_root": archive_root,
            "mirror_root": mirror_root,
            "dry_run": False,
        }
    ]
    assert (mirror_root / "Archive" / "measurements" / source_folder.name).exists() is False
