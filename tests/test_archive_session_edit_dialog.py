from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialogButtonBox

from container.v0_2 import writer as session_writer
from difra.gui.main_window_ext.archive_session_edit_dialog import ArchiveSessionEditDialog
import difra.gui.main_window_ext.archive_session_edit_dialog as dialog_module


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


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


def test_archive_edit_dialog_blocks_without_runtime_matador_token(qapp, tmp_path, monkeypatch):
    session_path = _create_session_file(tmp_path / "measurements", "SAMPLE_A", "STUDY_A")
    monkeypatch.setattr(dialog_module, "get_runtime_matador_context", lambda _owner: {})

    dialog = ArchiveSessionEditDialog(
        container_paths=[session_path],
        matador_cache_path=tmp_path / "matador_cache.json",
    )
    try:
        assert dialog.project_combo.isEnabled() is False
        assert dialog.study_combo.isEnabled() is False
        assert dialog.button_box.button(QDialogButtonBox.Ok).isEnabled() is False
        assert "required" in dialog.matador_status_label.text().lower()
    finally:
        dialog.deleteLater()


def test_archive_edit_dialog_loads_projects_and_studies_live_from_matador(qapp, tmp_path, monkeypatch):
    session_path = _create_session_file(tmp_path / "measurements", "SAMPLE_A", "STUDY_A")
    monkeypatch.setattr(
        dialog_module,
        "get_runtime_matador_context",
        lambda _owner: {
            "token": "jwt-token",
            "matador_url": "https://portal.matur.co.uk",
        },
    )

    calls = []

    def _refresh_stub(**kwargs):
        calls.append(kwargs)
        return {
            "savedAt": "2026-04-23T16:00:00Z",
            "studies": [
                {
                    "id": 6751,
                    "name": "NewStudy",
                    "projectId": 6701,
                    "projectName": "NewProject",
                }
            ],
        }

    monkeypatch.setattr(dialog_module, "refresh_matador_reference_cache", _refresh_stub)

    dialog = ArchiveSessionEditDialog(
        container_paths=[session_path],
        matador_cache_path=tmp_path / "matador_cache.json",
    )
    try:
        assert len(calls) == 1
        assert calls[0]["token"] == "jwt-token"
        assert calls[0]["base_url"] == "https://portal.matur.co.uk"
        assert dialog.project_combo.isEnabled() is True
        assert dialog.study_combo.isEnabled() is True
        assert dialog.button_box.button(QDialogButtonBox.Ok).isEnabled() is True

        dialog.project_combo.setCurrentIndex(1)
        dialog.study_combo.setCurrentIndex(1)

        selection = dialog.get_selection()
        assert selection == {
            "project_id": 6701,
            "project_name": "NewProject",
            "study_id": 6751,
            "study_name": "NewStudy",
        }
        assert "matador data loaded" in dialog.matador_status_label.text().lower()
    finally:
        dialog.deleteLater()
