from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import h5py

from container.v0_2 import writer as session_writer
from difra.gui.main_window_ext import session_mixin as session_mixin_module
from difra.gui.main_window_ext import session_flow_actions as module


class _FakePixmap:
    def __init__(self, path: str) -> None:
        self.path = path

    def isNull(self) -> bool:
        return False


def test_prompt_and_attach_sample_image_returns_none_when_user_declines(monkeypatch):
    monkeypatch.setattr(
        module.QInputDialog,
        "getItem",
        lambda *args, **kwargs: ("Skip for now", True),
    )

    owner = SimpleNamespace()

    result = module.prompt_and_attach_sample_image(owner)

    assert result is None


def test_prompt_and_attach_sample_image_only_offers_container_import(monkeypatch):
    captured_options = {}

    def _fake_get_item(_owner, _title, _label, items, *_args):
        captured_options["items"] = list(items)
        return "Skip for now", True

    monkeypatch.setattr(module.QInputDialog, "getItem", _fake_get_item)

    result = module.prompt_and_attach_sample_image(SimpleNamespace())

    assert result is None
    assert captured_options["items"] == [
        "Load image and points from previous session container",
        "Skip for now",
    ]


def test_prompt_and_attach_sample_image_can_import_workspace_from_previous_session(monkeypatch):
    monkeypatch.setattr(
        module.QInputDialog,
        "getItem",
        lambda *args, **kwargs: (
            "Load image and points from previous session container",
            True,
        ),
    )
    monkeypatch.setattr(
        module,
        "_import_workspace_from_previous_session",
        lambda owner: "Session: session_old.nxs.h5",
    )

    owner = SimpleNamespace()

    result = module.prompt_and_attach_sample_image(owner)

    assert result == "Session: session_old.nxs.h5"


def test_find_archived_session_candidates_filters_by_exact_specimen_id(tmp_path):
    archive_root = tmp_path / "archive" / "measurements"
    matching_dir = archive_root / "matching"
    other_dir = archive_root / "other"
    matching_dir.mkdir(parents=True, exist_ok=True)
    other_dir.mkdir(parents=True, exist_ok=True)

    _sid_match, matching_path = session_writer.create_session_container(
        folder=matching_dir,
        sample_id="SPEC_001",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-14",
        study_name="STUDY_A",
    )
    _sid_other, other_path = session_writer.create_session_container(
        folder=other_dir,
        sample_id="SPEC_999",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-15",
        study_name="STUDY_B",
    )

    with h5py.File(matching_path, "a") as h5f:
        h5f.attrs["specimenId"] = "SPEC_001"
        h5f.attrs["distance_cm"] = 17.0
    with h5py.File(other_path, "a") as h5f:
        h5f.attrs["specimenId"] = "SPEC_999"
        h5f.attrs["distance_cm"] = 2.0

    owner = SimpleNamespace(
        config={"measurements_archive_folder": str(archive_root)},
        session_manager=SimpleNamespace(session_path=tmp_path / "active_session.nxs.h5"),
    )

    candidates = module._find_archived_session_candidates(owner, "SPEC_001")

    assert candidates == []


def test_find_archived_session_candidates_keeps_only_locked_and_valid_matches(
    tmp_path, monkeypatch
):
    archive_root = tmp_path / "archive" / "measurements"
    locked_valid_dir = archive_root / "locked_valid"
    unlocked_dir = archive_root / "unlocked"
    invalid_dir = archive_root / "invalid"
    locked_valid_dir.mkdir(parents=True, exist_ok=True)
    unlocked_dir.mkdir(parents=True, exist_ok=True)
    invalid_dir.mkdir(parents=True, exist_ok=True)

    _sid_valid, valid_path = session_writer.create_session_container(
        folder=locked_valid_dir,
        sample_id="SPEC_001",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-14",
        study_name="STUDY_A",
    )
    _sid_unlocked, unlocked_path = session_writer.create_session_container(
        folder=unlocked_dir,
        sample_id="SPEC_001",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-15",
        study_name="STUDY_A",
    )
    _sid_invalid, invalid_path = session_writer.create_session_container(
        folder=invalid_dir,
        sample_id="SPEC_001",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-16",
        study_name="STUDY_A",
    )

    with h5py.File(valid_path, "a") as h5f:
        h5f.attrs["specimenId"] = "SPEC_001"
        h5f.attrs["distance_cm"] = 17.0
    with h5py.File(unlocked_path, "a") as h5f:
        h5f.attrs["specimenId"] = "SPEC_001"
        h5f.attrs["distance_cm"] = 2.0
    with h5py.File(invalid_path, "a") as h5f:
        h5f.attrs["specimenId"] = "SPEC_001"
        h5f.attrs["distance_cm"] = 25.0

    monkeypatch.setattr(
        module,
        "get_container_manager",
        lambda config: SimpleNamespace(
            is_container_locked=lambda path: Path(path) != Path(unlocked_path)
        ),
    )
    monkeypatch.setattr(
        module,
        "validate_container",
        lambda path, container_kind=None: SimpleNamespace(
            is_valid=Path(path) != Path(invalid_path)
        ),
    )

    owner = SimpleNamespace(
        config={"measurements_archive_folder": str(archive_root)},
        session_manager=SimpleNamespace(session_path=tmp_path / "active_session.nxs.h5"),
    )

    candidates = module._find_archived_session_candidates(owner, "SPEC_001")

    assert len(candidates) == 1
    assert Path(candidates[0]["path"]) == Path(valid_path)
    assert candidates[0]["specimen_id"] == "SPEC_001"


def test_find_archived_session_candidates_hides_not_complete_sessions(tmp_path, monkeypatch):
    archive_root = tmp_path / "archive" / "measurements"
    complete_dir = archive_root / "complete"
    incomplete_dir = archive_root / "incomplete"
    complete_dir.mkdir(parents=True, exist_ok=True)
    incomplete_dir.mkdir(parents=True, exist_ok=True)

    _sid_complete, complete_path = session_writer.create_session_container(
        folder=complete_dir,
        sample_id="SPEC_001",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-14",
        study_name="STUDY_A",
    )
    _sid_incomplete, incomplete_path = session_writer.create_session_container(
        folder=incomplete_dir,
        sample_id="SPEC_001",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-15",
        study_name="STUDY_A",
    )

    with h5py.File(complete_path, "a") as h5f:
        h5f.attrs["specimenId"] = "SPEC_001"
        h5f.attrs["session_completion_status"] = "complete"
        h5f.attrs["distance_cm"] = 17.0
    with h5py.File(incomplete_path, "a") as h5f:
        h5f.attrs["specimenId"] = "SPEC_001"
        h5f.attrs["transfer_status"] = "not_complete"
        h5f.attrs["session_completion_status"] = "not_complete"
        h5f.attrs["distance_cm"] = 2.0

    monkeypatch.setattr(
        module,
        "get_container_manager",
        lambda config: SimpleNamespace(is_container_locked=lambda path: True),
    )
    monkeypatch.setattr(
        module,
        "validate_container",
        lambda path, container_kind=None: SimpleNamespace(is_valid=True),
    )

    owner = SimpleNamespace(
        config={"measurements_archive_folder": str(archive_root)},
        session_manager=SimpleNamespace(session_path=tmp_path / "active_session.nxs.h5"),
    )

    candidates = module._find_archived_session_candidates(owner, "SPEC_001")

    assert len(candidates) == 1
    assert Path(candidates[0]["path"]) == Path(complete_path)


def test_on_import_workspace_from_session_uses_archived_session_picker(monkeypatch):
    called = {}
    harness = SimpleNamespace()

    monkeypatch.setattr(
        module,
        "_import_workspace_from_previous_session",
        lambda owner: called.setdefault("owner", owner),
    )

    session_mixin_module.SessionMixin.on_import_workspace_from_session(harness)

    assert called["owner"] is harness
