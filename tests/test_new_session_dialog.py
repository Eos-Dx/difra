from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QApplication, QDialog

from difra.gui import operator_manager as operator_manager_module
from difra.gui.main_window_ext.new_session_dialog import NewSessionDialog
import difra.gui.main_window_ext.new_session_dialog as dialog_module


class _FakeLineEdit:
    def __init__(self, value: str = "") -> None:
        self._value = value

    def text(self) -> str:
        return self._value


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


def _build_dialog_like(operator_manager) -> SimpleNamespace:
    return SimpleNamespace(
        operator_manager=operator_manager,
        operator_combo=_FakeCombo(),
        operator_details_label=_FakeLabel(),
        sample_id_edit=_FakeLineEdit(),
        study_name_edit=_FakeLineEdit(),
        project_id_edit=_FakeLineEdit(),
        distance_edit=_FakeLineEdit(),
        selected_operator_id=None,
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
        assert dialog.minimumWidth() == 500
        assert dialog.distance_edit.text() == "21.5"
        assert dialog.operator_combo.count() == 2
        assert dialog.operator_combo.currentData() == "op1"
        assert dialog.operator_details_label.text() == "Alpha User | alpha@example.com | Eos-Dx"
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
    assert warnings[-1] == ("Missing Sample ID", "Please enter a Sample ID.")

    fake_dialog.sample_id_edit = _FakeLineEdit("sample")
    NewSessionDialog.validate_and_accept(fake_dialog)
    assert warnings[-1] == ("Missing Study", "Please enter a Study name.")

    fake_dialog.study_name_edit = _FakeLineEdit("study")
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
    fake_dialog.sample_id_edit = _FakeLineEdit("sample")
    fake_dialog.study_name_edit = _FakeLineEdit("study")
    fake_dialog.project_id_edit = _FakeLineEdit("")
    fake_dialog.distance_edit = _FakeLineEdit("17.5")
    fake_dialog.operator_combo.addItem("Operator", "op-1")
    accepted = []
    fake_dialog.accept = lambda: accepted.append(True)

    NewSessionDialog.validate_and_accept(fake_dialog)

    assert warnings == []
    assert fake_dialog.selected_operator_id == "op-1"
    assert accepted == [True]


def test_get_parameters_trims_values_and_uses_study_as_project_fallback():
    fake_dialog = _build_dialog_like(_FakeOperatorManager())
    fake_dialog.sample_id_edit = _FakeLineEdit(" sample-1 ")
    fake_dialog.study_name_edit = _FakeLineEdit(" study-1 ")
    fake_dialog.project_id_edit = _FakeLineEdit(" ")
    fake_dialog.distance_edit = _FakeLineEdit("18.25")
    fake_dialog.selected_operator_id = "op-9"

    params = NewSessionDialog.get_parameters(fake_dialog)

    assert params == {
        "sample_id": "sample-1",
        "study_name": "study-1",
        "project_id": "study-1",
        "distance_cm": 18.25,
        "operator_id": "op-9",
    }
