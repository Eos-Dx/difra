from __future__ import annotations

import importlib.util
import stat
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


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
    assert first.created_zip_files == 1
    assert second.created_zip_files == 0
    assert second.copied_files == 0
    assert second.updated_files == 0
    assert second.skipped_files >= 1


def test_sync_archive_tree_does_not_reopen_zip_for_unchanged_day(monkeypatch, tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_no_zip_rebuild")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    day = source_root / "measurements" / "SESSION_A_20260622_115300"
    day.mkdir(parents=True)
    (day / "capture.txt").write_text("capture", encoding="utf-8")

    module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    def _raise_zip_open(*_args, **_kwargs):
        raise AssertionError("zip should not be rebuilt for unchanged day")

    monkeypatch.setattr(module.zipfile, "ZipFile", _raise_zip_open)

    summary = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    assert summary.created_zip_files == 0


def test_sync_archive_tree_dry_run_never_builds_a_staging_zip(monkeypatch, tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_dry_run_no_zip")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    day = source_root / "measurements" / "SESSION_A_20260622_115300"
    day.mkdir(parents=True)
    (day / "capture.txt").write_text("capture", encoding="utf-8")

    monkeypatch.setattr(
        module,
        "_build_day_zip",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry run must not create a ZIP")
        ),
    )
    monkeypatch.setattr(
        module,
        "_sha256",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry run must not hash archive payloads")
        ),
    )

    summary = module.sync_archive_tree(
        source_root=source_root,
        mirror_root=mirror_root,
        dry_run=True,
    )

    assert summary.created_zip_files == 1
    assert mirror_root.exists() is False


def test_sync_archive_tree_rebuilds_changed_day_from_fingerprint(tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_changed_day")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    day = source_root / "measurements" / "SESSION_A_20260622_115300"
    day.mkdir(parents=True)
    capture = day / "capture.txt"
    capture.write_text("capture-v1", encoding="utf-8")

    first = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)
    capture.write_text("capture-v2", encoding="utf-8")
    second = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    assert first.created_zip_files == 1
    assert second.created_zip_files == 1
    assert second.updated_files >= 1
    with zipfile.ZipFile(
        mirror_root / "Archive" / "measurements" / "measurements_20260622.zip",
        "r",
    ) as archive:
        assert (
            archive.read("20260622/SESSION_A_20260622_115300/capture.txt")
            == b"capture-v2"
        )


def test_sync_archive_tree_rebuilds_corrupt_destination_zip(tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_corrupt_zip")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    day = source_root / "measurements" / "SESSION_A_20260622_115300"
    day.mkdir(parents=True)
    (day / "capture.txt").write_text("capture", encoding="utf-8")

    module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)
    zip_path = (
        mirror_root / "Archive" / "measurements" / "measurements_20260622.zip"
    )
    corrupt_payload = bytearray(zip_path.read_bytes())
    corrupt_payload[len(corrupt_payload) // 2] ^= 0xFF
    zip_path.write_bytes(corrupt_payload)

    summary = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    assert summary.created_zip_files == 1
    with zipfile.ZipFile(zip_path, "r") as archive:
        assert archive.read("20260622/SESSION_A_20260622_115300/capture.txt") == b"capture"


def test_sync_archive_tree_preserves_legacy_mirror_when_zip_build_fails(
    monkeypatch,
    tmp_path,
):
    module = _sync_module("test_sync_archive_to_onedrive_safe_migration")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    day = source_root / "measurements" / "SESSION_A_20260622_115300"
    day.mkdir(parents=True)
    (day / "capture.txt").write_text("capture", encoding="utf-8")
    legacy_folder = mirror_root / "Archive" / "measurements" / "legacy_raw"
    legacy_folder.mkdir(parents=True)
    legacy_file = legacy_folder / "capture.txt"
    legacy_file.write_text("legacy-backup", encoding="utf-8")

    monkeypatch.setattr(
        module,
        "_build_day_zip",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated ZIP failure")),
    )

    with pytest.raises(RuntimeError, match="simulated ZIP failure"):
        module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    assert legacy_file.read_text(encoding="utf-8") == "legacy-backup"
    assert not (
        mirror_root / "Archive" / "measurements" / "measurements_manifest.txt"
    ).exists()


def test_clean_destination_kind_removes_readonly_legacy_content(tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_readonly_cleanup")

    kind_root = tmp_path / "measurements"
    legacy_folder = kind_root / "legacy_raw"
    legacy_folder.mkdir(parents=True)
    nested_file = legacy_folder / "capture.nxs.h5"
    loose_file = kind_root / "old.txt"
    nested_file.write_text("legacy", encoding="utf-8")
    loose_file.write_text("legacy", encoding="utf-8")
    nested_file.chmod(stat.S_IREAD)
    loose_file.chmod(stat.S_IREAD)

    removed = module._clean_destination_kind(
        kind_root,
        bootstrap=True,
        dry_run=False,
    )

    assert removed == 2
    assert list(kind_root.iterdir()) == []


def test_h5_manifest_lists_nexus_container_once(tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_unique_h5")

    source_root = tmp_path / "Archive"
    item = source_root / "measurements" / "SESSION_A_20260622_115300"
    item.mkdir(parents=True)
    (item / "sample.nxs.h5").write_text("not-real-h5", encoding="utf-8")

    summaries = module._h5_summaries_for_items([item], source_root=source_root)

    assert len(summaries) == 1
    assert summaries[0]["file"] == "sample.nxs.h5"


def test_sync_archive_tree_removes_each_staging_zip_after_copy(monkeypatch, tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_bounded_staging")

    source_root = tmp_path / "Archive"
    mirror_root = tmp_path / "OneDriveRoot"
    for day_token in ("20260622", "20260623"):
        day = source_root / "measurements" / f"SESSION_A_{day_token}_115300"
        day.mkdir(parents=True)
        (day / "capture.txt").write_text(day_token, encoding="utf-8")

    built_paths = []
    original_build = module._build_day_zip

    def _tracked_build(**kwargs):
        if built_paths:
            assert built_paths[-1].exists() is False
        built_path = original_build(**kwargs)
        built_paths.append(built_path)
        return built_path

    monkeypatch.setattr(module, "_build_day_zip", _tracked_build)

    summary = module.sync_archive_tree(source_root=source_root, mirror_root=mirror_root)

    assert summary.created_zip_files == 2
    assert len(built_paths) == 2
    assert all(path.exists() is False for path in built_paths)


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


def test_start_archive_zip_sync_process_reuses_running_process(monkeypatch, tmp_path):
    module = _sync_module("test_sync_archive_to_onedrive_process_reuse")
    created = []

    class FakeProcess:
        def __init__(self):
            self.returncode = None

        def poll(self):
            return self.returncode

    def _popen(*_args, **_kwargs):
        process = FakeProcess()
        created.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", _popen)

    kwargs = {
        "source_root": tmp_path / "Archive",
        "mirror_root": tmp_path / "OneDrive",
    }
    first = module.start_archive_zip_sync_process(**kwargs)
    second = module.start_archive_zip_sync_process(**kwargs)
    first.returncode = 0
    third = module.start_archive_zip_sync_process(**kwargs)

    assert first is second
    assert third is not first
    assert len(created) == 2


def test_finalize_workflow_requests_only_one_full_archive_sync(monkeypatch, tmp_path):
    from difra.gui.session_finalize_workflow import SessionFinalizeWorkflow
    from difra.gui.session_lifecycle_service import SessionLifecycleService

    archive_folder = tmp_path / "Archive" / "measurements" / "SESSION_A_20260622"
    archive_folder.mkdir(parents=True)
    bundle_path = archive_folder.with_suffix(".zip")
    bundle_path.write_bytes(b"bundle")
    calls = []
    expected_destination = tmp_path / "OneDrive" / "Archive" / "measurements"

    monkeypatch.setattr(
        SessionLifecycleService,
        "copy_archive_item_to_mirror",
        lambda source_path, **kwargs: calls.append((source_path, kwargs))
        or expected_destination,
    )

    destination = SessionFinalizeWorkflow.mirror_archive_outputs(
        archive_folder,
        config={"measurements_archive_mirror_folder": str(tmp_path / "OneDrive")},
        bundle_path=bundle_path,
    )

    assert destination == expected_destination
    assert calls == [
        (
            archive_folder,
            {
                "config": {
                    "measurements_archive_mirror_folder": str(tmp_path / "OneDrive")
                },
                "archive_kind": "measurements",
            },
        )
    ]


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
