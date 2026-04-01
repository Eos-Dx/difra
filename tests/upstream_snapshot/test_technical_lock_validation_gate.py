"""GUI lock workflow tests: enforce validation before technical lock."""

import sys
import tempfile
from pathlib import Path

import h5py

# Add project src to path
SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# Import order matters because technical mixins reference each other.
import difra.gui.main_window_ext.technical.h5_management_mixin as _h5_mixin  # noqa: F401
import difra.gui.main_window_ext.technical.h5_management_locking_mixin as locking_mod
from difra.gui.main_window_ext.technical.h5_management_locking_mixin import (
    H5ManagementLockingMixin,
)
from difra.gui.main_window_ext.technical.poni_center_validation import (
    parse_poni_center_px,
    validate_poni_centers,
)


class _MessageBoxStub:
    Yes = 1
    No = 0
    critical_calls = []
    question_calls = []
    information_calls = []
    warning_calls = []

    @classmethod
    def reset(cls):
        cls.critical_calls = []
        cls.question_calls = []
        cls.information_calls = []
        cls.warning_calls = []

    @staticmethod
    def question(*args, **kwargs):
        _MessageBoxStub.question_calls.append((args, kwargs))
        return _MessageBoxStub.Yes

    @staticmethod
    def information(*args, **kwargs):
        _MessageBoxStub.information_calls.append((args, kwargs))
        return None

    @staticmethod
    def warning(*args, **kwargs):
        _MessageBoxStub.warning_calls.append((args, kwargs))
        return _MessageBoxStub.Yes

    @staticmethod
    def critical(_parent, title, text):
        _MessageBoxStub.critical_calls.append((title, text))
        return None


class _ContainerManagerStub:
    def __init__(self, *, locked=False):
        self._locked = bool(locked)

    def is_container_locked(self, _path):
        return self._locked


class _ValidatorStub:
    def __init__(self, result):
        self._result = result

    def validate_technical_container(self, *_args, **_kwargs):
        return self._result


class _Harness(H5ManagementLockingMixin):
    def __init__(self, active_path: Path):
        self.config = {"container_version": "0.2"}
        self._active_technical_container_path = str(active_path)
        self._active_technical_container_locked = False
        self.lock_calls = []
        self.events = []
        self._distance_map_for_test = {"SAXS": 17.0}
        self.distance_dialog_calls = 0

    def _lock_container(self, container_path: str, container_id: str):
        self.lock_calls.append((container_path, container_id))

    def _log_technical_event(self, msg: str):
        self.events.append(msg)

    def _get_active_detector_aliases(self):
        return ["SAXS"]

    def _distance_map_by_alias(self):
        return dict(self._distance_map_for_test)

    def configure_detector_distances(self):
        self.distance_dialog_calls += 1


def _create_container(path: Path, *, schema_version: str = "0.2"):
    with h5py.File(path, "w") as h5f:
        h5f.attrs["container_id"] = "tech_test_001"
        h5f.attrs["schema_version"] = schema_version


def test_lock_is_blocked_when_validation_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path)
        harness = _Harness(container_path)
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(
            locking_mod,
            "get_technical_validator",
            lambda _cfg: _ValidatorStub((False, ["Missing required group: /entry"], [])),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(harness, "_ensure_poni_before_lock", lambda *_args, **_kwargs: True)

        harness.lock_active_technical_container()

        assert harness.lock_calls == []
        assert harness._active_technical_container_locked is False
        assert len(_MessageBoxStub.critical_calls) == 1
        assert "Validation Failed" in _MessageBoxStub.critical_calls[0][0]
        with h5py.File(container_path, "r") as h5f:
            assert h5f.attrs.get("container_state") == "validation_failed"


def test_lock_proceeds_when_validation_passes(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(
            locking_mod,
            "get_technical_validator",
            lambda _cfg: _ValidatorStub((True, [], [])),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(harness, "_ensure_poni_before_lock", lambda *_args, **_kwargs: True)

        harness.lock_active_technical_container()

        assert len(harness.lock_calls) == 1
        assert harness._active_technical_container_locked is True
        assert _MessageBoxStub.critical_calls == []
        with h5py.File(container_path, "r") as h5f:
            assert h5f.attrs.get("container_state") == "locked"


def test_lock_is_blocked_when_user_declines_poni_selection(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(
            locking_mod,
            "get_technical_validator",
            lambda _cfg: _ValidatorStub((True, [], [])),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)

        original_question = _MessageBoxStub.question
        _MessageBoxStub.question = staticmethod(lambda *args, **kwargs: _MessageBoxStub.No)
        try:
            harness.lock_active_technical_container()
        finally:
            _MessageBoxStub.question = original_question

        assert harness.lock_calls == []
        assert harness._active_technical_container_locked is False


def test_demo_mode_auto_provisions_poni_then_locks(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        harness.config["DEV"] = True
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(
            locking_mod,
            "get_technical_validator",
            lambda _cfg: _ValidatorStub((True, [], [])),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(
            harness,
            "_collect_lock_detector_aliases",
            lambda *_args, **_kwargs: ["SAXS"],
        )
        monkeypatch.setattr(
            harness,
            "_auto_provision_demo_poni_files",
            lambda *_args, **_kwargs: True,
        )
        monkeypatch.setattr(
            harness,
            "_sync_active_technical_container_from_table",
            lambda **_kwargs: True,
            raising=False,
        )
        states = iter([False, True])
        monkeypatch.setattr(
            harness,
            "_container_has_poni_datasets",
            lambda *_args, **_kwargs: next(states),
        )

        harness.lock_active_technical_container()

        assert len(harness.lock_calls) == 1
        assert harness._active_technical_container_locked is True
        assert len(_MessageBoxStub.information_calls) >= 1


def test_demo_mode_lock_auto_syncs_demo_poni_before_regular_lock_flow(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        harness.config["DEV"] = True
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(
            locking_mod,
            "get_technical_validator",
            lambda _cfg: _ValidatorStub((True, [], [])),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)

        call_order = []
        monkeypatch.setattr(
            harness,
            "_container_has_poni_datasets",
            lambda *_a, **_k: False,
        )
        monkeypatch.setattr(
            harness,
            "_collect_lock_detector_aliases",
            lambda *_a, **_k: ["SAXS", "WAXS"],
        )
        monkeypatch.setattr(
            harness,
            "_auto_provision_demo_poni_files",
            lambda aliases: (call_order.append(("auto", tuple(aliases))), True)[1],
        )
        monkeypatch.setattr(
            harness,
            "_sync_active_technical_container_from_table",
            lambda **kwargs: (call_order.append(("sync", bool(kwargs.get("show_errors")))), True)[1],
            raising=False,
        )
        monkeypatch.setattr(
            harness,
            "_ensure_poni_before_lock",
            lambda *_a, **_k: (call_order.append(("ensure", None)), True)[1],
        )

        harness.lock_active_technical_container()

        assert ("auto", ("SAXS", "WAXS")) in call_order
        assert ("sync", False) in call_order
        assert ("ensure", None) in call_order
        assert call_order.index(("auto", ("SAXS", "WAXS"))) < call_order.index(("ensure", None))
        assert len(harness.lock_calls) == 1


def test_pre_lock_sync_is_silent_when_sync_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)

        sync_show_errors = []

        def _fake_sync(*, show_errors=False):
            sync_show_errors.append(bool(show_errors))
            return False

        monkeypatch.setattr(
            harness,
            "_sync_active_technical_container_from_table",
            _fake_sync,
            raising=False,
        )
        monkeypatch.setattr(harness, "_ensure_poni_before_lock", lambda *_a, **_k: False)

        harness.lock_active_technical_container()

        assert sync_show_errors == [False]
        assert _MessageBoxStub.warning_calls == []
        assert harness.lock_calls == []


def test_lock_is_blocked_when_distances_not_configured(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        harness._distance_map_for_test = {}
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(harness, "_ensure_poni_before_lock", lambda *_a, **_k: True)

        harness.lock_active_technical_container()

        assert harness.lock_calls == []
        assert harness.distance_dialog_calls == 1
        assert len(_MessageBoxStub.warning_calls) >= 1
        with h5py.File(container_path, "r") as h5f:
            assert h5f.attrs.get("container_state") == "pending_distances"


def test_center_validation_failure_suggests_updating_poni_or_main_config(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(
            locking_mod,
            "get_technical_validator",
            lambda _cfg: _ValidatorStub((True, [], [])),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(harness, "_ensure_poni_before_lock", lambda *_a, **_k: True)
        monkeypatch.setattr(
            harness,
            "_validate_poni_centers_for_container",
            lambda *_a, **_k: (
                [
                    (
                        "PONI center for SAXS is outside the allowed zone. "
                        "Actual center: row=128.00px, col=200.00px. "
                        "Allowed rule: col > 256.00."
                    )
                ],
                [],
            ),
        )

        harness.lock_active_technical_container()

        assert harness.lock_calls == []
        assert len(_MessageBoxStub.critical_calls) == 1
        title, text = _MessageBoxStub.critical_calls[0]
        assert title == "Validation Failed"
        assert "Actual center: row=128.00px, col=200.00px" in text
        assert "Allowed rule: col > 256.00." in text
        assert "update the PONI center values" in text
        assert "poni_center_validation" in text


def test_update_poni_updates_active_container_with_silent_sync(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(
            harness,
            "_collect_lock_detector_aliases",
            lambda *_a, **_k: ["PRIMARY", "SECONDARY"],
        )
        monkeypatch.setattr(
            harness,
            "_prompt_poni_selection_for_lock",
            lambda *_a, **_k: True,
        )

        sync_flags = []
        monkeypatch.setattr(
            harness,
            "_sync_active_technical_container_from_table",
            lambda **kwargs: (sync_flags.append(bool(kwargs.get("show_errors"))), True)[1],
            raising=False,
        )

        result = harness.update_active_technical_container_poni()

        assert result is True
        assert sync_flags == [False]
        assert len(_MessageBoxStub.information_calls) >= 1
        assert harness.events[-1].startswith("Updated PONI files for active technical container")


def test_lock_auto_uses_preselected_poni_files_without_prompt(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        _MessageBoxStub.reset()

        ready_dir = Path(tmp) / "poni_ready"
        ready_dir.mkdir(parents=True, exist_ok=True)
        saxs_path = ready_dir / "saxs_demo.poni"
        waxs_path = ready_dir / "waxs_demo.poni"
        saxs_path.write_text("Distance: 0.17\nPixelSize1: 5.5e-05\n", encoding="utf-8")
        waxs_path.write_text("Distance: 0.17\nPixelSize1: 5.5e-05\n", encoding="utf-8")
        harness.poni_files = {
            "SAXS": {"path": str(saxs_path), "name": saxs_path.name},
            "WAXS": {"path": str(waxs_path), "name": waxs_path.name},
        }

        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(
            harness,
            "_collect_lock_detector_aliases",
            lambda *_a, **_k: ["SAXS", "WAXS"],
        )
        sync_calls = []
        monkeypatch.setattr(
            harness,
            "_sync_active_technical_container_from_table",
            lambda **kwargs: (sync_calls.append(bool(kwargs.get("show_errors"))), True)[1],
            raising=False,
        )
        has_poni_states = iter([False, True])
        monkeypatch.setattr(
            harness,
            "_container_has_poni_datasets",
            lambda *_a, **_k: next(has_poni_states),
        )
        monkeypatch.setattr(
            harness,
            "_prompt_poni_selection_for_lock",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("selection dialog should be skipped")),
        )

        result = harness._ensure_poni_before_lock(container_path, "tech_test_001")

        assert result is True
        assert sync_calls == [False]
        assert any("auto-applied before lock" in event for event in harness.events)


def test_update_poni_blocks_for_locked_container(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=True),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)

        result = harness.update_active_technical_container_poni()

        assert result is False
        assert len(_MessageBoxStub.warning_calls) >= 1


def test_lock_is_cancelled_when_operator_rejects_preview_confirmation(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        _MessageBoxStub.reset()

        monkeypatch.setattr(
            locking_mod,
            "get_container_manager",
            lambda _cfg: _ContainerManagerStub(locked=False),
        )
        monkeypatch.setattr(
            locking_mod,
            "get_technical_validator",
            lambda _cfg: _ValidatorStub((True, [], [])),
        )
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(harness, "_ensure_poni_before_lock", lambda *_a, **_k: True)
        monkeypatch.setattr(harness, "_validate_container_before_lock", lambda *_a, **_k: True)
        monkeypatch.setattr(
            harness,
            "_run_poni_center_review_workflow",
            lambda *_a, **_k: False,
            raising=False,
        )
        harness.config["poni_center_validation"] = {"enabled": True, "detectors": {"SAXS": {}}}
        harness.lock_active_technical_container()

        assert harness.lock_calls == []
    assert harness._active_technical_container_locked is False
    assert any("PONI center review must be re-confirmed before lock" in e for e in harness.events)


def test_poni_review_accept_persists_accepted_in_valid_zone(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        harness.config["poni_center_validation"] = {"enabled": True, "detectors": {"SAXS": {}}}
        _MessageBoxStub.reset()

        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(
            harness,
            "_show_poni_center_preview_for_container",
            lambda *_a, **_k: True,
            raising=False,
        )
        monkeypatch.setattr(
            harness,
            "_validate_poni_centers_for_container",
            lambda *_a, **_k: ([], []),
        )

        result = harness._run_poni_center_review_workflow(
            container_path,
            container_id="tech_test_001",
            prompt_reload_on_reject=True,
        )

        assert result is True
        with h5py.File(container_path, "r") as h5f:
            assert h5f.attrs.get("poni_center_review_status") == "accepted"
            assert bool(h5f.attrs.get("poni_center_in_allowed_zone", False)) is True
            assert str(h5f.attrs.get("poni_center_review_reason", "") or "") == ""
            assert h5f.attrs.get("container_state") == "ready_to_lock"


def test_poni_review_accept_out_of_zone_hard_fails_and_blocks_lock(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        harness.config["poni_center_validation"] = {"enabled": True, "detectors": {"SAXS": {}}}
        _MessageBoxStub.reset()

        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(
            harness,
            "_show_poni_center_preview_for_container",
            lambda *_a, **_k: True,
            raising=False,
        )
        monkeypatch.setattr(
            harness,
            "_validate_poni_centers_for_container",
            lambda *_a, **_k: (["center out of zone"], []),
        )
        original_question = _MessageBoxStub.question
        _MessageBoxStub.question = staticmethod(lambda *args, **kwargs: _MessageBoxStub.No)
        try:
            result = harness._run_poni_center_review_workflow(
                container_path,
                container_id="tech_test_001",
                prompt_reload_on_reject=True,
            )
        finally:
            _MessageBoxStub.question = original_question

        assert result is False
        assert len(_MessageBoxStub.critical_calls) >= 1
        title, text = _MessageBoxStub.critical_calls[0]
        assert title == "PONI Validation Failed"
        assert "PONI center is outside the allowed zone." in text
        assert "- center out of zone" in text
        assert "poni_center_validation" in text
        with h5py.File(container_path, "r") as h5f:
            assert h5f.attrs.get("poni_center_review_status") == "rejected"
            assert bool(h5f.attrs.get("poni_center_in_allowed_zone", True)) is False
            assert h5f.attrs.get("poni_center_review_reason") == "center_out_of_zone"
            assert h5f.attrs.get("container_state") == "rejected_blocked"
        assert any("Lock Blocked" in args[1] for args, _kwargs in _MessageBoxStub.warning_calls)


def test_poni_reject_requires_reason_code(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        harness.config["poni_center_validation"] = {"enabled": True, "detectors": {"SAXS": {}}}
        _MessageBoxStub.reset()
        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(
            harness,
            "_show_poni_center_preview_for_container",
            lambda *_a, **_k: False,
            raising=False,
        )

        result = harness._run_poni_center_review_workflow(
            container_path,
            container_id="tech_test_001",
            prompt_reload_on_reject=False,
        )

        assert result is False
        with h5py.File(container_path, "r") as h5f:
            assert h5f.attrs.get("poni_center_review_status") == "rejected"
            assert h5f.attrs.get("poni_center_review_reason") == "user_rejected_preview"


def test_smoke_review_happy_then_reject_updates_state_machine(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        container_path = Path(tmp) / "technical_test.nxs.h5"
        _create_container(container_path, schema_version="0.2")
        harness = _Harness(container_path)
        harness.config["poni_center_validation"] = {"enabled": True, "detectors": {"SAXS": {}}}
        _MessageBoxStub.reset()

        monkeypatch.setattr(locking_mod, "QMessageBox", _MessageBoxStub)
        monkeypatch.setattr(
            harness,
            "_show_poni_center_preview_for_container",
            lambda *_a, **_k: True,
            raising=False,
        )
        monkeypatch.setattr(
            harness,
            "_validate_poni_centers_for_container",
            lambda *_a, **_k: ([], []),
        )

        happy = harness._run_poni_center_review_workflow(
            container_path,
            container_id="tech_test_001",
            prompt_reload_on_reject=False,
        )
        assert happy is True
        with h5py.File(container_path, "r") as h5f:
            assert h5f.attrs.get("poni_center_review_status") == "accepted"
            assert h5f.attrs.get("container_state") == "ready_to_lock"

        monkeypatch.setattr(
            harness,
            "_show_poni_center_preview_for_container",
            lambda *_a, **_k: False,
            raising=False,
        )
        reject = harness._run_poni_center_review_workflow(
            container_path,
            container_id="tech_test_001",
            prompt_reload_on_reject=False,
        )
        assert reject is False
        with h5py.File(container_path, "r") as h5f:
            assert h5f.attrs.get("poni_center_review_status") == "rejected"
            assert h5f.attrs.get("poni_center_review_reason") == "user_rejected_preview"
            assert h5f.attrs.get("container_state") == "rejected_blocked"


def test_demo_fake_poni_generation_uses_center_validation_targets():
    harness = _Harness(Path("/tmp/nonexistent_demo_container.nxs.h5"))
    harness.config = {
        "DEV": True,
        "poni_center_validation": {
            "enabled": True,
            "defaults": {"row_tolerance_percent": 5.0},
            "detectors": {
                "PRIMARY": {
                    "row_target_px": 128,
                    "row_tolerance_px": 10,
                    "col_target_px": 8,
                    "col_tolerance_px": 10,
                    "col_max_px": 20,
                },
                "SECONDARY": {
                    "row_target_px": 128,
                    "row_tolerance_px": 10,
                    "col_gt_px": 256,
                },
            },
        },
    }

    primary_center = harness._resolve_demo_poni_center_px("PRIMARY", (256, 256))
    secondary_center = harness._resolve_demo_poni_center_px("SECONDARY", (256, 256))
    primary_poni = harness._build_fake_poni_content(
        alias="PRIMARY",
        distance_cm=17.0,
        detector_size=(256, 256),
        pixel_size_um=(55.0, 55.0),
        center_px=primary_center,
    )
    secondary_poni = harness._build_fake_poni_content(
        alias="SECONDARY",
        distance_cm=17.0,
        detector_size=(256, 256),
        pixel_size_um=(55.0, 55.0),
        center_px=secondary_center,
    )

    parsed_primary = parse_poni_center_px(primary_poni, fallback_detector_size=(256, 256))
    parsed_secondary = parse_poni_center_px(secondary_poni, fallback_detector_size=(256, 256))
    assert parsed_primary is not None
    assert parsed_secondary is not None

    errors, warnings = validate_poni_centers(
        poni_text_by_alias={"PRIMARY": primary_poni, "SECONDARY": secondary_poni},
        detector_sizes_by_alias={"PRIMARY": (256, 256), "SECONDARY": (256, 256)},
        validation_config=harness.config["poni_center_validation"],
    )
    assert errors == []
    assert warnings == []


def test_demo_poni_compliance_detects_wrong_center_and_accepts_secondary_over_256():
    harness = _Harness(Path("/tmp/nonexistent_demo_container.nxs.h5"))
    harness.config = {
        "DEV": True,
        "poni_center_validation": {
            "enabled": True,
            "detectors": {
                "PRIMARY": {
                    "row_target_px": 128,
                    "col_target_px": 8,
                },
                "SECONDARY": {
                    "row_target_px": 128,
                    "col_gt_px": 256,
                },
            },
        },
    }

    good_primary = harness._build_fake_poni_content(
        alias="PRIMARY",
        distance_cm=17.0,
        detector_size=(256, 256),
        pixel_size_um=(55.0, 55.0),
        center_px=(128.0, 8.0),
    )
    bad_primary = harness._build_fake_poni_content(
        alias="PRIMARY",
        distance_cm=17.0,
        detector_size=(256, 256),
        pixel_size_um=(55.0, 55.0),
        center_px=(60.0, 60.0),
    )
    secondary_over = harness._build_fake_poni_content(
        alias="SECONDARY",
        distance_cm=17.0,
        detector_size=(256, 256),
        pixel_size_um=(55.0, 55.0),
        center_px=(128.0, 280.0),
    )

    assert harness._demo_poni_is_compliant(
        alias="PRIMARY",
        poni_text=good_primary,
        detector_size=(256, 256),
    )
    assert not harness._demo_poni_is_compliant(
        alias="PRIMARY",
        poni_text=bad_primary,
        detector_size=(256, 256),
    )
    assert harness._demo_poni_is_compliant(
        alias="SECONDARY",
        poni_text=secondary_over,
        detector_size=(256, 256),
    )


def test_demo_center_alias_mapping_supports_saxs_and_waxs():
    harness = _Harness(Path("/tmp/nonexistent_demo_container.nxs.h5"))
    harness.config = {
        "detectors": [
            {"alias": "SAXS", "poni_center_rule_alias": "PRIMARY"},
            {"alias": "WAXS", "poni_center_rule_alias": "SECONDARY"},
        ]
    }

    assert harness._resolve_demo_poni_center_px("SAXS", (256, 256)) == (128.0, 8.0)
    assert harness._resolve_demo_poni_center_px("WAXS", (256, 256)) == (128.0, 280.0)
