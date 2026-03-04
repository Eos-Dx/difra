from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import difra.gui.main_window_ext.zone_measurements.logic.process_capture_mixin as capture_module
from difra.gui.main_window_ext.zone_measurements.logic.process_capture_mixin import (
    ZoneMeasurementsProcessCaptureMixin,
    _place_raw_capture_file,
)


class _StubStageController:
    def __init__(self):
        self.calls = []

    def get_home_load_positions(self):
        self.calls.append("get_home_load_positions")
        return {"load": (11.0, -5.5)}


def test_place_raw_capture_file_noops_when_source_matches_target_and_copies_dsc(tmp_path: Path):
    target_txt = tmp_path / "capture.txt"
    target_dsc = tmp_path / "capture.dsc"
    target_txt.write_text("txt", encoding="utf-8")
    target_dsc.write_text("dsc", encoding="utf-8")

    _place_raw_capture_file(str(target_txt), tmp_path / "capture.txt", allow_move=True)

    assert target_txt.read_text(encoding="utf-8") == "txt"
    assert target_dsc.read_text(encoding="utf-8") == "dsc"


def test_place_raw_capture_file_moves_or_copies_files(tmp_path: Path):
    src_txt = tmp_path / "source.txt"
    src_dsc = tmp_path / "source.dsc"
    src_txt.write_text("txt", encoding="utf-8")
    src_dsc.write_text("dsc", encoding="utf-8")
    target_txt = tmp_path / "nested" / "target.txt"
    target_dsc = target_txt.with_suffix(".dsc")

    _place_raw_capture_file(str(src_txt), target_txt, allow_move=False)

    assert src_txt.exists()
    assert src_dsc.exists()
    assert target_txt.read_text(encoding="utf-8") == "txt"
    assert target_dsc.read_text(encoding="utf-8") == "dsc"


def test_current_session_point_index_uses_mapping_and_fallback():
    owner = SimpleNamespace(
        current_measurement_sorted_index=1,
        _session_point_indices=[10, 20, 30],
    )

    assert ZoneMeasurementsProcessCaptureMixin._current_session_point_index(owner) == 20

    owner = SimpleNamespace(current_measurement_sorted_index="bad", _session_point_indices=None)
    assert ZoneMeasurementsProcessCaptureMixin._current_session_point_index(owner) == 1


def test_get_loading_position_prefers_config_and_falls_back_to_stage_controller():
    owner = SimpleNamespace(
        config={"attenuation": {"loading_position": {"x": "3.5", "y": "-2.0"}}},
        stage_controller=_StubStageController(),
    )

    assert ZoneMeasurementsProcessCaptureMixin._get_loading_position(owner) == (3.5, -2.0)

    owner = SimpleNamespace(
        config={},
        stage_controller=_StubStageController(),
    )
    assert ZoneMeasurementsProcessCaptureMixin._get_loading_position(owner) == (11.0, -5.5)
    assert owner.stage_controller.calls == ["get_home_load_positions"]


def test_get_loading_position_returns_none_pair_when_unavailable():
    owner = SimpleNamespace(config={}, stage_controller=None)

    assert ZoneMeasurementsProcessCaptureMixin._get_loading_position(owner) == (None, None)


def test_record_attenuation_files_stores_files_for_current_measurement_and_dumps():
    dumped = []
    owner = SimpleNamespace(
        state_measurements={
            "measurement_points": [{"unique_id": "1_deadbeef"}],
        },
        current_measurement_sorted_index=0,
        state_path_measurements="state.json",
        _dump_state_measurements=lambda: dumped.append(True),
    )

    ZoneMeasurementsProcessCaptureMixin._record_attenuation_files(
        owner,
        "without_sample",
        {"det_a": "/tmp/a.npy"},
    )

    assert dumped == [True]
    assert owner.state_measurements["attenuation_files"] == {
        "1_deadbeef": {"without_sample": {"det_a": "/tmp/a.npy"}}
    }


def test_record_attenuation_files_noops_without_uid(tmp_path: Path):
    path = tmp_path / "state.json"
    owner = SimpleNamespace(
        state_measurements={"measurement_points": []},
        current_measurement_sorted_index=3,
        state_path_measurements=str(path),
    )

    ZoneMeasurementsProcessCaptureMixin._record_attenuation_files(
        owner,
        "with_sample",
        {"det_a": "/tmp/a.npy"},
    )

    assert not path.exists()


def test_record_attenuation_files_writes_json_when_dump_helper_is_missing(tmp_path: Path):
    path = tmp_path / "state.json"
    owner = SimpleNamespace(
        state_measurements={
            "measurement_points": [{"unique_id": "2_deadbeef"}],
        },
        current_measurement_sorted_index=0,
        state_path_measurements=str(path),
    )

    ZoneMeasurementsProcessCaptureMixin._record_attenuation_files(
        owner,
        "with_sample",
        {"det_b": "/tmp/b.npy"},
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["attenuation_files"] == {
        "2_deadbeef": {"with_sample": {"det_b": "/tmp/b.npy"}}
    }


def test_capture_attenuation_background_skips_when_loading_position_missing(monkeypatch):
    logs = []
    owner = SimpleNamespace(
        config={},
        detector_controller={},
        _get_loading_position=lambda: (None, None),
        _append_capture_log=lambda message: logs.append(message),
        fileNameLineEdit=SimpleNamespace(text=lambda: "sample"),
        measurement_folder="/tmp",
    )
    logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(capture_module, "_pm", lambda: SimpleNamespace(logger=logger))

    ZoneMeasurementsProcessCaptureMixin._capture_attenuation_background(owner)

    assert owner._attenuation_bg_files is None
    assert logs[-1] == "I0 skipped: loading position is not configured"
