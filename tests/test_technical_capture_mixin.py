from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np

from difra.gui.main_window_ext.technical.capture_mixin import (
    TechnicalCaptureMixin,
)
from difra.gui.technical.pyfai_calibration import (
    PyfaiCalib2Review,
    normalized_auto_poni_config,
)


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _FakeThread:
    def __init__(self):
        self.started = _FakeSignal()

    def start(self):
        return None

    def quit(self):
        return None

    def deleteLater(self):
        return None


class _FakeWorker:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.finished = _FakeSignal()
        _FakeWorker.instances.append(self)

    def moveToThread(self, _thread):
        return None

    def run(self):
        return None

    def deleteLater(self):
        return None


class _FakeCheckBox:
    def __init__(self, checked):
        self._checked = bool(checked)

    def isChecked(self):
        return self._checked


class _FakeSpin:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _FakeTimer:
    def __init__(self):
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1


class _FakeStatus:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = str(value)


class _FakeStageController:
    def __init__(self):
        self.moves = []

    def move_stage(self, x, y, move_timeout=20):
        self.moves.append((x, y, move_timeout))
        return x, y

    def get_xy_position(self):
        return 1.0, 2.0


class _FakeHardwareClient:
    def __init__(self, stage_controller):
        self.stage_controller = stage_controller


class _Harness(TechnicalCaptureMixin):
    AUX_COL_FILE = 0

    def __init__(self, *, checked=True):
        self.config = {
            "detectors": [
                {"id": "det_primary", "alias": "PRIMARY"},
                {"id": "det_secondary", "alias": "SECONDARY"},
            ]
        }
        self.hardware_controller = None
        self.stage_controller = None
        self.hardware_client = _FakeHardwareClient(_FakeStageController())
        self.moveContinuousCheck = _FakeCheckBox(checked)
        self.movementRadiusSpin = _FakeSpin(2.5)
        self.integrationTimeSpin = _FakeSpin(3.0)
        self.captureFramesSpin = _FakeSpin(4)
        self.detector_controller = {"SAXS": object()}
        self._detector_distances = {"SAXS": 17.0, "WAXS": 17.0}
        self.continuous_movement_controller = None
        self._capture_workers = []
        self.logged_events = []

    def _technical_imports_available(self):
        return True

    def _log_technical_event(self, message):
        self.logged_events.append(str(message))

    def _current_technical_output_folder(self):
        return "/tmp"

    def _active_technical_container_path_obj(self):
        raw = str(getattr(self, "_active_technical_container_path", "") or "")
        return Path(raw) if raw else None

    def _file_base(self, typ):
        return f"{typ.lower()}_base"

    def _get_technical_module(self, name):
        if name == "validate_folder":
            return lambda folder: Path(folder)
        if name == "CaptureWorker":
            return _FakeWorker
        raise AssertionError(f"Unexpected technical module request: {name}")

    def _on_capture_done(self, *args, **kwargs):
        return None

    def _collect_container_poni_text_by_alias(self, container_path: Path):
        payload = {}
        with h5py.File(container_path, "r") as h5f:
            poni_group = h5f.get("/entry/technical/poni")
            if poni_group is None:
                return payload
            for name, ds in poni_group.items():
                alias = ds.attrs.get("detector_alias", "")
                detector_id = ds.attrs.get("detector_id", "")
                value = ds[()]
                if isinstance(value, bytes):
                    value = value.decode("utf-8")
                alias_candidates = set()
                alias_candidates.update(self._normalize_technical_alias_candidates(alias))
                alias_candidates.update(
                    self._normalize_technical_alias_candidates(detector_id)
                )
                alias_candidates.update(self._normalize_technical_alias_candidates(name))
                for candidate in alias_candidates:
                    payload[str(candidate)] = str(value)
        return payload


class _CaptureDoneHarness(_Harness):
    def __init__(self):
        super().__init__()
        self._aux_timer = _FakeTimer()
        self._aux_status = _FakeStatus()
        self.append_calls = []
        self.technical_module_requests = []

    def _on_capture_done(self, *args, **kwargs):
        return TechnicalCaptureMixin._on_capture_done(self, *args, **kwargs)

    def _append_captured_result_files_to_active_container(
        self,
        result_files,
        technical_type,
        *,
        show_errors=False,
    ):
        self.append_calls.append(
            {
                "result_files": dict(result_files),
                "technical_type": technical_type,
                "show_errors": bool(show_errors),
            }
        )
        return True

    def _get_technical_module(self, name):
        self.technical_module_requests.append(name)
        return super()._get_technical_module(name)


def _patch_tm(monkeypatch):
    fake_tm = SimpleNamespace(QThread=_FakeThread, QMessageBox=SimpleNamespace(warning=lambda *a, **k: None))
    monkeypatch.setattr(
        "difra.gui.main_window_ext.technical.capture_mixin._tm",
        lambda: fake_tm,
    )


def test_start_capture_enables_continuous_movement_for_agbh(monkeypatch):
    _FakeWorker.instances.clear()
    _patch_tm(monkeypatch)
    harness = _Harness(checked=True)

    harness._start_capture("AGBH")

    assert len(_FakeWorker.instances) == 1
    worker = _FakeWorker.instances[0]
    assert worker.kwargs["enable_continuous_movement"] is True
    assert worker.kwargs["stage_controller"] is harness.hardware_client.stage_controller
    assert worker.kwargs["continuous_movement_controller"] is not None
    assert (
        worker.kwargs["continuous_movement_controller"].stage_controller
        is harness.hardware_client.stage_controller
    )


def test_technical_capture_base_stem_includes_distance_and_contract_order():
    harness = _Harness()
    harness._file_base = lambda _typ: "AgBH"

    stem = harness._technical_capture_base_stem(
        typ="AGBH",
        count=1,
        timestamp_token="20260506_085558",
        integration_time_s=1.0,
        frames=1,
    )

    assert stem == "AgBH_17cm_003_20260506_085558_1.000000s_1frames"


def test_technical_capture_distance_prefers_active_container_root_attr(tmp_path):
    harness = _Harness()
    harness._detector_distances = {"SAXS": 17.0}
    container_path = tmp_path / "technical_abc_2cm_20260506.nxs.h5"
    with h5py.File(container_path, "w") as h5f:
        h5f.attrs["distance_cm"] = 2.0
    harness._active_technical_container_path = str(container_path)

    stem = harness._technical_capture_base_stem(
        typ="AGBH",
        count=1,
        timestamp_token="20260506_085558",
        integration_time_s=1.0,
        frames=1,
    )

    assert "_2cm_003_" in stem
    assert "_17cm_" not in stem


def test_auto_poni_defaults_use_active_container_distance_for_two_cm(tmp_path):
    harness = _Harness()
    container_path = tmp_path / "technical_abc_2cm_20260506.nxs.h5"
    with h5py.File(container_path, "w") as h5f:
        h5f.attrs["distance_cm"] = 2.0
    harness._active_technical_container_path = str(container_path)

    settings = harness._auto_poni_default_settings(
        normalized_auto_poni_config({}),
        ["PRIMARY", "SECONDARY"],
    )

    assert settings["distance_cm_by_alias"] == {
        "PRIMARY": 2.0,
        "SECONDARY": 2.0,
    }
    assert settings["first_visible_ring_by_alias"] == {
        "PRIMARY": 2,
        "SECONDARY": 5,
    }
    assert settings["rings_to_search_by_alias"] == {
        "PRIMARY": 5,
        "SECONDARY": 4,
    }


def test_auto_poni_defaults_use_first_ring_for_seventeen_cm(tmp_path):
    harness = _Harness()
    container_path = tmp_path / "technical_abc_17cm_20260506.nxs.h5"
    with h5py.File(container_path, "w") as h5f:
        h5f.attrs["distance_cm"] = 17.0
    harness._active_technical_container_path = str(container_path)

    settings = harness._auto_poni_default_settings(
        normalized_auto_poni_config({}),
        ["PRIMARY", "SECONDARY"],
    )

    assert settings["distance_cm_by_alias"] == {
        "PRIMARY": 17.0,
        "SECONDARY": 17.0,
    }
    assert settings["first_visible_ring_by_alias"] == {
        "PRIMARY": 1,
        "SECONDARY": 1,
    }
    assert settings["rings_to_search_by_alias"] == {
        "PRIMARY": 3,
        "SECONDARY": 3,
    }


def test_auto_poni_prepare_uses_dialog_distance_override(tmp_path):
    harness = _Harness()
    harness._current_technical_output_folder = lambda: str(tmp_path)
    agbh_path = tmp_path / "agbh_PRIMARY.npy"
    np.save(agbh_path, np.ones((8, 8), dtype=np.float32))

    prepared = harness._prepare_auto_poni_reviews(
        normalized_auto_poni_config({}),
        sources={"PRIMARY": str(agbh_path)},
        distance_cm_by_alias={"PRIMARY": 2.0},
        first_visible_ring_by_alias={"PRIMARY": 1},
    )

    review = prepared["reviews"]["PRIMARY"]
    assert "Distance: 0.02" in review.poni_text
    assert review.poni_path.parent == tmp_path / "autopony"
    assert review.image_path.name == "PRIMARY_pyfai.tif"
    assert review.poni_path.name == "PRIMARY.poni"
    assert (tmp_path / "autopony" / "PRIMARY.npt").exists()


def test_auto_poni_saxs_uses_physical_primary_detector_config():
    harness = _Harness()
    harness.config["detectors"] = [
        {
            "id": "MiniPIX G08-W0299",
            "alias": "PRIMARY",
            "size": {"width": 256, "height": 256},
            "pixel_size_um": [50, 50],
        },
        {
            "id": "DUMMY-0001",
            "alias": "SAXS",
            "poni_center_rule_alias": "PRIMARY",
            "type": "DummyDetector",
            "size": {"width": 256, "height": 256},
            "pixel_size_um": [100, 100],
        },
    ]

    cfg = harness._auto_poni_detector_config_for_alias("SAXS")

    assert cfg["alias"] == "SAXS"
    assert cfg["id"] == "DUMMY-0001"
    assert cfg["pixel_size_um"] == [50, 50]


def test_auto_poni_correct_uses_legacy_sidecar_env(monkeypatch):
    harness = _Harness()
    harness.config["conda"] = "eosdx13"
    monkeypatch.setenv("SIDECAR_ENV", "ulster38")

    assert harness._resolve_auto_poni_pyfai_calib2_env() == "ulster38"


def test_auto_poni_correct_env_config_has_priority(monkeypatch):
    harness = _Harness()
    harness.config["conda"] = "eosdx13"
    harness.config["auto_poni_calibration"] = {"pyfai_calib2_env": "pyfai37"}
    monkeypatch.setenv("SIDECAR_ENV", "ulster38")

    assert harness._resolve_auto_poni_pyfai_calib2_env() == "pyfai37"


def test_auto_poni_validate_writes_poni_next_to_agbh(tmp_path):
    harness = _Harness()
    harness._active_technical_container_path = str(tmp_path / "technical.nxs.h5")
    with h5py.File(harness._active_technical_container_path_obj(), "w"):
        pass
    agbh_path = tmp_path / "AgBH_001_PRIMARY.npy"
    np.save(agbh_path, np.ones((8, 8), dtype=np.float32))
    review = PyfaiCalib2Review(
        image_path=tmp_path / "autopony" / "AgBH_001_PRIMARY_pyfai.tif",
        poni_path=tmp_path / "autopony" / "generated.poni",
        command=[],
        poni_text="Distance: 0.02\n",
        source_path=agbh_path,
    )

    assert harness._validate_auto_poni_reviews({"PRIMARY": review})

    target = tmp_path / "AgBH_001_PRIMARY.poni"
    assert target.read_text(encoding="utf-8") == "Distance: 0.02\n"
    assert harness.poni_files["PRIMARY"]["path"] == str(target)
    assert not (tmp_path / "autopony" / "AgBH_001_PRIMARY.poni").exists()


def test_auto_poni_h5ref_output_uses_container_source_file(tmp_path):
    harness = _Harness()
    harness._current_technical_output_folder = lambda: str(tmp_path)
    source_path = tmp_path / "raw" / "AgBH_001_PRIMARY.npy"
    source_path.parent.mkdir()
    np.save(source_path, np.ones((8, 8), dtype=np.float32))
    container_path = tmp_path / "technical.nxs.h5"
    with h5py.File(container_path, "w") as h5f:
        ds = h5f.create_dataset("/entry/technical/tech_evt_000001/det_primary/processed_signal", data=np.ones((8, 8)))
        ds.parent.attrs["source_file"] = str(source_path)

    output_dir, resolved_source = harness._auto_poni_output_dir_for_source(
        f"h5ref://{container_path}#/entry/technical/tech_evt_000001/det_primary/processed_signal"
    )

    assert resolved_source == source_path
    assert output_dir == tmp_path / "autopony"


def test_auto_poni_prepare_cleans_autopony_folder(tmp_path):
    harness = _Harness()
    harness._current_technical_output_folder = lambda: str(tmp_path)
    stale = tmp_path / "autopony" / "stale.poni"
    stale.parent.mkdir()
    stale.write_text("old", encoding="utf-8")
    agbh_path = tmp_path / "AgBH_001_PRIMARY.npy"
    np.save(agbh_path, np.ones((8, 8), dtype=np.float32))

    prepared = harness._prepare_auto_poni_reviews(
        normalized_auto_poni_config({}),
        sources={"PRIMARY": str(agbh_path)},
        distance_cm_by_alias={"PRIMARY": 2.0},
        first_visible_ring_by_alias={"PRIMARY": 1},
    )

    assert prepared
    assert not stale.exists()
    assert sorted(path.name for path in (tmp_path / "autopony").iterdir()) == [
        "PRIMARY.npt",
        "PRIMARY.poni",
        "PRIMARY_pyfai.tif",
    ]


def test_auto_poni_validate_noops_when_active_container_locked(tmp_path, monkeypatch):
    warnings = []
    fake_tm = SimpleNamespace(
        QMessageBox=SimpleNamespace(
            warning=lambda _parent, _title, message: warnings.append(str(message))
        )
    )
    monkeypatch.setattr(
        "difra.gui.main_window_ext.technical.capture_mixin._tm",
        lambda: fake_tm,
    )

    class _SyncHarness(_Harness):
        def __init__(self):
            super().__init__()
            self.synced = 0
            self.created = []
            self._active_technical_container_path = str(tmp_path / "locked.nxs.h5")

        def _create_new_active_technical_container(self, *, clear_table=False):
            new_path = tmp_path / "unlocked.nxs.h5"
            with h5py.File(new_path, "w"):
                pass
            self._active_technical_container_path = str(new_path)
            self.created.append(bool(clear_table))
            return new_path

        def _sync_active_technical_container_from_table(self, show_errors=False):
            self.synced += 1
            return True

    harness = _SyncHarness()
    with h5py.File(harness._active_technical_container_path_obj(), "w") as h5f:
        h5f.attrs["locked"] = True
    agbh_path = tmp_path / "AgBH_001_PRIMARY.npy"
    np.save(agbh_path, np.ones((8, 8), dtype=np.float32))
    review = PyfaiCalib2Review(
        image_path=tmp_path / "autopony" / "AgBH_001_PRIMARY_pyfai.tif",
        poni_path=tmp_path / "autopony" / "generated.poni",
        command=[],
        poni_text="Distance: 0.02\n",
        source_path=agbh_path,
    )

    assert not harness._validate_auto_poni_reviews({"PRIMARY": review})
    assert harness._active_technical_container_path_obj().name == "locked.nxs.h5"
    assert harness.created == []
    assert harness.synced == 0
    assert not (tmp_path / "AgBH_001_PRIMARY.poni").exists()
    assert warnings == [
        "Active technical container is locked. PONI files cannot be updated in this container."
    ]


def test_auto_poni_validate_moves_poni_and_syncs_unlocked_container(tmp_path):
    class _SyncHarness(_Harness):
        def __init__(self):
            super().__init__()
            self.synced = 0
            self._active_technical_container_path = str(tmp_path / "technical.nxs.h5")

        def _sync_active_technical_container_from_table(self, show_errors=False):
            self.synced += 1
            return True

    harness = _SyncHarness()
    with h5py.File(harness._active_technical_container_path_obj(), "w"):
        pass
    agbh_path = tmp_path / "AgBH_001_PRIMARY.npy"
    np.save(agbh_path, np.ones((8, 8), dtype=np.float32))
    review = PyfaiCalib2Review(
        image_path=tmp_path / "autopony" / "AgBH_001_PRIMARY_pyfai.tif",
        poni_path=tmp_path / "autopony" / "generated.poni",
        command=[],
        poni_text="Distance: 0.02\n",
        source_path=agbh_path,
    )

    assert harness._validate_auto_poni_reviews({"PRIMARY": review})

    target = tmp_path / "AgBH_001_PRIMARY.poni"
    assert target.read_text(encoding="utf-8") == "Distance: 0.02\n"
    assert harness.poni_files["PRIMARY"]["path"] == str(target)
    assert harness.synced == 1


def test_auto_poni_validate_runs_poni_review_after_sync(tmp_path):
    class _ReviewHarness(_Harness):
        STATE_PENDING_PONI_REVIEW = "pending_poni_review"

        def __init__(self):
            super().__init__()
            self.synced = 0
            self.state_calls = []
            self.review_calls = []
            self.sync_state_calls = []
            self._active_technical_container_path = str(tmp_path / "technical.nxs.h5")

        def _sync_active_technical_container_from_table(self, show_errors=False):
            self.synced += 1
            return True

        def _set_container_state(self, path, *, state, reason):
            self.state_calls.append((Path(path), state, reason))

        def _run_poni_center_review_workflow(
            self,
            container_path,
            *,
            container_id,
            prompt_reload_on_reject=True,
        ):
            self.review_calls.append(
                (Path(container_path), container_id, bool(prompt_reload_on_reject))
            )
            return True

        def _sync_container_state(self, path, *, reason):
            self.sync_state_calls.append((Path(path), reason))

    harness = _ReviewHarness()
    with h5py.File(harness._active_technical_container_path_obj(), "w"):
        pass
    agbh_path = tmp_path / "AgBH_001_PRIMARY.npy"
    np.save(agbh_path, np.ones((8, 8), dtype=np.float32))
    review = PyfaiCalib2Review(
        image_path=tmp_path / "autopony" / "AgBH_001_PRIMARY_pyfai.tif",
        poni_path=tmp_path / "autopony" / "generated.poni",
        command=[],
        poni_text="Distance: 0.02\n",
        source_path=agbh_path,
    )

    assert harness._validate_auto_poni_reviews({"PRIMARY": review})

    container_path = harness._active_technical_container_path_obj()
    assert harness.synced == 1
    assert harness.state_calls == [
        (container_path, "pending_poni_review", "auto_poni_synced_review_required")
    ]
    assert harness.review_calls == [
        (container_path, container_path.stem, False)
    ]
    assert harness.sync_state_calls == [
        (container_path, "auto_poni_review_completed")
    ]


def test_auto_poni_seed_center_uses_poni_validation_config():
    harness = _Harness()
    harness.config["detectors"][0]["size"] = {"width": 256, "height": 256}
    harness.config["detectors"][0]["pixel_size_um"] = [55, 55]
    harness.config["poni_center_validation"] = {
        "enabled": True,
        "defaults": {"row_target_px": 120},
        "detectors": {
            "PRIMARY": {
                "col_target_px": 42,
            },
        },
    }

    center = harness._auto_poni_center_px_for_alias(
        "PRIMARY",
        harness.config["detectors"][0],
    )

    assert center == (120.0, 42.0)


def test_auto_poni_seed_center_uses_off_detector_zone_edge():
    harness = _Harness()
    detector_config = dict(harness.config["detectors"][1])
    detector_config["size"] = {"width": 256, "height": 256}
    harness.config["poni_center_validation"] = {
        "enabled": True,
        "detectors": {
            "SECONDARY": {
                "row_target_px": 128,
                "col_gt_px": 256,
                "col_max_px": 320,
            },
        },
    }

    center = harness._auto_poni_center_px_for_alias(
        "SECONDARY",
        detector_config,
    )

    assert center == (128.0, 320.0)


def test_start_capture_passes_distance_aware_base_to_worker(monkeypatch):
    _FakeWorker.instances.clear()
    _patch_tm(monkeypatch)
    harness = _Harness(checked=False)

    harness._start_capture("AGBH")

    assert len(_FakeWorker.instances) == 1
    txt_filename_base = Path(_FakeWorker.instances[0].kwargs["txt_filename_base"]).name
    assert txt_filename_base.startswith("agbh_base_17cm_003_")
    assert txt_filename_base.endswith("_3.000000s_4frames")


def test_start_capture_does_not_enable_continuous_movement_for_non_agbh(monkeypatch):
    _FakeWorker.instances.clear()
    _patch_tm(monkeypatch)
    harness = _Harness(checked=True)

    harness._start_capture("DARK")

    assert len(_FakeWorker.instances) == 1
    worker = _FakeWorker.instances[0]
    assert worker.kwargs["enable_continuous_movement"] is False


def test_resolve_technical_measurement_poni_reads_from_active_container(tmp_path):
    harness = _Harness()
    container_path = tmp_path / "technical.h5"
    with h5py.File(container_path, "w") as h5f:
        entry = h5f.create_group("entry")
        technical = entry.create_group("technical")
        group = technical.create_group("poni")
        ds = group.create_dataset("poni_waxs", data=b"Distance: 0.17\nPoni1: 0.007\nPoni2: 0.008\n")
        ds.attrs["detector_alias"] = "WAXS"
    harness._active_technical_container_path = str(container_path)

    resolved = harness._resolve_technical_measurement_poni(alias="SECONDARY")

    assert resolved is not None
    assert "Distance:" in resolved


def test_resolve_technical_measurement_poni_prefers_detector_linked_ref(tmp_path):
    harness = _Harness()
    container_path = tmp_path / "technical_ref.h5"
    with h5py.File(container_path, "w") as h5f:
        entry = h5f.create_group("entry")
        technical = entry.create_group("technical")
        poni_group = technical.create_group("poni")
        wrong = poni_group.create_dataset("poni_primary", data=b"Distance: 9.99\n")
        wrong.attrs["detector_alias"] = "PRIMARY"
        linked = poni_group.create_dataset("poni_det_saxs", data=b"Distance: 0.17\nPoni1: 0.007\n")
        linked.attrs["detector_alias"] = "SAXS"
        linked.attrs["detector_id"] = "DET_SAXS"

        event = technical.create_group("tech_evt_001")
        detector_group = event.create_group("det_saxs")
        detector_group.attrs["detector_alias"] = "SAXS"
        detector_group.attrs["detector_id"] = "DET_SAXS"
        detector_group.attrs["poni_ref"] = "/entry/technical/poni/poni_det_saxs"
        detector_group.create_dataset("processed_signal", data=[[1.0, 2.0], [3.0, 4.0]])

    resolved = harness._resolve_technical_measurement_poni(
        alias="PRIMARY",
        source_ref=f"h5ref://{container_path}#/entry/technical/tech_evt_001/det_saxs/processed_signal",
    )

    assert resolved is not None
    assert "Distance: 0.17" in resolved
    assert "9.99" not in resolved


def test_resolve_technical_measurement_mask_uses_detector_alias_from_container_context(tmp_path):
    harness = _Harness()
    harness.masks = {"SAXS": "saxs-mask"}
    container_path = tmp_path / "technical_mask.h5"
    with h5py.File(container_path, "w") as h5f:
        entry = h5f.create_group("entry")
        technical = entry.create_group("technical")
        event = technical.create_group("tech_evt_001")
        detector_group = event.create_group("det_saxs")
        detector_group.attrs["detector_alias"] = "SAXS"
        detector_group.attrs["detector_id"] = "DET_SAXS"
        detector_group.create_dataset("processed_signal", data=[[1.0, 2.0], [3.0, 4.0]])

    resolved = harness._resolve_technical_measurement_mask(
        alias="PRIMARY",
        source_ref=f"h5ref://{container_path}#/entry/technical/tech_evt_001/det_saxs/processed_signal",
    )

    assert resolved == "saxs-mask"


def test_on_capture_done_appends_results_to_container_before_table_processing(tmp_path):
    harness = _CaptureDoneHarness()
    result_path = tmp_path / "agbh_001_20260213_120000_3.000000s_4frames_PRIMARY.npy"
    np.save(result_path, np.ones((4, 4), dtype=np.float32))
    harness._pending_aux_capture_metadata = {
        "integration_time_ms": 3000.0,
        "n_frames": 4,
    }

    harness._on_capture_done(
        True,
        {"PRIMARY": str(result_path)},
        "AGBH",
    )

    assert harness._aux_timer.stop_calls == 1
    assert harness._aux_status.text == ""
    assert harness._pending_aux_capture_metadata is None
    assert harness.append_calls == [
        {
            "result_files": {"PRIMARY": str(result_path)},
            "technical_type": "AGBH",
            "show_errors": True,
        }
    ]
    assert "MeasurementWorker" not in harness.technical_module_requests


def test_resolve_technical_measurement_poni_uses_container_canonical_technical_path(tmp_path):
    harness = _Harness()
    container_path = tmp_path / "technical_entry.h5"
    with h5py.File(container_path, "w") as h5f:
        entry = h5f.create_group("entry")
        technical = entry.create_group("technical")
        poni_group = technical.create_group("poni")
        canonical = poni_group.create_dataset("poni_primary", data=b"Distance: 0.172399\nPoni1: 0.00702\n")
        canonical.attrs["detector_alias"] = "PRIMARY"

        event = technical.create_group("tech_evt_000001")
        detector_group = event.create_group("det_primary")
        detector_group.attrs["detector_alias"] = "PRIMARY"
        detector_group.attrs["detector_id"] = "MiniPIX G08-W0299"
        detector_group.create_dataset("processed_signal", data=[[1.0, 2.0], [3.0, 4.0]])

    resolved = harness._resolve_technical_measurement_poni(
        alias="PRIMARY",
        source_ref=f"h5ref://{container_path}#/entry/technical/tech_evt_000001/det_primary/processed_signal",
    )

    assert resolved is not None
    assert "Distance: 0.172399" in resolved


def test_resolve_technical_measurement_poni_matches_raw_detector_role_aliases(tmp_path):
    harness = _Harness()
    container_path = tmp_path / "session_like.h5"
    with h5py.File(container_path, "w") as h5f:
        entry = h5f.create_group("entry")
        technical = entry.create_group("technical")
        poni_group = technical.create_group("poni")
        ds = poni_group.create_dataset(
            "poni_det_primary",
            data=b"Distance: 0.170001\nPoni1: 0.00701\n",
        )
        ds.attrs["detector_alias"] = "det_primary"
        ds.attrs["detector_id"] = "det_primary"

        event = technical.create_group("tech_evt_000001")
        detector_group = event.create_group("det_primary")
        detector_group.attrs["detector_alias"] = "det_primary"
        detector_group.attrs["detector_id"] = "det_primary"
        detector_group.create_dataset("processed_signal", data=[[1.0, 2.0], [3.0, 4.0]])

    resolved = harness._resolve_technical_measurement_poni(
        alias="PRIMARY",
        source_ref=f"h5ref://{container_path}#/entry/technical/tech_evt_000001/det_primary/processed_signal",
    )

    assert resolved is not None
    assert "Distance: 0.170001" in resolved
