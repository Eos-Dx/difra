"""GUI tests for Session tab send queue and archive list."""

import os
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from difra.gui.qt_compat import (
    QApplication,
    QDialog,
    QGroupBox,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from container.v0_2 import schema, writer as session_writer
from container.v0_2.container_manager import is_container_locked
from difra.gui.main_window_ext.zone_measurements.session_tab_mixin import SessionTabMixin


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeSessionManager:
    def __init__(self):
        self.session_path = None
        self.sample_id = None
        self.session_id = None
        self.operator_id = "sad"
        self.study_name = None
        self.close_calls = 0

    def is_session_active(self):
        return self.session_path is not None

    def close_session(self):
        self.session_path = None
        self.sample_id = None
        self.session_id = None
        self.study_name = None
        self.close_calls += 1

    def get_session_info(self):
        if not self.is_session_active():
            return {"active": False}
        return {
            "active": True,
            "session_id": self.session_id,
            "session_path": str(self.session_path),
            "sample_id": self.sample_id or "UNKNOWN",
            "study_name": self.study_name or "UNSPECIFIED",
            "operator_id": self.operator_id,
            "machine_name": "DIFRA_TEST",
            "beam_energy_kev": 17.5,
            "is_locked": is_container_locked(Path(self.session_path)),
            "i0_recorded": False,
            "i_recorded": False,
            "attenuation_complete": False,
        }


class _SessionQueueHarness(QMainWindow, SessionTabMixin):
    def __init__(self, config, session_manager):
        super().__init__()
        self.config = config
        self.session_manager = session_manager
        self.status_updates = 0

        container = QWidget(self)
        layout = QVBoxLayout(container)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.setCentralWidget(container)

        self.create_session_tab()

    def update_session_status(self):
        self.status_updates += 1


def _create_session_file(folder: Path, sample: str, study: str) -> Path:
    _session_id, session_path = session_writer.create_session_container(
        folder=folder,
        sample_id=sample,
        study_name=study,
        operator_id="sad",
        site_id="ULSTER",
        machine_name="DIFRA_TEST",
        beam_energy_keV=17.5,
        acquisition_date="2026-02-13",
    )
    return Path(session_path)


def _make_session_complete(session_path: Path):
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


def _open_archive_table(harness: _SessionQueueHarness, qapp):
    harness._show_archive_window()
    qapp.processEvents()
    return harness.archive_window_table


def test_session_queue_send_single_container(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))

    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    archive_folder = tmp_path / "archive" / "measurements"
    old_format_folder = tmp_path / "Data" / "difra" / "Old_format"
    first_session = _create_session_file(measurements_folder, "SAMPLE_A", "STUDY_A")

    session_manager = _FakeSessionManager()
    session_manager.session_path = first_session
    session_manager.sample_id = "SAMPLE_A"
    session_manager.study_name = "STUDY_A"
    session_manager.session_id = "active"

    harness = _SessionQueueHarness(
        config={
            "measurements_folder": str(measurements_folder),
            "measurements_archive_folder": str(archive_folder),
            "old_format_export_folder": str(old_format_folder),
            "enable_old_format_export": True,
            "matador_logs_folder": str(tmp_path / "matador_logs"),
        },
        session_manager=session_manager,
    )
    harness.show()
    qapp.processEvents()
    tab_names = [harness.tabs.tabText(idx) for idx in range(harness.tabs.count())]
    assert "Session" in tab_names
    assert "Archive" in tab_names

    harness._refresh_session_container_lists()
    assert harness._selected_pending_container() == first_session
    assert "File: session_" in harness._pending_session_summary_text
    assert "Specimen: SAMPLE_A" in harness._pending_session_summary_text
    archive_table = _open_archive_table(harness, qapp)
    assert archive_table.rowCount() == 0

    harness._on_send_pending_session()
    qapp.processEvents()

    assert harness._selected_pending_container() is None
    assert harness._pending_session_summary_text == "No session container in measurements folder."
    assert archive_table.rowCount() == 1
    assert list(old_format_folder.glob("*")) == []
    archived_files = sorted(archive_folder.rglob("session_*.nxs.h5"))
    assert len(archived_files) == 1
    with h5py.File(archived_files[0], "r") as h5f:
        assert bool(h5f.attrs.get("locked", False)) is True
        assert h5f.attrs.get(schema.ATTR_SAMPLE_ID) == "SAMPLE_A"
        assert h5f.attrs.get(schema.ATTR_STUDY_NAME) == "STUDY_A"
        assert h5f.attrs.get("uploaded_by") == "sad"
        assert str(h5f.attrs.get("upload_timestamp", "")).strip()
        assert str(h5f.attrs.get("upload_session_id", "")).startswith("upload_sad_")
        assert h5f.attrs.get("upload_status") == "success"
        assert "status=success" in str(h5f.attrs.get("upload_attempts_log", ""))

    # Active session got closed if it was the one sent
    assert session_manager.close_calls in {0, 1}

    harness.archive_window_project_filter_edit.setText("STUDY_A")
    harness._populate_archive_window_table()
    qapp.processEvents()
    assert archive_table.rowCount() == 1
    harness.archive_window_project_filter_edit.setText("")
    harness._populate_archive_window_table()


def test_session_queue_multiple_pending_containers_disables_actions(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))

    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    _create_session_file(measurements_folder, "SAMPLE_A", "STUDY_A")
    _create_session_file(measurements_folder, "SAMPLE_B", "STUDY_B")

    harness = _SessionQueueHarness(
        config={"measurements_folder": str(measurements_folder)},
        session_manager=_FakeSessionManager(),
    )
    harness.show()
    qapp.processEvents()

    harness._refresh_session_container_lists()

    assert harness._selected_pending_container() is None
    assert "Multiple session containers found" in harness._pending_session_summary_text
    assert harness.load_session_btn.isEnabled() is True
    assert harness.close_session_btn.isEnabled() is False
    assert harness.send_session_btn.isEnabled() is False
    assert harness.preview_session_data_btn.isEnabled() is False


def test_session_data_preview_enabled_for_loaded_locked_archived_container(qapp, tmp_path):
    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    archive_folder = tmp_path / "archive" / "measurements" / "20260430_120000"
    archive_folder.mkdir(parents=True, exist_ok=True)
    archived_path = _create_session_file(
        archive_folder,
        "378993__377656_P103_H_S03",
        "Mouse Skin - Grant 4",
    )
    with h5py.File(archived_path, "a") as h5f:
        h5f.attrs["locked"] = True
        h5f.attrs["transfer_status"] = "sent"
        h5f.attrs["session_state"] = "archived"

    session_manager = _FakeSessionManager()
    session_manager.session_path = archived_path
    session_manager.sample_id = "378993__377656_P103_H_S03"
    session_manager.study_name = "Mouse Skin - Grant 4"
    session_manager.session_id = "loaded_archived"

    harness = _SessionQueueHarness(
        config={
            "measurements_folder": str(measurements_folder),
            "measurements_archive_folder": str(tmp_path / "archive" / "measurements"),
        },
        session_manager=session_manager,
    )
    harness.show()
    qapp.processEvents()

    harness._update_session_tab_info()

    assert harness._selected_pending_container() is None
    assert harness._preview_session_container_path() == archived_path
    assert harness.preview_session_data_btn.isEnabled() is True


def test_open_archive_starts_pending_matador_verification(qapp, tmp_path, monkeypatch):
    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    archive_folder = tmp_path / "archive" / "measurements" / "20260522_120000"
    archive_folder.mkdir(parents=True, exist_ok=True)
    archived_path = _create_session_file(
        archive_folder,
        "337503__337552_P42_S01_RL",
        "Mouse Claw - Grant 1",
    )
    with h5py.File(archived_path, "a") as h5f:
        h5f.attrs["locked"] = True
        h5f.attrs["transfer_status"] = "unsent"
        h5f.attrs["upload_status"] = "pending_verification"
        h5f.attrs["matador_send_status"] = "pending_verification"
        h5f.attrs["matador_zip_file_id"] = "640001"
        h5f.attrs["matador_h5_file_id"] = "640002"

    calls = []

    def _fake_schedule(self, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        SessionTabMixin,
        "_schedule_matador_pending_verification",
        _fake_schedule,
    )

    harness = _SessionQueueHarness(
        config={
            "measurements_folder": str(measurements_folder),
            "measurements_archive_folder": str(tmp_path / "archive" / "measurements"),
            "matador_url": "https://matador.example",
            "matador_token": "jwt-for-test",
        },
        session_manager=_FakeSessionManager(),
    )
    harness.show()
    qapp.processEvents()

    archive_table = _open_archive_table(harness, qapp)
    status_filters = [
        harness.archive_window_status_filter_combo.itemText(index)
        for index in range(harness.archive_window_status_filter_combo.count())
    ]

    assert calls
    assert calls[0]["container_paths"] == [archived_path]
    assert calls[0]["initial_delay_sec"] == 0.0
    assert calls[0]["runtime_config"]["matador_upload_max_parallel"] == 4
    assert "Pending" not in status_filters
    assert "Failed" not in status_filters
    assert "UNSENT" in str(archive_table.item(0, 8).text())
    harness._archive_window_dialog.close()
    harness.close()


def test_send_selected_archived_report_to_analysts(qapp, tmp_path, monkeypatch):
    info_messages = []
    warning_messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda _parent, title, text: info_messages.append((title, text))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, text: warning_messages.append((title, text))),
    )
    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    archive_folder = tmp_path / "archive" / "measurements"
    archive_folder.mkdir(parents=True, exist_ok=True)
    archived_path = _create_session_file(archive_folder, "SAMPLE_A", "STUDY_A")
    calls = []

    def _build_report_stub(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            valid_containers=1,
            images=[tmp_path / "image.png"],
            zip_path=tmp_path / "report.zip",
            email_result={
                "sent": True,
                "skipped": False,
                "message": "daily report email sent",
            },
            skipped=[],
        )

    monkeypatch.setattr(
        "difra.gui.main_window_ext.zone_measurements.session_tab_mixin.build_daily_report_for_containers",
        _build_report_stub,
    )
    harness = _SessionQueueHarness(
        config={
            "measurements_folder": str(measurements_folder),
            "measurements_archive_folder": str(archive_folder),
            "difra_base_folder": str(tmp_path / "difra"),
        },
        session_manager=_FakeSessionManager(),
    )
    harness.show()
    qapp.processEvents()

    harness._send_selected_archived_report_to_analysts([archived_path])

    assert calls
    assert calls[0]["container_paths"] == [archived_path]
    assert calls[0]["send_email"] is True
    assert info_messages[-1][0] == "Report Sent to Analysts"
    assert warning_messages == []


def test_session_tab_shows_single_group_with_expected_buttons(qapp, tmp_path):
    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)

    harness = _SessionQueueHarness(
        config={"measurements_folder": str(measurements_folder)},
        session_manager=_FakeSessionManager(),
    )
    harness.show()
    qapp.processEvents()

    session_tab = harness.tabs.widget(0)
    group_titles = [group.title() for group in session_tab.findChildren(QGroupBox)]
    button_texts = {button.text() for button in session_tab.findChildren(QPushButton)}

    assert group_titles == ["Active Session Information"]
    assert button_texts == {
        "Create Session",
        "Load Container",
        "Close",
        "Close and Send",
        "Check data",
        "Refresh",
    }


def test_session_data_preview_collects_measurement_profiles_and_attenuation(
    qapp, tmp_path, monkeypatch
):
    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    session_path = _create_session_file(measurements_folder, "SAMPLE_A", "STUDY_A")
    session_writer.add_point(
        session_path,
        point_index=1,
        pixel_coordinates=[10.0, 12.0],
        physical_coordinates_mm=[1.0, 2.0],
    )
    session_writer.add_measurement(
        session_path,
        point_index=1,
        measurement_data={
            "PRIMARY": np.ones((4, 4), dtype=np.float32),
            "SECONDARY": np.ones((4, 4), dtype=np.float32) * 2.0,
        },
        detector_metadata={
            "PRIMARY": {"integration_time_ms": 100.0},
            "SECONDARY": {"integration_time_ms": 100.0},
        },
        poni_alias_map={"PRIMARY": "PRIMARY", "SECONDARY": "SECONDARY"},
    )
    with h5py.File(session_path, "a") as h5f:
        ana_group = h5f.require_group(schema.GROUP_ANALYTICAL_MEASUREMENTS.lstrip("/"))
        item = ana_group.require_group("ana_000000001")
        item.attrs[schema.ATTR_ANALYSIS_TYPE] = "attenuation"

    harness = _SessionQueueHarness(
        config={"measurements_folder": str(measurements_folder)},
        session_manager=_FakeSessionManager(),
    )
    calls = []

    def _extract(ref, alias="", npt=200):
        calls.append((ref, alias, npt))
        return {"q_values": [0.0, 1.0], "intensity": [1.0, 2.0]}

    monkeypatch.setattr(
        harness,
        "_extract_profile_from_measurement",
        _extract,
        raising=False,
    )

    payload = harness._collect_session_data_preview(session_path)

    assert payload["attenuation_exists"] is True
    assert len(payload["profiles"]["PRIMARY"]) == 1
    assert len(payload["profiles"]["SECONDARY"]) == 1
    assert ("PRIMARY", 200) in [(alias, npt) for _ref, alias, npt in calls]
    assert ("SECONDARY", 100) in [(alias, npt) for _ref, alias, npt in calls]


def test_session_queue_load_button_falls_back_to_file_dialog_when_measurements_folder_is_empty(
    qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))

    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    chosen_container = tmp_path / "picked_elsewhere" / "session_external.nxs.h5"
    chosen_container.parent.mkdir(parents=True, exist_ok=True)
    chosen_container.write_bytes(b"placeholder")

    harness = _SessionQueueHarness(
        config={"measurements_folder": str(measurements_folder)},
        session_manager=_FakeSessionManager(),
    )
    harness.show()
    qapp.processEvents()
    harness._refresh_session_container_lists()

    opened_paths = []
    monkeypatch.setattr(
        "difra.gui.main_window_ext.zone_measurements.session_tab_mixin.QFileDialog.getOpenFileName",
        staticmethod(
            lambda *args, **kwargs: (
                opened_paths.append((args[2], args[3])) or str(chosen_container),
                "NeXus HDF5 Files (*.nxs.h5 *.h5)",
            )
        ),
    )
    monkeypatch.setattr(harness, "_open_session_container_path", lambda path: opened_paths.append(Path(path)))

    assert harness.load_session_btn.isEnabled() is True

    harness._on_load_selected_session_container()

    assert opened_paths[0][0] == str(measurements_folder)
    assert "NeXus HDF5 Files" in opened_paths[0][1]
    assert opened_paths[1] == chosen_container


def test_archive_tab_can_resend_already_archived_container(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))

    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    archive_folder = tmp_path / "archive" / "measurements"
    old_format_folder = tmp_path / "Data" / "difra" / "Old_format"
    session_path = _create_session_file(measurements_folder, "326111__326169", "STUDY_A")
    _make_session_complete(session_path)

    session_manager = _FakeSessionManager()
    harness = _SessionQueueHarness(
        config={
            "measurements_folder": str(measurements_folder),
            "measurements_archive_folder": str(archive_folder),
            "old_format_export_folder": str(old_format_folder),
            "enable_old_format_export": True,
            "matador_logs_folder": str(tmp_path / "matador_logs"),
        },
        session_manager=session_manager,
    )
    harness.show()
    qapp.processEvents()

    monkeypatch.setattr(
        harness,
        "_request_upload_login_context",
        lambda fallback_operator: {
            "uploader_id": fallback_operator,
            "token": "",
            "matador_url": "https://portal.matur.co.uk",
        },
    )

    harness._send_and_archive_sessions([session_path])
    qapp.processEvents()

    archived_files = sorted(archive_folder.rglob("session_*.nxs.h5"))
    assert len(archived_files) == 1
    archived = archived_files[0]
    with h5py.File(archived, "r") as h5f:
        assert h5f.attrs.get("upload_status") == "success"
        assert int(h5f.attrs.get("matadorSpecimenId")) == 326111

    monkeypatch.setattr(
        harness,
        "_request_matador_specimen_override",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("override should not be requested")),
    )
    harness._send_archived_sessions([archived])
    qapp.processEvents()

    with h5py.File(archived, "r") as h5f:
        assert int(h5f.attrs.get("matadorSpecimenId")) == 326111
        assert h5f.attrs.get("upload_status") == "success"
        assert int(h5f.attrs.get("upload_attempt_count", 0)) >= 2


def test_session_tab_close_finalize_active_session(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))

    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    archive_folder = tmp_path / "archive" / "measurements"
    old_format_folder = tmp_path / "Data" / "difra" / "Old_format"

    active_session = _create_session_file(measurements_folder, "SAMPLE_FINAL", "STUDY_FINAL")
    (measurements_folder / "SAMPLE_FINAL_state.json").write_text('{"demo": true}')
    (measurements_folder / "capture.txt").write_text("raw")
    (measurements_folder / "capture.npy").write_text("processed")

    session_manager = _FakeSessionManager()
    session_manager.session_path = active_session
    session_manager.sample_id = "SAMPLE_FINAL"
    session_manager.study_name = "STUDY_FINAL"
    session_manager.session_id = "session_final"

    harness = _SessionQueueHarness(
        config={
            "measurements_folder": str(measurements_folder),
            "measurements_archive_folder": str(archive_folder),
            "old_format_export_folder": str(old_format_folder),
            "enable_old_format_export": True,
        },
        session_manager=session_manager,
    )
    harness.show()
    qapp.processEvents()

    harness._on_close_finalize_session()
    qapp.processEvents()

    assert session_manager.close_calls == 1
    assert active_session.exists() is False
    archived_sessions = sorted(archive_folder.rglob("session_*.nxs.h5"))
    assert archived_sessions, "Expected archived session container after finalize"
    assert is_container_locked(archived_sessions[-1]) is True

    archived_dir = archived_sessions[-1].parent
    assert archived_dir.exists() is True
    assert (archived_dir / "SAMPLE_FINAL_state.json").exists() is True
    assert (archived_dir / "capture.txt").exists() is True
    assert (archived_dir / "capture.npy").exists() is True

    bundle_zip = archived_dir.with_suffix(".zip")
    assert bundle_zip.exists() is True
    old_dirs = [path for path in old_format_folder.glob("*") if path.is_dir()]
    assert len(old_dirs) == 1


def test_archived_container_manual_generate_old_format(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))

    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    archive_folder = tmp_path / "archive" / "measurements"
    old_format_folder = tmp_path / "Data" / "difra" / "Old_format"

    _create_session_file(measurements_folder, "SAMPLE_ARCH", "STUDY_ARCH")

    session_manager = _FakeSessionManager()
    harness = _SessionQueueHarness(
        config={
            "measurements_folder": str(measurements_folder),
            "measurements_archive_folder": str(archive_folder),
            "old_format_export_folder": str(old_format_folder),
            "enable_old_format_export": False,
            "matador_logs_folder": str(tmp_path / "matador_logs"),
        },
        session_manager=session_manager,
    )
    harness.show()
    qapp.processEvents()

    harness._on_send_pending_session()
    qapp.processEvents()
    archive_table = _open_archive_table(harness, qapp)
    assert archive_table.rowCount() == 1
    assert list(old_format_folder.glob("*")) == []

    archived_path = harness._path_from_table_row(archive_table, 0, 9)
    assert archived_path is not None and archived_path.exists() is True
    harness._generate_old_format_for_container(archived_path)
    qapp.processEvents()

    old_dirs = [path for path in old_format_folder.glob("*") if path.is_dir()]
    assert len(old_dirs) == 1


def test_archive_tab_can_edit_project_and_study_without_touching_specimen(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))

    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    archive_folder = tmp_path / "archive" / "measurements"

    session_path = _create_session_file(measurements_folder, "SAMPLE_KEEP", "OLD_STUDY")
    with h5py.File(session_path, "a") as h5f:
        h5f.attrs["project_id"] = "OLD_PROJECT"
        h5f.attrs["matadorProjectId"] = 11
        h5f.attrs["matadorProjectName"] = "OLD_PROJECT"
        h5f.attrs["matadorStudyId"] = 22
        h5f.attrs["meta_json"] = '{"sample_id": "SAMPLE_KEEP", "project_id": "OLD_PROJECT", "study_name": "OLD_STUDY"}'

    session_manager = _FakeSessionManager()
    harness = _SessionQueueHarness(
        config={
            "measurements_folder": str(measurements_folder),
            "measurements_archive_folder": str(archive_folder),
        },
        session_manager=session_manager,
    )
    harness.show()
    qapp.processEvents()

    harness._archive_sessions([session_path])
    qapp.processEvents()

    archive_table = _open_archive_table(harness, qapp)
    archived_path = harness._path_from_table_row(archive_table, 0, 9)
    assert archived_path is not None and archived_path.exists() is True

    class _FakeEditDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec_(self):
            return QDialog.Accepted

        def get_selection(self):
            return {
                "project_id": 6701,
                "project_name": "NewProject",
                "study_id": 6751,
                "study_name": "NewStudy",
            }

    monkeypatch.setattr(harness, "_confirm_archive_metadata_edit_password", lambda: True)
    monkeypatch.setattr(
        "difra.gui.main_window_ext.zone_measurements.session_tab_mixin.ArchiveSessionEditDialog",
        _FakeEditDialog,
    )

    harness._edit_archived_sessions([archived_path])
    qapp.processEvents()

    with h5py.File(archived_path, "r") as h5f:
        assert h5f.attrs[schema.ATTR_SAMPLE_ID] == "SAMPLE_KEEP"
        assert str(h5f.attrs.get("specimenId", "SAMPLE_KEEP")) == "SAMPLE_KEEP"
        assert h5f.attrs["project_id"] == "NewProject"
        assert h5f.attrs["matadorProjectName"] == "NewProject"
        assert int(h5f.attrs["matadorProjectId"]) == 6701
        assert h5f.attrs["study_name"] == "NewStudy"
        assert int(h5f.attrs["matadorStudyId"]) == 6751
        assert "operator=sad" in str(h5f.attrs.get("archive_metadata_edit_log", ""))

    harness._refresh_session_container_lists()
    assert archive_table.item(0, 1).text() == "SAMPLE_KEEP"
    assert archive_table.item(0, 2).text() == "NewProject"
    assert archive_table.item(0, 3).text() == "NewStudy"


def test_session_queue_close_archives_and_marks_incomplete(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: QMessageBox.Ok))

    measurements_folder = tmp_path / "measurements"
    measurements_folder.mkdir(parents=True, exist_ok=True)
    archive_folder = tmp_path / "archive" / "measurements"

    complete_session = _create_session_file(measurements_folder, "SAMPLE_OK", "STUDY_OK")
    incomplete_session = _create_session_file(measurements_folder, "SAMPLE_BAD", "STUDY_BAD")
    _make_session_complete(complete_session)

    session_manager = _FakeSessionManager()
    harness = _SessionQueueHarness(
        config={
            "measurements_folder": str(measurements_folder),
            "measurements_archive_folder": str(archive_folder),
        },
        session_manager=session_manager,
    )
    harness.show()
    qapp.processEvents()

    harness._archive_sessions([complete_session, incomplete_session])
    qapp.processEvents()
    assert harness._selected_pending_container() is None
    assert harness._pending_session_summary_text == "No session container in measurements folder."
    archive_table = _open_archive_table(harness, qapp)
    assert archive_table.rowCount() == 2

    archived_files = sorted(archive_folder.rglob("session_*.nxs.h5"))
    assert len(archived_files) == 2

    archived_statuses = {}
    for archived in archived_files:
        with h5py.File(archived, "r") as h5f:
            archived_statuses[str(h5f.attrs.get(schema.ATTR_SAMPLE_ID))] = (
                str(h5f.attrs.get("transfer_status")),
                str(h5f.attrs.get("session_completion_status")),
            )

    assert archived_statuses["SAMPLE_OK"] == ("unsent", "complete")
    assert archived_statuses["SAMPLE_BAD"] == ("not_complete", "not_complete")
