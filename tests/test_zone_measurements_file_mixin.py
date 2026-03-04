from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt5.QtWidgets import QMessageBox

import difra.gui.main_window_ext.zone_measurements.logic.file_mixin as file_mixin_module
from difra.gui.main_window_ext.zone_measurements.logic.file_mixin import (
    ZoneMeasurementsFileMixin,
)


class _FakeLineEdit:
    def __init__(self, text: str = "") -> None:
        self.value = text

    def setText(self, text: str) -> None:
        self.value = text

    def text(self) -> str:
        return self.value


class _FakeSpinBox:
    def __init__(self, value: int) -> None:
        self._value = value

    def value(self) -> int:
        return self._value


class _FakeTimer:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


class _FakeStatus:
    def __init__(self) -> None:
        self.values = []

    def setText(self, text: str) -> None:
        self.values.append(text)


class _FakeListWidget:
    def __init__(self) -> None:
        self.items = []

    def addItem(self, item) -> None:
        self.items.append(item)


class _FakeListWidgetItem:
    def __init__(self, text: str) -> None:
        self.text = text
        self.data = {}

    def setData(self, role, value) -> None:
        self.data[role] = value


def test_browse_folder_refuses_when_measurement_output_is_locked(monkeypatch, tmp_path: Path):
    messages = []
    fake_owner = SimpleNamespace(
        _is_measurement_output_folder_locked=lambda: True,
        _current_measurement_output_folder=lambda: tmp_path / "session",
        _refresh_measurement_output_folder_lock=lambda: messages.append("refreshed"),
        folderLineEdit=_FakeLineEdit(),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append(args[1:3]),
    )

    ZoneMeasurementsFileMixin.browse_folder(fake_owner)

    assert messages[0][0] == "Measurement Folder Locked"
    assert "Folder:" in messages[0][1]
    assert messages[1] == "refreshed"


def test_browse_folder_sets_selected_directory(monkeypatch, tmp_path: Path):
    fake_owner = SimpleNamespace(folderLineEdit=_FakeLineEdit())
    monkeypatch.setattr(
        file_mixin_module.QFileDialog,
        "getExistingDirectory",
        lambda *args: str(tmp_path),
    )

    ZoneMeasurementsFileMixin.browse_folder(fake_owner)

    assert fake_owner.folderLineEdit.text() == str(tmp_path)


def test_browse_folder_leaves_value_unchanged_when_user_cancels(monkeypatch):
    fake_owner = SimpleNamespace(folderLineEdit=_FakeLineEdit("existing"))
    monkeypatch.setattr(
        file_mixin_module.QFileDialog,
        "getExistingDirectory",
        lambda *args: "",
    )

    ZoneMeasurementsFileMixin.browse_folder(fake_owner)

    assert fake_owner.folderLineEdit.text() == "existing"


def test_process_measurement_result_handles_capture_failure(capsys):
    fake_owner = SimpleNamespace(
        _aux_timer=_FakeTimer(),
        _aux_status=_FakeStatus(),
    )

    result = ZoneMeasurementsFileMixin.process_measurement_result(
        fake_owner,
        False,
        {"detector": "/tmp/file.txt"},
        "AUX",
    )

    assert result == {}
    assert fake_owner._aux_timer.stopped == 1
    assert fake_owner._aux_status.values == [""]
    assert "[AUX] capture failed." in capsys.readouterr().out


def test_process_measurement_result_converts_txt_files_and_updates_list(
    monkeypatch, tmp_path: Path, capsys
):
    txt_file = tmp_path / "result.txt"
    txt_file.write_text("1 2 3\n4 5 6\n", encoding="utf-8")
    monkeypatch.setattr(file_mixin_module, "QListWidgetItem", _FakeListWidgetItem)
    fake_owner = SimpleNamespace(
        _aux_timer=_FakeTimer(),
        _aux_status=_FakeStatus(),
        auxList=_FakeListWidget(),
    )

    result = ZoneMeasurementsFileMixin.process_measurement_result(
        fake_owner,
        True,
        {"detector_a": str(txt_file)},
        "TECH",
    )

    npy_path = txt_file.with_suffix(".npy")
    assert result == {"detector_a": str(npy_path)}
    assert npy_path.exists()
    loaded = np.load(npy_path)
    assert loaded.shape == (2, 3)
    assert fake_owner._aux_timer.stopped == 1
    assert fake_owner._aux_status.values == ["Done"]
    assert fake_owner.auxList.items[0].text == "detector_a: result.npy"
    assert "[TECH] capture successful" in capsys.readouterr().out


def test_process_measurement_result_keeps_txt_path_when_conversion_fails(
    monkeypatch, tmp_path: Path, capsys
):
    txt_file = tmp_path / "broken.txt"
    txt_file.write_text("bad data", encoding="utf-8")
    fake_owner = SimpleNamespace(auxList=_FakeListWidget())
    monkeypatch.setattr(file_mixin_module, "QListWidgetItem", _FakeListWidgetItem)
    monkeypatch.setattr(
        file_mixin_module.np,
        "loadtxt",
        lambda path: (_ for _ in ()).throw(ValueError("decode error")),
    )

    result = ZoneMeasurementsFileMixin.process_measurement_result(
        fake_owner,
        True,
        {"detector_b": str(txt_file)},
        "TECH",
    )

    assert result == {"detector_b": str(txt_file)}
    assert "Conversion error for detector_b" in capsys.readouterr().out


def test_handle_add_count_appends_spinner_value_to_filename():
    fake_owner = SimpleNamespace(
        fileNameLineEdit=_FakeLineEdit("sample_001"),
        addCountSpinBox=_FakeSpinBox(7),
    )

    ZoneMeasurementsFileMixin.handle_add_count(fake_owner)

    assert fake_owner.fileNameLineEdit.text() == "sample_001_7"
