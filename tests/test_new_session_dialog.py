from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QApplication, QDialog

from difra.gui.matador_upload_api import save_matador_reference_cache
from difra.gui import operator_manager as operator_manager_module
from difra.gui.main_window_ext.new_session_dialog import NewSessionDialog
import difra.gui.main_window_ext.new_session_dialog as dialog_module


class _FakeLineEdit:
    def __init__(self, value: str = "") -> None:
        self._value = value

    def text(self) -> str:
        return self._value

    def setText(self, value: str) -> None:
        self._value = value

    def clear(self) -> None:
        self._value = ""


class _FakeLabel:
    def __init__(self) -> None:
        self.text = None

    def setText(self, value: str) -> None:
        self.text = value


class _FakeCombo:
    def __init__(self) -> None:
        self.items = []
        self.current_index = 0

    def clear(self) -> None:
        self.items.clear()
        self.current_index = 0

    def addItem(self, label: str, data=None) -> None:
        self.items.append((label, data))

    def count(self) -> int:
        return len(self.items)

    def setCurrentIndex(self, index: int) -> None:
        self.current_index = index

    def currentData(self):
        if not self.items:
            return None
        return self.items[self.current_index][1]

    def itemData(self, index: int):
        return self.items[index][1]

    def blockSignals(self, _blocked: bool) -> None:
        return None


class _FakeOperatorManager:
    def __init__(self, operators=None, current_id=None) -> None:
        self._operators = operators or {}
        self._current_id = current_id

    def get_all_operators(self):
        return dict(self._operators)

    def get_current_operator_id(self):
        return self._current_id

    def get_operator_display_name(self, operator_id: str) -> str:
        operator = self._operators[operator_id]
        return f"{operator['name']} {operator['surname']}"

    def get_operator(self, operator_id: str):
        return self._operators.get(operator_id)

    def set_current_operator(self, operator_id: str) -> bool:
        if operator_id not in self._operators:
            return False
        self._current_id = operator_id
        return True


class _FakeSettings:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.synced = False

    def value(self, key: str, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key: str, value) -> None:
        self.values[key] = value

    def sync(self) -> None:
        self.synced = True

    def remove(self, key: str) -> None:
        self.values.pop(key, None)


def _build_dialog_like(operator_manager) -> SimpleNamespace:
    specimen_edit = _FakeLineEdit()
    return SimpleNamespace(
        operator_manager=operator_manager,
        settings=_FakeSettings(),
        operator_combo=_FakeCombo(),
        operator_details_label=_FakeLabel(),
        specimen_id_edit=specimen_edit,
        sample_id_edit=specimen_edit,
        study_name_edit=_FakeLineEdit(),
        project_id_edit=_FakeLineEdit(),
        matador_study_id_edit=_FakeLineEdit(),
        matador_machine_id_edit=_FakeLineEdit(),
        distance_edit=_FakeLineEdit(),
        selected_operator_id=None,
        _save_last_session_defaults=lambda operator_id: None,
        _set_matador_status=lambda message: None,
        _last_auto_study_name="",
        _last_auto_project_name="",
        matador_study_combo=_FakeCombo(),
        matador_machine_combo=_FakeCombo(),
    )


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_dialog_constructor_builds_widgets_and_applies_defaults(qapp):
    operators = {
        "op2": {
            "name": "Beta",
            "surname": "User",
            "email": "beta@example.com",
        },
        "op1": {
            "name": "Alpha",
            "surname": "User",
            "email": "alpha@example.com",
            "institution": "Eos-Dx",
        },
    }
    manager = _FakeOperatorManager(operators, current_id="op1")

    dialog = NewSessionDialog(manager, default_distance=21.5)
    try:
        assert dialog.windowTitle() == "New Session"
        assert dialog.isModal() is True
        assert dialog.minimumWidth() == 560
        assert dialog.distance_edit.text() == "21.5"
        assert dialog.sample_id_edit is dialog.specimen_id_edit
        assert dialog.operator_combo.count() == 2
        assert dialog.operator_combo.currentData() == "op1"
        assert dialog.operator_details_label.text() == "Alpha User | alpha@example.com | Eos-Dx"
        assert dialog.matador_study_combo.count() == 1
        assert dialog.matador_machine_combo.count() == 1
    finally:
        dialog.deleteLater()


def test_dialog_loads_cached_matador_references(qapp, tmp_path):
    cache_path = tmp_path / "matador_cache.json"
    save_matador_reference_cache(
        studies=[
            {
                "id": 1701,
                "name": "Horizon_Grant1",
                "projectId": 11,
                "projectName": "Horizon",
            }
        ],
        machines=[{"id": 1751, "name": "MOLI"}],
        cache_path=cache_path,
    )
    manager = _FakeOperatorManager(
        {"op1": {"name": "Alpha", "surname": "User", "email": "alpha@example.com"}},
        current_id="op1",
    )

    dialog = NewSessionDialog(
        manager,
        default_distance=17.0,
        matador_cache_path=cache_path,
    )
    try:
        assert dialog.matador_study_combo.count() == 2
        assert dialog.matador_machine_combo.count() == 2
        dialog.matador_study_combo.setCurrentIndex(1)
        dialog.matador_machine_combo.setCurrentIndex(1)
        assert dialog.study_name_edit.text() == "Horizon_Grant1"
        assert dialog.project_id_edit.text() == "Horizon"
        assert dialog.matador_study_id_edit.text() == "1701"
        assert dialog.matador_machine_id_edit.text() == "1751"
    finally:
        dialog.deleteLater()


def test_dialog_restores_last_defaults_from_settings(qapp, monkeypatch):
    settings = _FakeSettings(
        {
            "matador/last_operator_id": "op1",
            "matador/last_study_name": "Keele_Grant2",
            "matador/last_project_id": "Keele",
            "matador/last_matador_study_id": "1701",
            "matador/last_matador_machine_id": "1751",
        }
    )
    monkeypatch.setattr(dialog_module, "QSettings", lambda *args, **kwargs: settings)
    manager = _FakeOperatorManager(
        {"op1": {"name": "Alpha", "surname": "User", "email": "alpha@example.com"}},
        current_id=None,
    )

    dialog = NewSessionDialog(manager, default_distance=17.0)
    try:
        assert dialog.operator_combo.currentData() == "op1"
        assert dialog.study_name_edit.text() == "Keele_Grant2"
        assert dialog.project_id_edit.text() == "Keele"
        assert dialog.matador_study_id_edit.text() == "1701"
        assert dialog.matador_machine_id_edit.text() == "1751"
    finally:
        dialog.deleteLater()


def test_populate_operator_combo_handles_empty_operator_list():
    fake_dialog = _build_dialog_like(_FakeOperatorManager())

    NewSessionDialog._populate_operator_combo(fake_dialog)

    assert fake_dialog.operator_combo.items == [("No operators defined", None)]


def test_populate_operator_combo_sorts_entries_and_selects_current_operator():
    operators = {
        "b": {"name": "Beta", "surname": "User"},
        "a": {"name": "Alpha", "surname": "User"},
    }
    fake_dialog = _build_dialog_like(_FakeOperatorManager(operators, current_id="b"))

    NewSessionDialog._populate_operator_combo(fake_dialog)

    assert fake_dialog.operator_combo.items == [
        ("Alpha User", "a"),
        ("Beta User", "b"),
    ]
    assert fake_dialog.operator_combo.current_index == 1


@pytest.mark.parametrize(
    ("current_data", "operator", "expected"),
    [
        (None, None, "No operator selected"),
        ("missing", None, "Operator not found"),
        (
            "op1",
            {
                "name": "Alice",
                "surname": "Smith",
                "email": "alice@example.com",
                "institution": "Eos-Dx",
            },
            "Alice Smith | alice@example.com | Eos-Dx",
        ),
        (
            "op2",
            {
                "name": "Bob",
                "surname": "Jones",
                "email": "bob@example.com",
            },
            "Bob Jones | bob@example.com",
        ),
    ],
)
def test_update_operator_details_covers_selection_states(current_data, operator, expected):
    operators = {current_data: operator} if current_data and operator else {}
    fake_dialog = _build_dialog_like(_FakeOperatorManager(operators))
    fake_dialog.operator_combo.addItem("Selected", current_data)

    NewSessionDialog._update_operator_details(fake_dialog)

    assert fake_dialog.operator_details_label.text == expected


def test_on_operator_changed_delegates_to_details_refresh():
    calls = []
    fake_dialog = _build_dialog_like(_FakeOperatorManager())
    fake_dialog._update_operator_details = lambda: calls.append("updated")

    NewSessionDialog._on_operator_changed(fake_dialog)

    assert calls == ["updated"]


def test_on_add_new_operator_repopulates_and_selects_new_operator(monkeypatch):
    fake_dialog = _build_dialog_like(_FakeOperatorManager())
    fake_dialog.operator_combo.items = [("Old", "old")]

    def _populate():
        fake_dialog.operator_combo.items = [("Old", "old"), ("New", "new-op")]

    fake_dialog._populate_operator_combo = _populate

    class _FakeNewOperatorDialog:
        def __init__(self, operator_manager, parent) -> None:
            self.operator_manager = operator_manager
            self.parent = parent

        def exec_(self) -> int:
            return QDialog.Accepted

        def get_operator_id(self) -> str:
            return "new-op"

    monkeypatch.setattr(operator_manager_module, "NewOperatorDialog", _FakeNewOperatorDialog)

    NewSessionDialog._on_add_new_operator(fake_dialog)

    assert fake_dialog.operator_combo.current_index == 1


def test_validate_and_accept_rejects_invalid_inputs(monkeypatch):
    warnings = []
    monkeypatch.setattr(
        dialog_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args[1:3]),
    )
    fake_dialog = _build_dialog_like(_FakeOperatorManager())
    fake_dialog.accept = lambda: warnings.append(("accepted", None))

    NewSessionDialog.validate_and_accept(fake_dialog)
    assert warnings[-1] == ("Missing Specimen ID", "Please enter a Specimen ID.")

    fake_dialog.specimen_id_edit = _FakeLineEdit("sample")
    NewSessionDialog.validate_and_accept(fake_dialog)
    assert warnings[-1] == ("Missing Study", "Please enter a Study name.")

    fake_dialog.study_name_edit = _FakeLineEdit("study")
    NewSessionDialog.validate_and_accept(fake_dialog)
    assert warnings[-1] == (
        "Missing Matador Study ID",
        "Please enter a Matador Study ID.",
    )

    fake_dialog.matador_study_id_edit = _FakeLineEdit("1701")
    NewSessionDialog.validate_and_accept(fake_dialog)
    assert warnings[-1] == (
        "Missing Matador Machine ID",
        "Please enter a Matador Machine ID.",
    )

    fake_dialog.matador_machine_id_edit = _FakeLineEdit("bad")
    NewSessionDialog.validate_and_accept(fake_dialog)
    assert warnings[-1] == (
        "Invalid Matador IDs",
        "Matador Study ID and Machine ID must be integers.",
    )

    fake_dialog.matador_machine_id_edit = _FakeLineEdit("1751")
    NewSessionDialog.validate_and_accept(fake_dialog)
    assert warnings[-1] == ("Missing Distance", "Please enter a distance value.")

    fake_dialog.distance_edit = _FakeLineEdit("bad")
    NewSessionDialog.validate_and_accept(fake_dialog)
    assert warnings[-1] == ("Invalid Distance", "Distance must be a number.")

    fake_dialog.distance_edit = _FakeLineEdit("17.5")
    fake_dialog.operator_combo.addItem("No operator", None)
    NewSessionDialog.validate_and_accept(fake_dialog)
    assert warnings[-1] == (
        "No Operator Selected",
        "Please select an operator or add a new one.",
    )


def test_validate_and_accept_sets_selected_operator_and_accepts(monkeypatch):
    warnings = []
    monkeypatch.setattr(
        dialog_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args[1:3]),
    )
    fake_dialog = _build_dialog_like(_FakeOperatorManager())
    fake_dialog.specimen_id_edit = _FakeLineEdit("sample")
    fake_dialog.study_name_edit = _FakeLineEdit("study")
    fake_dialog.project_id_edit = _FakeLineEdit("")
    fake_dialog.matador_study_id_edit = _FakeLineEdit("1701")
    fake_dialog.matador_machine_id_edit = _FakeLineEdit("1751")
    fake_dialog.distance_edit = _FakeLineEdit("17.5")
    fake_dialog.operator_combo.addItem("Operator", "op-1")
    fake_dialog.operator_manager._operators["op-1"] = {"name": "A", "surname": "B"}
    accepted = []
    saved_defaults = []
    fake_dialog.accept = lambda: accepted.append(True)
    fake_dialog._save_last_session_defaults = lambda operator_id: saved_defaults.append(
        operator_id
    )

    NewSessionDialog.validate_and_accept(fake_dialog)

    assert warnings == []
    assert fake_dialog.selected_operator_id == "op-1"
    assert accepted == [True]
    assert saved_defaults == ["op-1"]


def test_save_last_session_defaults_persists_last_selection():
    fake_dialog = _build_dialog_like(_FakeOperatorManager())
    fake_dialog.settings = _FakeSettings()
    fake_dialog.study_name_edit = _FakeLineEdit("Keele_Grant2")
    fake_dialog.project_id_edit = _FakeLineEdit("Keele")
    fake_dialog.matador_study_id_edit = _FakeLineEdit("1701")
    fake_dialog.matador_machine_id_edit = _FakeLineEdit("1751")

    NewSessionDialog._save_last_session_defaults(fake_dialog, "op-1")

    assert fake_dialog.settings.values["matador/last_operator_id"] == "op-1"
    assert fake_dialog.settings.values["matador/last_study_name"] == "Keele_Grant2"
    assert fake_dialog.settings.values["matador/last_project_id"] == "Keele"
    assert fake_dialog.settings.values["matador/last_matador_study_id"] == "1701"
    assert fake_dialog.settings.values["matador/last_matador_machine_id"] == "1751"
    assert fake_dialog.settings.synced is True


def test_clear_remembered_matador_defaults_resets_saved_values():
    fake_dialog = _build_dialog_like(_FakeOperatorManager())
    fake_dialog.settings = _FakeSettings(
        {
            "matador/last_operator_id": "op-1",
            "matador/last_study_name": "Keele_Grant2",
            "matador/last_project_id": "Keele",
            "matador/last_matador_study_id": "1701",
            "matador/last_matador_machine_id": "1751",
        }
    )
    fake_dialog.study_name_edit = _FakeLineEdit("Keele_Grant2")
    fake_dialog.project_id_edit = _FakeLineEdit("Keele")
    fake_dialog.matador_study_id_edit = _FakeLineEdit("1701")
    fake_dialog.matador_machine_id_edit = _FakeLineEdit("1751")
    fake_dialog.matador_study_combo.addItem("Manual", None)
    fake_dialog.matador_study_combo.addItem("Study", {"id": 1701})
    fake_dialog.matador_study_combo.setCurrentIndex(1)
    fake_dialog.matador_machine_combo.addItem("Manual", None)
    fake_dialog.matador_machine_combo.addItem("Machine", {"id": 1751})
    fake_dialog.matador_machine_combo.setCurrentIndex(1)
    status_messages = []
    fake_dialog._set_matador_status = lambda message: status_messages.append(message)

    NewSessionDialog._clear_remembered_matador_defaults(fake_dialog)

    assert fake_dialog.settings.values == {}
    assert fake_dialog.study_name_edit.text() == ""
    assert fake_dialog.project_id_edit.text() == ""
    assert fake_dialog.matador_study_id_edit.text() == ""
    assert fake_dialog.matador_machine_id_edit.text() == ""
    assert fake_dialog.matador_study_combo.current_index == 0
    assert fake_dialog.matador_machine_combo.current_index == 0
    assert fake_dialog._last_auto_study_name == ""
    assert fake_dialog._last_auto_project_name == ""
    assert fake_dialog.settings.synced is True
    assert status_messages[-1] == (
        "Remembered Matador defaults cleared. Refresh or choose new values."
    )


def test_get_parameters_trims_values_and_uses_study_as_project_fallback():
    fake_dialog = _build_dialog_like(_FakeOperatorManager())
    fake_dialog.specimen_id_edit = _FakeLineEdit(" 64101 ")
    fake_dialog.study_name_edit = _FakeLineEdit(" study-1 ")
    fake_dialog.project_id_edit = _FakeLineEdit(" ")
    fake_dialog.matador_study_id_edit = _FakeLineEdit(" 1701 ")
    fake_dialog.matador_machine_id_edit = _FakeLineEdit(" 1751 ")
    fake_dialog.distance_edit = _FakeLineEdit("18.25")
    fake_dialog.selected_operator_id = "op-9"

    params = NewSessionDialog.get_parameters(fake_dialog)

    assert params == {
        "sample_id": "64101",
        "specimenId": "64101",
        "study_name": "study-1",
        "project_id": "study-1",
        "matadorStudyId": 1701,
        "matadorMachineId": 1751,
        "distance_cm": 18.25,
        "operator_id": "op-9",
    }
