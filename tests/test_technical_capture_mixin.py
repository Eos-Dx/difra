from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np

from difra.gui.main_window_ext.technical.capture_mixin import TechnicalCaptureMixin


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
        self.continuous_movement_controller = None
        self._capture_workers = []
        self.logged_events = []

    def _technical_imports_available(self):
        return True

    def _log_technical_event(self, message):
        self.logged_events.append(str(message))

    def _current_technical_output_folder(self):
        return "/tmp"

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
