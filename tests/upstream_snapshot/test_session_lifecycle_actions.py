"""Tests for high-level session lifecycle workflow actions."""

from pathlib import Path
from unittest.mock import patch

import h5py
import numpy as np
from container.v0_2 import container_manager, writer as session_writer
from difra.gui.matador_upload_api import RealMatadorUploadApi
from difra.gui.session_lifecycle_actions import SessionLifecycleActions


def _create_session_file(folder: Path, sample_id: str):
    session_id, session_path = session_writer.create_session_container(
        folder=folder,
        sample_id=sample_id,
        study_name="STUDY_A",
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-16",
    )
    return session_id, Path(session_path)


def _add_complete_session_payload(session_path: Path):
    session_writer.add_image(
        session_path,
        image_index=1,
        image_data=np.ones((8, 8), dtype=np.float32),
        image_type="sample",
    )
    session_writer.add_point(
        session_path,
        point_index=1,
        pixel_coordinates=[10.0, 12.0],
        physical_coordinates_mm=[1.0, 2.0],
    )
    session_writer.add_measurement(
        session_path,
        point_index=1,
        measurement_data={"PRIMARY": np.ones((4, 4), dtype=np.float32)},
        detector_metadata={"PRIMARY": {"integration_time_ms": 100.0}},
        poni_alias_map={"PRIMARY": "PRIMARY"},
    )


def test_finalize_session_container_locks_once(tmp_path):
    _sid, session_path = _create_session_file(tmp_path / "measurements", "SAMPLE_A")

    changed = SessionLifecycleActions.finalize_session_container(
        session_path=session_path,
        container_manager=container_manager,
        lock_user="sad",
    )
    assert changed is True
    assert container_manager.is_container_locked(session_path) is True
    assert container_manager.get_transfer_status(session_path) == "unsent"
    with h5py.File(session_path, "r") as h5f:
        assert h5f.attrs.get("session_state") == "locked"

    changed_again = SessionLifecycleActions.finalize_session_container(
        session_path=session_path,
        container_manager=container_manager,
        lock_user="sad",
    )
    assert changed_again is False


def test_matador_specimen_parser_uses_leading_db_specimen_id():
    assert SessionLifecycleActions._coerce_optional_int("326111__326169") == 326111
    assert SessionLifecycleActions._coerce_optional_int("326169") == 326169
    assert SessionLifecycleActions._coerce_optional_int("patient__not-a-number") is None


def test_read_matador_metadata_uses_leading_specimen_id_from_container(tmp_path):
    _sid, session_path = _create_session_file(tmp_path / "measurements", "326111__326169")

    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["specimenId"] = "326111__326169"

    metadata = SessionLifecycleActions._read_matador_session_metadata(session_path)

    assert metadata["specimen_text"] == "326111__326169"
    assert metadata["specimen_id"] == 326111


def test_read_matador_metadata_uses_acquisition_date_as_session_date(tmp_path):
    _sid, session_path = _create_session_file(tmp_path / "measurements", "326111__326169")

    metadata = SessionLifecycleActions._read_matador_session_metadata(session_path)

    assert metadata["session_date"] == "2026-02-16"


def test_execute_matador_upload_rejects_real_upload_without_numeric_specimen(tmp_path):
    _sid, session_path = _create_session_file(tmp_path / "measurements", "SAMPLE_A")
    zip_path = tmp_path / "payload.zip"
    zip_path.write_text("payload", encoding="utf-8")

    result = SessionLifecycleActions._execute_matador_upload(
        session_path,
        old_format_zip_path=zip_path,
        upload_api=RealMatadorUploadApi(
            base_url="https://portal.matur.co.uk",
            token="token-value",
        ),
        config={},
    )

    assert result.success is False
    assert "specimen ID is required" in result.message


def test_upload_payload_names_include_specimen_distance_and_session_ids(tmp_path):
    sid, session_path = _create_session_file(tmp_path / "measurements", "9907__9856")

    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["specimenId"] = "9907__9856"
        h5f.attrs["distance_cm"] = 17.0
        h5f.attrs["session_id"] = sid
        h5f.require_group("/entry/calibration_snapshot").attrs["source_container_id"] = "tech_abc123"

    names = SessionLifecycleActions._read_upload_payload_names(session_path)

    assert names["measurement_zip_name"] == f"measurement_9907__9856_17cm_{sid}.zip"
    assert names["calibration_zip_name"] == "calibration_17cm_tech_abc123.zip"


def test_upload_payload_names_accept_technical_snapshot_path_used_by_current_schema(tmp_path):
    sid, session_path = _create_session_file(tmp_path / "measurements", "9907__9856")

    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["specimenId"] = "9907__9856"
        h5f.attrs["distance_cm"] = 2.0
        h5f.attrs["session_id"] = sid
        h5f.require_group("/entry/technical").attrs["source_container_id"] = "0664bb96181e4206"

    names = SessionLifecycleActions._read_upload_payload_names(session_path)

    assert names["measurement_zip_name"] == f"measurement_9907__9856_2cm_{sid}.zip"
    assert names["calibration_zip_name"] == "calibration_2cm_0664bb96181e4206.zip"


def test_prepare_old_format_payload_uses_descriptive_zip_filenames(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    sid, session_path = _create_session_file(measurements, "9907__9856")
    _add_complete_session_payload(session_path)

    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["specimenId"] = "9907__9856"
        h5f.attrs["distance_cm"] = 17.0
        h5f.attrs["session_id"] = sid
        h5f.require_group("/entry/calibration_snapshot").attrs["source_container_id"] = "tech_abc123"

    _summary, _archived_export_dir, zip_path, calibration_zip_paths = (
        SessionLifecycleActions._prepare_old_format_payload(
            session_path,
            archive_folder=archive_folder,
            config={"old_format_export_folder": str(tmp_path / "old_format")},
        )
    )

    assert Path(zip_path).name == f"measurement_9907__9856_17cm_{sid}.zip"
    assert len(calibration_zip_paths) == 1
    assert Path(calibration_zip_paths[0]).name == "calibration_17cm_tech_abc123.zip"


def test_send_and_archive_session_containers_tracks_active_session(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    old_format_folder = tmp_path / "Data" / "difra" / "Old_format"
    sid_a, path_a = _create_session_file(measurements, "SAMPLE_A")
    sid_b, path_b = _create_session_file(measurements, "SAMPLE_B")

    result = SessionLifecycleActions.send_and_archive_session_containers(
        container_paths=[path_a, path_b],
        container_manager=container_manager,
        archive_folder=archive_folder,
        active_session_path=path_a,
        lock_user="sad",
        session_ids={str(path_a): sid_a, str(path_b): sid_b},
        config={
            "old_format_export_folder": str(old_format_folder),
            "enable_old_format_export": True,
        },
    )

    assert result.failed == []
    assert result.moved == 2
    assert result.archived_active_session is True
    assert result.upload_session_id.startswith("upload_sad_")
    assert result.upload_success == 2
    assert result.upload_failed == 0
    assert len(result.archived_paths) == 2
    assert all(path.exists() for path in result.archived_paths)
    assert result.old_format_failed == []
    assert len(result.old_format_paths) == 2
    assert all(path.exists() for path in result.old_format_paths)
    assert all(path.parent == old_format_folder for path in result.old_format_paths)
    assert path_a.exists() is False
    assert path_b.exists() is False
    parent_names = {p.parent.name for p in result.archived_paths}
    assert any(name.startswith(f"{sid_a}_") for name in parent_names)
    assert any(name.startswith(f"{sid_b}_") for name in parent_names)
    assert all(
        container_manager.get_transfer_status(path) == "sent"
        for path in result.archived_paths
    )
    for archived_path in result.archived_paths:
        with h5py.File(archived_path, "r") as h5f:
            assert h5f.attrs.get("uploaded_by") == "sad"
            assert str(h5f.attrs.get("upload_timestamp", "")).strip()
            assert str(h5f.attrs.get("upload_session_id", "")).startswith("upload_sad_")
            assert h5f.attrs.get("upload_status") == "success"
            assert h5f.attrs.get("matador_send_status") == "successful"
            assert str(h5f.attrs.get("matador_send_reason", "")) == ""
            assert str(h5f.attrs.get("matador_send_timestamp", "")).strip()
            assert h5f.attrs.get("session_state") == "archived"
            local_checksum = str(h5f.attrs.get("upload_local_checksum_sha256", ""))
            response_checksum = str(
                h5f.attrs.get("upload_response_checksum_sha256", "")
            )
            assert len(local_checksum) == 64
            assert response_checksum == local_checksum
            attempt_log = str(h5f.attrs.get("upload_attempts_log", ""))
            assert "operator=sad" in attempt_log
            assert "status=success" in attempt_log


def test_send_and_archive_cleans_measurement_artifacts(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    old_format_folder = tmp_path / "Data" / "difra" / "Old_format"
    sid_a, path_a = _create_session_file(measurements, "SAMPLE_A")

    (measurements / "capture.txt").write_text("txt")
    (measurements / "capture.dsc").write_text("dsc")
    (measurements / "capture.npy").write_text("npy")
    (measurements / "SAMPLE_A_state.json").write_text("{}")
    grpc_folder = measurements / "grpc_exposures"
    grpc_folder.mkdir(parents=True, exist_ok=True)
    (grpc_folder / "tmp.txt").write_text("tmp")

    result = SessionLifecycleActions.send_and_archive_session_containers(
        container_paths=[path_a],
        container_manager=container_manager,
        archive_folder=archive_folder,
        lock_user="sad",
        session_ids={str(path_a): sid_a},
        config={
            "old_format_export_folder": str(old_format_folder),
            "enable_old_format_export": True,
        },
    )

    assert result.failed == []
    assert result.moved == 1
    archived_folder = result.archived_paths[0].parent
    assert (archived_folder / "capture.txt").exists() is True
    assert (archived_folder / "capture.dsc").exists() is True
    assert (archived_folder / "capture.npy").exists() is True
    assert (archived_folder / "SAMPLE_A_state.json").exists() is True
    assert (archived_folder / "grpc_exposures" / "tmp.txt").exists() is True
    assert (measurements / "capture.txt").exists() is False
    assert (measurements / "capture.dsc").exists() is False
    assert (measurements / "capture.npy").exists() is False
    assert (measurements / "SAMPLE_A_state.json").exists() is False
    assert grpc_folder.exists() is False


def test_send_and_archive_exports_old_format_before_archive_move(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    sid_a, path_a = _create_session_file(measurements, "SAMPLE_A")

    call_order = []

    class _Summary:
        def __init__(self, export_dir: Path):
            self.export_dir = export_dir

    def _export_stub(session_path, **kwargs):
        call_order.append(("export", Path(session_path)))
        return _Summary(tmp_path / "old_format" / "mock")

    def _archive_stub(session_path, **kwargs):
        call_order.append(("archive", Path(session_path)))
        destination = (
            archive_folder
            / "mock_session_sad_SAMPLE_A_STUDY_A_20260225_100000"
            / Path(session_path).name
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        Path(session_path).rename(destination)
        return destination

    with patch(
        "difra.gui.session_lifecycle_actions.SessionOldFormatExporter.export_from_session_container",
        side_effect=_export_stub,
    ), patch(
        "difra.gui.session_lifecycle_actions.SessionLifecycleService.archive_session_container",
        side_effect=_archive_stub,
    ):
        result = SessionLifecycleActions.send_and_archive_session_containers(
            container_paths=[path_a],
            container_manager=container_manager,
            archive_folder=archive_folder,
            lock_user="sad",
            session_ids={str(path_a): sid_a},
            config={
                "old_format_export_folder": str(tmp_path / "old_format"),
                "enable_old_format_export": True,
            },
            export_old_format=True,
        )

    assert result.failed == []
    assert [item[0] for item in call_order] == ["export", "archive"]
    assert call_order[0][1] == path_a


def test_send_and_archive_upload_failure_marks_unsent(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    sid_a, path_a = _create_session_file(measurements, "SAMPLE_A")

    result = SessionLifecycleActions.send_and_archive_session_containers(
        container_paths=[path_a],
        container_manager=container_manager,
        archive_folder=archive_folder,
        lock_user="sad",
        session_ids={str(path_a): sid_a},
        simulate_upload_failure=True,
    )

    assert result.moved == 1
    assert result.upload_success == 0
    assert result.upload_failed == 1
    assert result.failed
    archived = result.archived_paths[0]
    assert container_manager.get_transfer_status(archived) == "unsent"
    with h5py.File(archived, "r") as h5f:
        assert h5f.attrs.get("upload_status") == "failed"
        assert h5f.attrs.get("matador_send_status") == "unsuccessful"
        assert "failed" in str(h5f.attrs.get("matador_send_reason", "")).lower()
        assert str(h5f.attrs.get("matador_send_timestamp", "")).strip()
        assert str(h5f.attrs.get("upload_result_message", "")).lower().find("failed") >= 0
        assert str(h5f.attrs.get("upload_response_checksum_sha256", "")) == ""
        assert "status=failed" in str(h5f.attrs.get("upload_attempts_log", ""))


def test_send_and_archive_composite_specimen_uses_leading_db_id(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    old_format_folder = tmp_path / "Data" / "difra" / "Old_format"
    sid_a, path_a = _create_session_file(measurements, "326111__326169")
    _add_complete_session_payload(path_a)

    result = SessionLifecycleActions.send_and_archive_session_containers(
        container_paths=[path_a],
        container_manager=container_manager,
        archive_folder=archive_folder,
        lock_user="sad",
        session_ids={str(path_a): sid_a},
        config={
            "old_format_export_folder": str(old_format_folder),
            "enable_old_format_export": True,
        },
    )

    assert result.moved == 1
    assert result.upload_success == 1
    assert result.upload_failed == 0
    archived = result.archived_paths[0]
    assert container_manager.get_transfer_status(archived) == "sent"
    with h5py.File(archived, "r") as h5f:
        assert h5f.attrs.get("upload_status") == "success"
        assert str(h5f.attrs.get("upload_result_message", "")).strip()


def test_reupload_archived_container_can_override_composite_specimen_mapping(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    old_format_folder = tmp_path / "Data" / "difra" / "Old_format"
    sid_a, path_a = _create_session_file(measurements, "326111__326169")
    _add_complete_session_payload(path_a)

    first_result = SessionLifecycleActions.send_and_archive_session_containers(
        container_paths=[path_a],
        container_manager=container_manager,
        archive_folder=archive_folder,
        lock_user="sad",
        session_ids={str(path_a): sid_a},
        config={
            "old_format_export_folder": str(old_format_folder),
            "enable_old_format_export": True,
        },
    )
    archived = first_result.archived_paths[0]

    resend_result = SessionLifecycleActions.reupload_archived_session_containers(
        container_paths=[archived],
        container_manager=container_manager,
        lock_user="sad",
        uploader_id="sad",
        config={
            "old_format_export_folder": str(old_format_folder),
            "enable_old_format_export": True,
        },
        specimen_overrides={str(archived): 326111},
    )

    assert resend_result.upload_success == 1
    assert resend_result.upload_failed == 0
    assert container_manager.get_transfer_status(archived) == "sent"
    with h5py.File(archived, "r") as h5f:
        assert int(h5f.attrs.get("matadorSpecimenId")) == 326111
        assert h5f.attrs.get("upload_status") == "success"
        assert int(h5f.attrs.get("upload_attempt_count", 0)) >= 2


def test_send_and_archive_old_format_export_enabled_by_default(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    old_format_folder = tmp_path / "old_format"
    sid_a, path_a = _create_session_file(measurements, "SAMPLE_A")

    result = SessionLifecycleActions.send_and_archive_session_containers(
        container_paths=[path_a],
        container_manager=container_manager,
        archive_folder=archive_folder,
        lock_user="sad",
        session_ids={str(path_a): sid_a},
        config={"old_format_export_folder": str(old_format_folder)},
    )

    assert result.moved == 1
    assert len(result.old_format_paths) == 1
    assert result.old_format_paths[0].exists() is True
    assert result.old_format_failed == []
    assert result.old_format_paths[0].parent == old_format_folder


def test_send_and_archive_old_format_export_can_be_disabled(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    sid_a, path_a = _create_session_file(measurements, "SAMPLE_A")

    result = SessionLifecycleActions.send_and_archive_session_containers(
        container_paths=[path_a],
        container_manager=container_manager,
        archive_folder=archive_folder,
        lock_user="sad",
        session_ids={str(path_a): sid_a},
        config={
            "old_format_export_folder": str(tmp_path / "old_format"),
            "enable_old_format_export": False,
        },
    )

    assert result.moved == 1
    assert result.old_format_paths == []
    assert result.old_format_failed == []


def test_send_and_archive_continues_after_lock_validation_error(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    sid_a, path_a = _create_session_file(measurements, "SAMPLE_A")

    with patch.object(
        container_manager,
        "lock_container",
        side_effect=RuntimeError("validation_failed"),
    ):
        result = SessionLifecycleActions.send_and_archive_session_containers(
            container_paths=[path_a],
            container_manager=container_manager,
            archive_folder=archive_folder,
            lock_user="sad",
            session_ids={str(path_a): sid_a},
            config={"upload_stub_failure_probability": 0.0},
        )

    assert result.moved == 1
    assert len(result.archived_paths) == 1
    assert result.archived_paths[0].exists() is True
    assert any("lock/validation skipped" in msg for msg in result.failed)


def test_send_and_archive_metadata_write_failure_marks_unsent(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    sid_a, path_a = _create_session_file(measurements, "SAMPLE_A")

    with patch.object(
        SessionLifecycleActions,
        "_write_container_attrs",
        return_value=False,
    ):
        result = SessionLifecycleActions.send_and_archive_session_containers(
            container_paths=[path_a],
            container_manager=container_manager,
            archive_folder=archive_folder,
            lock_user="sad",
            session_ids={str(path_a): sid_a},
            config={"upload_stub_failure_probability": 0.0},
        )

    assert result.moved == 1
    assert result.upload_success == 0
    assert result.upload_failed == 1
    assert any("metadata write failed" in msg for msg in result.failed)
    archived = result.archived_paths[0]
    assert container_manager.get_transfer_status(archived) == "unsent"


def test_send_and_archive_emits_progress_updates(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    sid_a, path_a = _create_session_file(measurements, "SAMPLE_A")

    progress_events = []
    result = SessionLifecycleActions.send_and_archive_session_containers(
        container_paths=[path_a],
        container_manager=container_manager,
        archive_folder=archive_folder,
        lock_user="sad",
        session_ids={str(path_a): sid_a},
        config={"upload_stub_failure_probability": 0.0},
        progress_callback=progress_events.append,
    )

    assert result.moved == 1
    messages = [str(event.get("message") or "") for event in progress_events if isinstance(event, dict)]
    assert any("Starting send+archive workflow" in message for message in messages)
    assert any("Archiving H5 container" in message for message in messages)
    assert any("Now uploading ZIP" in message for message in messages)
    assert any("Now uploading H5 container" in message for message in messages)
    assert any("SUCCESS - ZIP and H5 container uploaded and verified" in message for message in messages)


def test_archive_session_containers_marks_complete_vs_not_complete(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    sid_complete, complete_path = _create_session_file(measurements, "COMPLETE_SAMPLE")
    sid_incomplete, incomplete_path = _create_session_file(measurements, "INCOMPLETE_SAMPLE")

    _add_complete_session_payload(complete_path)

    result = SessionLifecycleActions.archive_session_containers(
        container_paths=[complete_path, incomplete_path],
        container_manager=container_manager,
        archive_folder=archive_folder,
        lock_user="sad",
        session_ids={
            str(complete_path): sid_complete,
            str(incomplete_path): sid_incomplete,
        },
    )

    assert result.failed == []
    assert result.moved == 2
    assert result.archived_complete == 1
    assert result.archived_not_complete == 1

    archived_by_sample = {}
    for archived_path in result.archived_paths:
        with h5py.File(archived_path, "r") as h5f:
            archived_by_sample[str(h5f.attrs.get("sample_id"))] = {
                "transfer_status": str(h5f.attrs.get("transfer_status")),
                "completion_status": str(h5f.attrs.get("session_completion_status")),
                "upload_status": str(h5f.attrs.get("upload_status")),
                "message": str(h5f.attrs.get("upload_result_message", "")),
            }

    assert archived_by_sample["COMPLETE_SAMPLE"]["transfer_status"] == "unsent"
    assert archived_by_sample["COMPLETE_SAMPLE"]["completion_status"] == "complete"
    assert archived_by_sample["COMPLETE_SAMPLE"]["upload_status"] == "unsent"
    assert "without Matador send" in archived_by_sample["COMPLETE_SAMPLE"]["message"]

    assert archived_by_sample["INCOMPLETE_SAMPLE"]["transfer_status"] == "not_complete"
    assert archived_by_sample["INCOMPLETE_SAMPLE"]["completion_status"] == "not_complete"
    assert archived_by_sample["INCOMPLETE_SAMPLE"]["upload_status"] == "not_complete"
    assert "missing sample image" in archived_by_sample["INCOMPLETE_SAMPLE"]["message"]


def test_archive_session_containers_mirrors_archived_folder(tmp_path):
    measurements = tmp_path / "measurements"
    archive_folder = tmp_path / "archive" / "measurements"
    mirror_folder = tmp_path / "onedrive_archive"
    sid_complete, complete_path = _create_session_file(measurements, "COMPLETE_SAMPLE")

    _add_complete_session_payload(complete_path)
    (measurements / "capture.txt").write_text("txt")

    result = SessionLifecycleActions.archive_session_containers(
        container_paths=[complete_path],
        container_manager=container_manager,
        archive_folder=archive_folder,
        config={"measurements_archive_mirror_folder": str(mirror_folder)},
        lock_user="sad",
        session_ids={str(complete_path): sid_complete},
    )

    assert result.failed == []
    assert result.moved == 1
    archived_folder = result.archived_paths[0].parent
    mirrored_folder = (
        mirror_folder
        / "Archive"
        / "measurements"
        / archived_folder.name
    )
    assert mirrored_folder.exists() is True
    assert (mirrored_folder / complete_path.name).exists() is True
    assert (mirrored_folder / "capture.txt").exists() is True
