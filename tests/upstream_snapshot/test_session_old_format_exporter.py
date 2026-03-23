"""Tests for exporting legacy old-format folders from session containers."""

import base64
import json
from pathlib import Path

import h5py
import numpy as np

from container.v0_2 import technical_container, writer as session_writer
from difra.gui.session_old_format_exporter import SessionOldFormatExporter


def _create_session_with_technical_and_measurement(
    tmp_path: Path,
    *,
    technical_value: float = 1.0,
    technical_timestamp: str = "2026-02-24 10:00:00",
    sample_id: str = "SAMPLE_OLD_FMT",
    tag: str = "a",
) -> Path:
    technical_dir = tmp_path / f"technical_{tag}"
    measurements_dir = tmp_path / f"measurements_{tag}"

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
        poni_data={"PRIMARY": ("Distance: 0.17\nPoni1: 0.001\nPoni2: 0.002\n", "primary.poni")},
        distances_cm={"PRIMARY": 17.0},
        detector_id_by_alias={"PRIMARY": "DET-PRIMARY"},
    )
    technical_container.add_technical_event(
        file_path=tech_path,
        event_index=1,
        technical_type="AGBH",
        measurements={
            "PRIMARY": {
                "data": np.full((4, 4), technical_value, dtype=np.float32),
                "detector_id": "DET-PRIMARY",
                "integration_time_ms": 5000.0,
            }
        },
        timestamp=technical_timestamp,
        distances_cm={"PRIMARY": 17.0},
    )

    _sid, session_path = session_writer.create_session_container(
        folder=measurements_dir,
        sample_id=sample_id,
        study_name="STUDY_OLD_FMT",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-24",
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

    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["meta_json"] = json.dumps({"from_state_file": True})

    return Path(session_path)


def test_export_session_to_old_format_creates_expected_layout(tmp_path):
    session_path = _create_session_with_technical_and_measurement(tmp_path, tag="layout")
    old_format_root = tmp_path / "Data" / "difra" / "Old_format"

    summary = SessionOldFormatExporter.export_from_session_container(
        session_path,
        config={"old_format_export_folder": str(old_format_root)},
    )

    assert summary.export_dir.exists() is True
    assert summary.export_dir.parent == old_format_root
    assert summary.state_path.exists() is True
    assert summary.raw_file_count >= 3  # npy + txt + dsc
    assert summary.technical_file_count >= 2  # poni + technical event data

    state = json.loads(summary.state_path.read_text(encoding="utf-8"))
    assert state.get("sample_id") == "SAMPLE_OLD_FMT"
    assert state.get("measurements_meta")
    assert all(str(name).endswith(".txt") for name in state.get("measurements_meta", {}).keys())

    sample_files = {path.name for path in summary.state_path.parent.iterdir() if path.is_file()}
    assert any(name.endswith(".npy") for name in sample_files)
    assert any(name.endswith(".txt") for name in sample_files)
    assert any(name.endswith(".dsc") for name in sample_files)

    calibration_root = summary.export_dir / "calibration background"
    assert calibration_root.exists() is True
    distance_dirs = sorted(path for path in calibration_root.iterdir() if path.is_dir())
    assert len(distance_dirs) == 1
    technical_dir = distance_dirs[0]
    tech_files = {path.name for path in technical_dir.iterdir() if path.is_file()}
    assert any(name.endswith(".poni") for name in tech_files)
    assert any(name.endswith(".npy") for name in tech_files)
    assert any(name.startswith("technical_meta_") and name.endswith(".json") for name in tech_files)


def test_export_session_to_old_format_copies_jpg_from_state_image_path(tmp_path):
    session_path = _create_session_with_technical_and_measurement(tmp_path, tag="jpg_copy")
    old_format_root = tmp_path / "Data" / "difra" / "Old_format"

    source_jpg = tmp_path / "source_state_image.jpg"
    jpg_bytes = b"\xff\xd8\xff\xd9"
    source_jpg.write_bytes(jpg_bytes)

    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["meta_json"] = json.dumps(
            {
                "from_state_file": True,
                "image": str(source_jpg),
            }
        )

    summary = SessionOldFormatExporter.export_from_session_container(
        session_path,
        config={"old_format_export_folder": str(old_format_root)},
    )

    state = json.loads(summary.state_path.read_text(encoding="utf-8"))
    exported_image = Path(state["image"])
    assert exported_image.exists() is True
    assert exported_image.parent == summary.state_path.parent
    assert exported_image.suffix.lower() == ".jpg"
    assert exported_image.read_bytes() == jpg_bytes
    assert state.get("image_base64") == base64.b64encode(jpg_bytes).decode("ascii")


def test_export_session_to_old_format_restores_jpg_from_base64_when_path_missing(tmp_path):
    session_path = _create_session_with_technical_and_measurement(tmp_path, tag="jpg_b64")
    old_format_root = tmp_path / "Data" / "difra" / "Old_format"

    jpg_bytes = b"\xff\xd8\xff\xdb\xd9"
    jpg_b64 = base64.b64encode(jpg_bytes).decode("ascii")

    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["meta_json"] = json.dumps(
            {
                "from_state_file": True,
                "image": str(tmp_path / "missing.jpg"),
                "image_base64": jpg_b64,
            }
        )

    summary = SessionOldFormatExporter.export_from_session_container(
        session_path,
        config={"old_format_export_folder": str(old_format_root)},
    )

    state = json.loads(summary.state_path.read_text(encoding="utf-8"))
    exported_image = Path(state["image"])
    assert exported_image.exists() is True
    assert exported_image.parent == summary.state_path.parent
    assert exported_image.suffix.lower() == ".jpg"
    assert exported_image.read_bytes() == jpg_bytes
    assert state.get("image_base64") == base64.b64encode(jpg_bytes).decode("ascii")


def test_export_session_to_old_format_restores_jpg_from_container_image_dataset(tmp_path):
    session_path = _create_session_with_technical_and_measurement(tmp_path, tag="jpg_h5")
    old_format_root = tmp_path / "Data" / "difra" / "Old_format"

    image_array = np.full((16, 16), 123, dtype=np.uint8)
    session_writer.add_image(
        file_path=session_path,
        image_index=1,
        image_data=image_array,
        image_type="sample",
    )
    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["meta_json"] = json.dumps({"from_state_file": True})

    summary = SessionOldFormatExporter.export_from_session_container(
        session_path,
        config={"old_format_export_folder": str(old_format_root)},
    )

    state = json.loads(summary.state_path.read_text(encoding="utf-8"))
    exported_image = Path(state["image"])
    assert exported_image.exists() is True
    assert exported_image.parent == summary.state_path.parent
    payload = exported_image.read_bytes()
    assert payload[:2] == b"\xff\xd8"
    assert payload[-2:] == b"\xff\xd9"
    assert state.get("image_base64") == base64.b64encode(payload).decode("ascii")


def test_export_session_to_old_format_reuses_existing_distance_folder_when_payload_matches(tmp_path):
    session_path = _create_session_with_technical_and_measurement(tmp_path, tag="reuse")
    old_format_root = tmp_path / "Data" / "difra" / "Old_format"

    summary_first = SessionOldFormatExporter.export_from_session_container(
        session_path,
        config={"old_format_export_folder": str(old_format_root)},
    )

    calibration_root = summary_first.export_dir / "calibration background"
    first_files = {
        path.name
        for path in (calibration_root / "17cm").iterdir()
        if path.is_file() and not path.name.startswith("technical_meta_")
    }

    summary_second = SessionOldFormatExporter.export_from_session_container(
        session_path,
        config={"old_format_export_folder": str(old_format_root)},
    )

    calibration_root_second = summary_second.export_dir / "calibration background"
    distance_dirs = sorted(path.name for path in calibration_root_second.iterdir() if path.is_dir())
    assert distance_dirs == ["17cm"]
    second_files = {
        path.name
        for path in (calibration_root_second / "17cm").iterdir()
        if path.is_file() and not path.name.startswith("technical_meta_")
    }
    assert second_files == first_files


def test_export_session_to_old_format_creates_new_distance_folder_when_payload_changes(tmp_path):
    first_session = _create_session_with_technical_and_measurement(
        tmp_path,
        technical_value=1.0,
        technical_timestamp="2026-02-24 10:00:00",
        tag="first",
    )
    second_session = _create_session_with_technical_and_measurement(
        tmp_path,
        technical_value=2.0,
        technical_timestamp="2026-02-24 11:00:00",
        tag="second",
    )
    old_format_root = tmp_path / "Data" / "difra" / "Old_format"

    SessionOldFormatExporter.export_from_session_container(
        first_session,
        config={"old_format_export_folder": str(old_format_root)},
    )
    summary_second = SessionOldFormatExporter.export_from_session_container(
        second_session,
        config={"old_format_export_folder": str(old_format_root)},
    )

    calibration_root = summary_second.export_dir / "calibration background"
    distance_dirs = sorted(path.name for path in calibration_root.iterdir() if path.is_dir())
    assert distance_dirs == ["17cm", "18cm"]
