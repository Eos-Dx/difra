from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
from container.v0_2 import technical_container, writer as session_writer

from difra.gui.session_technical_rewrite_service import (
    SessionTechnicalRewriteService,
)


def _create_technical(
    tmp_path: Path,
    *,
    container_id: str,
    distance_cm: float,
    folder_name: str = "technical",
) -> Path:
    _id, path = technical_container.create_technical_container(
        folder=tmp_path / folder_name,
        distance_cm=distance_cm,
        container_id=container_id,
    )
    technical_container.write_detector_config(
        file_path=path,
        detectors_config=[
            {
                "id": "DET-PRIMARY",
                "alias": "PRIMARY",
                "type": "Pixet",
                "size": {"width": 4, "height": 4},
                "pixel_size_um": [55.0, 55.0],
            }
        ],
        active_detector_ids=["DET-PRIMARY"],
    )
    technical_container.write_poni_datasets(
        file_path=path,
        poni_data={
            "PRIMARY": (
                f"Distance: {distance_cm / 100.0}\nPoni1: 0.001\nPoni2: 0.002\n",
                "primary.poni",
            )
        },
        distances_cm={"PRIMARY": distance_cm},
        detector_id_by_alias={"PRIMARY": "DET-PRIMARY"},
    )
    technical_container.add_technical_event(
        file_path=path,
        event_index=1,
        technical_type="AGBH",
        measurements={
            "PRIMARY": {
                "data": np.full((4, 4), distance_cm, dtype=np.float32),
                "detector_id": "DET-PRIMARY",
            }
        },
        timestamp="2026-06-03 10:00:00",
        distances_cm={"PRIMARY": distance_cm},
    )
    return Path(path)


def _create_session(tmp_path: Path, technical_path: Path) -> Path:
    _id, path = session_writer.create_session_container(
        folder=tmp_path / "measurements",
        sample_id="378897__377557_P01",
        study_name="STUDY",
        operator_id="jennifer_nicell",
        site_id="ULSTER",
        machine_name="XENA",
        beam_energy_keV=8.04,
        acquisition_date="2026-06-03",
        patient_id="377557",
    )
    session_path = Path(path)
    session_writer.copy_technical_to_session(technical_path, session_path)
    session_writer.add_point(
        file_path=session_path,
        point_index=1,
        pixel_coordinates=[1.0, 2.0],
        physical_coordinates_mm=[3.0, 4.0],
    )
    session_writer.add_measurement(
        file_path=session_path,
        point_index=1,
        measurement_data={"DET-PRIMARY": np.ones((4, 4), dtype=np.float32)},
        detector_metadata={"DET-PRIMARY": {"integration_time_ms": 1000.0}},
        poni_alias_map={"PRIMARY": "DET-PRIMARY"},
    )
    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["transfer_status"] = "sent"
        h5f.attrs["meta_json"] = json.dumps(
            {
                "CALIBRATION_GROUP_HASH": "1111111111111111",
                "sample_id": "378897__377557_P01",
            }
        )
    return session_path


def test_rewrite_session_technical_section_backs_up_and_marks_req_resend(tmp_path: Path):
    old_technical = _create_technical(tmp_path, container_id="1111111111111111", distance_cm=17.0)
    new_technical = _create_technical(tmp_path, container_id="2222222222222222", distance_cm=2.0)
    session_path = _create_session(tmp_path, old_technical)
    sidecar = session_path.with_name(f"{session_path.stem}_state.json")
    sidecar.write_text(json.dumps({"CALIBRATION_GROUP_HASH": "1111111111111111"}), encoding="utf-8")

    result = SessionTechnicalRewriteService().rewrite_session_technical_section(
        session_path=session_path,
        technical_path=new_technical,
        reason="validated corrected PONI",
    )

    assert result.backup_path.name.endswith(".h5old")
    assert result.backup_path.exists()
    assert result.technical_container_id == "2222222222222222"
    assert result.state_json_updated is True
    assert result.sidecar_state_json_updated is True

    with h5py.File(result.backup_path, "r") as backup_h5:
        assert backup_h5["/entry/technical"].attrs["source_container_id"] == "1111111111111111"
        assert "/entry/measurements" in backup_h5

    with h5py.File(session_path, "r") as h5f:
        assert h5f.attrs["transfer_status"] == "req_resend"
        assert h5f.attrs["technical_container_id"] == "2222222222222222"
        assert h5f["/entry/technical"].attrs["source_container_id"] == "2222222222222222"
        assert "/entry/measurements" in h5f
        state = json.loads(h5f.attrs["meta_json"])
        assert state["CALIBRATION_GROUP_HASH"] == "2222222222222222"
        assert state["technical_rewrite_required_resend"] is True

    sidecar_state = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_state["CALIBRATION_GROUP_HASH"] == "2222222222222222"


def test_rewrite_session_technical_section_uses_h5old2_when_backup_exists(tmp_path: Path):
    old_technical = _create_technical(tmp_path, container_id="1111111111111111", distance_cm=17.0)
    new_technical = _create_technical(tmp_path, container_id="2222222222222222", distance_cm=2.0)
    session_path = _create_session(tmp_path, old_technical)
    session_path.with_name(session_path.name[:-3] + ".h5old").write_bytes(b"existing")

    result = SessionTechnicalRewriteService().rewrite_session_technical_section(
        session_path=session_path,
        technical_path=new_technical,
    )

    assert result.backup_path.name.endswith(".h5old2")
    assert result.backup_path.exists()


def test_rewrite_sessions_by_technical_id_only_updates_matching_sessions(tmp_path: Path):
    old_technical = _create_technical(
        tmp_path,
        container_id="aaaaaaaaaaaaaaaa",
        distance_cm=17.0,
        folder_name="old_technical",
    )
    corrected_technical = _create_technical(
        tmp_path,
        container_id="aaaaaaaaaaaaaaaa",
        distance_cm=2.0,
        folder_name="corrected_technical",
    )
    unmatched_technical = _create_technical(
        tmp_path,
        container_id="bbbbbbbbbbbbbbbb",
        distance_cm=17.0,
        folder_name="unmatched_technical",
    )
    session_path = _create_session(tmp_path, old_technical)

    results = SessionTechnicalRewriteService().rewrite_sessions_by_technical_id(
        session_paths=[session_path],
        technical_paths=[unmatched_technical, corrected_technical],
        reason="batch repair",
    )

    assert len(results) == 1
    assert results[0].technical_container_id == "aaaaaaaaaaaaaaaa"
    with h5py.File(session_path, "r") as h5f:
        assert h5f.attrs["transfer_status"] == "req_resend"
        assert h5f["/entry/technical"].attrs["source_container_id"] == "aaaaaaaaaaaaaaaa"
        assert h5f["/entry/technical"].attrs["technical_rewrite_reason"] == "batch repair"
