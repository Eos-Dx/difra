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


def test_clear_session_workspace_resets_state_and_refreshes_tables():
    calls = []
    owner = SimpleNamespace(
        state={
            "shapes": [{"id": 1}],
            "zone_points": [{"id": 2}],
            "measurement_points": [{"id": 3}],
            "skipped_points": [{"id": 4}],
        },
        state_measurements={
            "measurement_points": [{"id": 5}],
            "skipped_points": [{"id": 6}],
        },
        measurement_widgets=[],
        delete_all_shapes_from_table=lambda force=True: calls.append(("shapes", force)),
        delete_all_points=lambda: calls.append(("points", None)),
        update_shape_table=lambda: calls.append(("shape_table", None)),
        update_points_table=lambda: calls.append(("points_table", None)),
    )

    module.clear_session_workspace(owner)

    assert calls == [
        ("shapes", True),
        ("points", None),
        ("shape_table", None),
        ("points_table", None),
    ]
    assert owner.state["shapes"] == []
    assert owner.state["zone_points"] == []
    assert owner.state["measurement_points"] == []
    assert owner.state["skipped_points"] == []
    assert owner.state_measurements["measurement_points"] == []
    assert owner.state_measurements["skipped_points"] == []
    assert owner.measurement_widgets == {}


def test_on_new_session_clears_workspace_before_prompt(monkeypatch, tmp_path):
    order = []

    class _FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec_(self):
            return session_mixin_module.QDialog.Accepted

        def get_parameters(self):
            return {
                "sample_id": "SPEC_001",
                "study_name": "STUDY_A",
                "operator_id": "sad",
                "distance_cm": 17.0,
            }

    class _FakeSessionManager:
        def __init__(self):
            self.sample_id = None
            self.session_path = None

        def is_session_active(self):
            return False

        def create_session(self, folder, **kwargs):
            self.sample_id = kwargs["sample_id"]
            self.session_path = Path(folder) / "session_new.nxs.h5"
            return "session_new", self.session_path

    monkeypatch.setattr(session_mixin_module, "NewSessionDialog", _FakeDialog)
    monkeypatch.setattr(
        session_mixin_module.session_flow_actions,
        "clear_session_workspace",
        lambda owner: order.append("clear"),
    )
    monkeypatch.setattr(
        session_mixin_module.session_flow_actions,
        "prompt_and_attach_sample_image",
        lambda owner: order.append("prompt") or None,
    )
    monkeypatch.setattr(
        session_mixin_module.QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: session_mixin_module.QMessageBox.Ok),
    )

    owner = SimpleNamespace(
        operator_manager=object(),
        session_manager=_FakeSessionManager(),
        _default_session_distance_cm=lambda: 17.0,
        get_session_folder=lambda: tmp_path,
        _append_session_log=lambda message: None,
        update_session_status=lambda: order.append("status"),
    )

    session_mixin_module.SessionMixin.on_new_session(owner)

    assert order[:2] == ["clear", "prompt"]
    assert "status" in order


def test_on_new_session_allows_replacing_locked_archived_session(
    monkeypatch, tmp_path
):
    archive_root = tmp_path / "archive" / "measurements"
    archived_dir = archive_root / "20260407"
    archived_dir.mkdir(parents=True, exist_ok=True)
    archived_path = archived_dir / "session_old.nxs.h5"
    archived_path.write_text("placeholder", encoding="utf-8")

    order = []
    warnings = []

    class _FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec_(self):
            return session_mixin_module.QDialog.Accepted

        def get_parameters(self):
            return {
                "sample_id": "SPEC_002",
                "study_name": "STUDY_B",
                "operator_id": "sad",
                "distance_cm": 17.0,
            }

    class _FakeSessionManager:
        def __init__(self):
            self.sample_id = "SPEC_001"
            self.session_path = archived_path
            self.create_calls = 0

        def is_session_active(self):
            return True

        def is_locked(self):
            return True

        def create_session(self, folder, **kwargs):
            self.create_calls += 1
            self.sample_id = kwargs["sample_id"]
            self.session_path = Path(folder) / "session_new.nxs.h5"
            return "session_new", self.session_path

    monkeypatch.setattr(session_mixin_module, "NewSessionDialog", _FakeDialog)
    monkeypatch.setattr(
        session_mixin_module.session_flow_actions,
        "clear_session_workspace",
        lambda owner: order.append("clear"),
    )
    monkeypatch.setattr(
        session_mixin_module.session_flow_actions,
        "prompt_and_attach_sample_image",
        lambda owner: order.append("prompt") or None,
    )
    monkeypatch.setattr(
        session_mixin_module.QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: warnings.append(args[2]) or session_mixin_module.QMessageBox.Ok),
    )
    monkeypatch.setattr(
        session_mixin_module.QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: session_mixin_module.QMessageBox.Ok),
    )

    manager = _FakeSessionManager()
    owner = SimpleNamespace(
        config={
            "measurements_folder": str(tmp_path / "measurements"),
            "measurements_archive_folder": str(archive_root),
        },
        operator_manager=object(),
        session_manager=manager,
        _default_session_distance_cm=lambda: 17.0,
        get_session_folder=lambda: tmp_path / "measurements",
        _append_session_log=lambda message: order.append(message),
        update_session_status=lambda: order.append("status"),
    )

    session_mixin_module.SessionMixin.on_new_session(owner)

    assert manager.create_calls == 1
    assert warnings == []
    assert "clear" in order
    assert "prompt" in order


def test_on_new_session_blocks_replacing_locked_nonarchived_session(
    monkeypatch, tmp_path
):
    live_dir = tmp_path / "measurements"
    live_dir.mkdir(parents=True, exist_ok=True)
    live_path = live_dir / "session_live.nxs.h5"
    live_path.write_text("placeholder", encoding="utf-8")

    warnings = []

    class _FakeSessionManager:
        sample_id = "SPEC_001"
        session_path = live_path

        def is_session_active(self):
            return True

        def is_locked(self):
            return True

    monkeypatch.setattr(
        session_mixin_module.QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: warnings.append(args[2]) or session_mixin_module.QMessageBox.Ok),
    )

    owner = SimpleNamespace(
        config={
            "measurements_folder": str(live_dir),
            "measurements_archive_folder": str(tmp_path / "archive" / "measurements"),
        },
        session_manager=_FakeSessionManager(),
        _append_session_log=lambda message: None,
        get_session_folder=lambda: live_dir,
    )

    session_mixin_module.SessionMixin.on_new_session(owner)

    assert len(warnings) == 1
    assert "A session container is already open." in warnings[0]


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
