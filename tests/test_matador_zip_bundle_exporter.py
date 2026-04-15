from __future__ import annotations

import base64
import json
from pathlib import Path

import h5py
import numpy as np

from container.v0_2 import technical_container, writer as session_writer
from difra.gui.matador_zip_bundle_exporter import MatadorZipBundleExporter


def _create_session_with_measurements_and_attenuation(tmp_path: Path) -> Path:
    technical_dir = tmp_path / "technical"
    measurements_dir = tmp_path / "measurements"

    _tech_id, tech_path = technical_container.create_technical_container(
        folder=technical_dir,
        distance_cm=17.0,
    )
    technical_container.write_detector_config(
        file_path=tech_path,
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
        file_path=tech_path,
        poni_data={
            "PRIMARY": (
                "Distance: 0.17\nPoni1: 0.001\nPoni2: 0.002\nWavelength: 1.5406e-10\n",
                "primary.poni",
            )
        },
        distances_cm={"PRIMARY": 17.0},
        detector_id_by_alias={"PRIMARY": "DET-PRIMARY"},
    )
    technical_container.add_technical_event(
        file_path=tech_path,
        event_index=1,
        technical_type="AGBH",
        measurements={
            "PRIMARY": {
                "data": np.full((4, 4), 1.0, dtype=np.float32),
                "detector_id": "DET-PRIMARY",
                "integration_time_ms": 5000.0,
            }
        },
        timestamp="2026-03-31 10:00:00",
        distances_cm={"PRIMARY": 17.0},
    )

    _sid, session_path = session_writer.create_session_container(
        folder=measurements_dir,
        sample_id="378897__377557",
        study_name="STUDY_MATADOR",
        operator_id="339001",
        site_id="ULSTER",
        machine_name="XENA",
        beam_energy_keV=17.5,
        acquisition_date="2026-03-31",
        patient_id="377557",
    )
    session_writer.copy_technical_to_session(tech_path, session_path)

    session_writer.add_point(
        file_path=session_path,
        point_index=1,
        pixel_coordinates=[123.0, 456.0],
        physical_coordinates_mm=[1.25, 2.5],
    )
    session_writer.add_measurement(
        file_path=session_path,
        point_index=1,
        measurement_data={"DET-PRIMARY": np.arange(16).reshape(4, 4).astype(np.float32)},
        detector_metadata={"DET-PRIMARY": {"integration_time_ms": 5000.0}},
        poni_alias_map={"PRIMARY": "DET-PRIMARY"},
        raw_files={
            "DET-PRIMARY": {
                "raw_txt": b"1 2 3\n4 5 6\n",
                "raw_dsc": b"[F0]\nType=i16\n",
            }
        },
    )

    i0_signal = np.full((4, 4), 7, dtype=np.float32)
    i_signal = np.full((4, 4), 3, dtype=np.float32)
    i0_path = session_writer.add_analytical_measurement(
        file_path=session_path,
        measurement_data={"DET-PRIMARY": i0_signal},
        detector_metadata={
            "DET-PRIMARY": {
                "detector_id": "DET-PRIMARY",
                "integration_time_ms": 5000.0,
            }
        },
        poni_alias_map={"PRIMARY": "DET-PRIMARY"},
        analysis_type="attenuation",
        analysis_role="i0",
        timestamp_start="2026-03-31 10:10:00",
    )
    i_path = session_writer.add_analytical_measurement(
        file_path=session_path,
        measurement_data={"DET-PRIMARY": i_signal},
        detector_metadata={
            "DET-PRIMARY": {
                "detector_id": "DET-PRIMARY",
                "integration_time_ms": 5000.0,
                "point_position_mm": [1.25, 2.5],
            }
        },
        poni_alias_map={"PRIMARY": "DET-PRIMARY"},
        analysis_type="attenuation",
        analysis_role="i",
        timestamp_start="2026-03-31 10:11:00",
    )
    session_writer.link_analytical_measurement_to_point(
        file_path=session_path,
        point_index=1,
        analytical_measurement_index=int(str(i0_path).split("_")[-1]),
    )
    session_writer.link_analytical_measurement_to_point(
        file_path=session_path,
        point_index=1,
        analytical_measurement_index=int(str(i_path).split("_")[-1]),
    )

    image_bytes = b"\xff\xd8\xff\xd9"
    image_path = tmp_path / "absolute_state_image.jpg"
    image_path.write_bytes(image_bytes)

    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["matadorStudyId"] = 377501
        h5f.attrs["matadorMachineId"] = 1251
        h5f.attrs["meta_json"] = json.dumps(
            {
                "measurement_points": [
                    {
                        "unique_id": "7ccbcf0e1c85fa4c",
                        "index": 0,
                        "point_index": 1,
                        "x": 1.25,
                        "y": 2.5,
                    }
                ],
                "image": str(image_path),
                "dock_geometry": "deadbeef",
                "technical_aux": [
                    {
                        "type": "AGBH",
                        "alias": "PRIMARY",
                        "file_path": str(tmp_path / "technical.npy"),
                    }
                ],
                "rotation_angle": 5,
            }
        )

    return Path(session_path)


def _create_session_with_duplicate_measurement_name_risk(tmp_path: Path) -> Path:
    technical_dir = tmp_path / "technical_dup"
    measurements_dir = tmp_path / "measurements_dup"

    _tech_id, tech_path = technical_container.create_technical_container(
        folder=technical_dir,
        distance_cm=17.0,
    )
    technical_container.write_detector_config(
        file_path=tech_path,
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

    _sid, session_path = session_writer.create_session_container(
        folder=measurements_dir,
        sample_id="378897__377557",
        study_name="STUDY_MATADOR",
        operator_id="339001",
        site_id="ULSTER",
        machine_name="XENA",
        beam_energy_keV=17.5,
        acquisition_date="2026-03-31",
        patient_id="377557",
    )
    session_writer.copy_technical_to_session(tech_path, session_path)

    session_writer.add_point(
        file_path=session_path,
        point_index=1,
        pixel_coordinates=[123.0, 456.0],
        physical_coordinates_mm=[1.25, 2.5],
    )
    shared_timestamps = {
        "timestamp_start": "2026-03-31 10:00:00",
        "timestamp_end": "2026-03-31 10:00:00",
    }
    session_writer.add_measurement(
        file_path=session_path,
        point_index=1,
        measurement_data={"DET-PRIMARY": np.arange(16).reshape(4, 4).astype(np.float32)},
        detector_metadata={"DET-PRIMARY": {"integration_time_ms": 5000.0}},
        poni_alias_map={"PRIMARY": "DET-PRIMARY"},
        raw_files={
            "DET-PRIMARY": {
                "raw_txt": b"1 2 3\n4 5 6\n",
                "raw_dsc": b"[F0]\nType=i16\n",
            }
        },
        **shared_timestamps,
    )
    session_writer.add_measurement(
        file_path=session_path,
        point_index=1,
        measurement_data={"DET-PRIMARY": np.full((4, 4), 9.0, dtype=np.float32)},
        detector_metadata={"DET-PRIMARY": {"integration_time_ms": 5000.0}},
        poni_alias_map={"PRIMARY": "DET-PRIMARY"},
        raw_files={
            "DET-PRIMARY": {
                "raw_txt": b"7 8 9\n10 11 12\n",
                "raw_dsc": b"[F0]\nType=i16\n",
            }
        },
        **shared_timestamps,
    )

    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["matadorStudyId"] = 377501
        h5f.attrs["matadorMachineId"] = 1251
        h5f.attrs["meta_json"] = json.dumps({})

    return Path(session_path)


def test_export_bundle_creates_flat_payload_and_keeps_attenuation(tmp_path: Path):
    session_path = _create_session_with_measurements_and_attenuation(tmp_path)

    summary = MatadorZipBundleExporter.export_from_session_container(
        session_path,
        target_root=tmp_path / "bundle_root",
    )

    files = sorted(path.name for path in summary.export_dir.iterdir() if path.is_file())

    assert "metadata.json" in files
    assert "measurementData.json" in files
    assert any(name.endswith("_state.json") for name in files)
    assert any(name.endswith(".npy") for name in files)
    assert any(name.endswith(".txt") for name in files)
    assert any(name.endswith(".txt.dsc") for name in files)
    assert any("ATTENUATION" in name for name in files)
    assert not any(path.is_dir() for path in summary.export_dir.iterdir())

    state = json.loads(summary.state_path.read_text(encoding="utf-8"))
    assert "image" not in state
    assert state["image_base64"] == base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii")
    assert "dock_geometry" not in state
    assert state["rotation_angle"] == 5
    assert state["attenuation_files"]
    assert "poni_path" not in state["detector_poni"]["PRIMARY"]
    assert "file_path" not in state["technical_aux"][0]
    attenuation_file = state["attenuation_files"]["7ccbcf0e1c85fa4c"]["without_sample"]["PRIMARY"]
    assert attenuation_file.endswith(".npy")
    assert "/" not in attenuation_file

    manifest = json.loads(summary.metadata_path.read_text(encoding="utf-8"))
    assert manifest["fileCount"] == len(manifest["fileNames"])
    assert len(set(manifest["fileNames"])) == len(manifest["fileNames"])
    assert "metadata.json" in manifest["fileNames"]
    assert "measurementData.json" in manifest["fileNames"]
    assert summary.state_path.name in manifest["fileNames"]
    assert all("/" not in name for name in manifest["fileNames"])

    measurement_data = json.loads(summary.measurement_data_path.read_text(encoding="utf-8"))
    assert measurement_data["distanceInMM"] == 170
    assert measurement_data["study"]["id"] == 377501
    assert measurement_data["machineMeasur"]["id"] == 1251
    assert measurement_data["patient"]["id"] == 377557
    assert measurement_data["specimen"]["id"] == 378897


def test_export_bundle_keeps_measurement_filenames_unique_when_point_and_time_match(tmp_path: Path):
    session_path = _create_session_with_duplicate_measurement_name_risk(tmp_path)

    summary = MatadorZipBundleExporter.export_from_session_container(
        session_path,
        target_root=tmp_path / "bundle_root_dup",
    )

    txt_files = sorted(path.name for path in summary.export_dir.glob("*.txt"))
    npy_files = sorted(path.name for path in summary.export_dir.glob("*.npy"))

    assert len(txt_files) == 2
    assert len(set(txt_files)) == 2
    assert len(npy_files) == 2
    assert len(set(npy_files)) == 2
    assert any("_meas_" in name for name in txt_files)
