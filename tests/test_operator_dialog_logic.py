from __future__ import annotations

from types import SimpleNamespace

import difra.gui.operator_manager as operator_manager_module
from difra.gui.operator_manager import NewOperatorDialog, OperatorSelectionDialog


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


class _FakeLabel:
    def __init__(self) -> None:
        self.text = None

    def setText(self, value: str) -> None:
        self.text = value


class _FakeLineEdit:
    Password = 2

    def __init__(self, value: str = "") -> None:
        self._value = value
        self.read_only = False

    def text(self) -> str:
        return self._value

    def setText(self, value: str) -> None:
        self._value = value

    def setReadOnly(self, value: bool) -> None:
        self.read_only = value


class _FakeOperatorManager:
    def __init__(self, operators=None, current_id=None) -> None:
        self.operators = dict(operators or {})
        self.current_id = current_id
        self.add_calls = []
        self.set_calls = []
        self.password_ok = True

    def get_all_operators(self):
        return dict(self.operators)

    def get_current_operator_id(self):
        return self.current_id

    def get_operator(self, operator_id: str):
        return self.operators.get(operator_id)

    def get_operator_display_name(self, operator_id: str) -> str:
        op = self.operators[operator_id]
        return f"{op['name']} {op['surname']}"

    def set_current_operator(self, operator_id: str):
        self.current_id = operator_id
        self.set_calls.append(operator_id)
        return True

    def add_operator(self, **kwargs):
        self.add_calls.append(kwargs)
        self.operators[kwargs["operator_id"]] = kwargs

    def verify_modify_password(self, password: str) -> bool:
        return self.password_ok


def _selection_like(manager) -> SimpleNamespace:
    return SimpleNamespace(
        operator_manager=manager,
        operator_combo=_FakeCombo(),
        operator_details_label=_FakeLabel(),
        selected_operator_id=None,
        accept=lambda: None,
    )


def _new_operator_like(manager, existing_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        operator_manager=manager,
        _existing_operator_id=existing_id,
        id_edit=_FakeLineEdit(),
        name_edit=_FakeLineEdit(),
        surname_edit=_FakeLineEdit(),
        email_edit=_FakeLineEdit(),
        phone_edit=_FakeLineEdit(),
        institution_edit=_FakeLineEdit(),
        new_operator_id=None,
        accept=lambda: None,
    )


def test_selection_dialog_populate_and_update_details():
    manager = _FakeOperatorManager(
        operators={
            "op2": {"name": "Beta", "surname": "User", "email": "b@example.com"},
            "op1": {"name": "Alpha", "surname": "User", "email": "a@example.com", "phone": "123"},
        },
        current_id="op2",
    )
    dlg = _selection_like(manager)

    OperatorSelectionDialog._populate_operator_combo(dlg)
    OperatorSelectionDialog._update_operator_details(dlg)

    assert dlg.operator_combo.items[0][1] == "op1"
    assert dlg.operator_combo.currentData() == "op2"
    assert "Beta User" in dlg.operator_details_label.text


def test_selection_dialog_create_and_edit_operator_flow(monkeypatch):
    manager = _FakeOperatorManager(
        operators={"old": {"name": "Old", "surname": "User", "email": "o@example.com"}}
    )
    dlg = _selection_like(manager)
    dlg.operator_combo.items = [("Old User", "old")]

    def _populate_after_create():
        dlg.operator_combo.items = [("Old User", "old"), ("New User", "new")]

    dlg._populate_operator_combo = _populate_after_create

    class _FakeNewOperatorDialog:
        def __init__(self, operator_manager, parent, existing_operator_id=None):
            self.operator_manager = operator_manager
            self.parent = parent
            self.existing_operator_id = existing_operator_id

        def exec_(self):
            return 1

        def get_operator_id(self):
            return "new" if self.existing_operator_id is None else self.existing_operator_id

    monkeypatch.setattr(operator_manager_module, "NewOperatorDialog", _FakeNewOperatorDialog)
    warnings = []
    monkeypatch.setattr(
        operator_manager_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args[1:3]),
    )

    OperatorSelectionDialog._on_create_new_operator(dlg)
    assert dlg.operator_combo.currentData() == "new"

    dlg.operator_combo = _FakeCombo()
    OperatorSelectionDialog._on_edit_operator(dlg)
    assert warnings[-1][0] == "No Operator Selected"


def test_selection_dialog_accept_sets_current_operator(monkeypatch):
    manager = _FakeOperatorManager(
        operators={"op1": {"name": "A", "surname": "B", "email": "a@example.com"}}
    )
    accepted = []
    dlg = _selection_like(manager)
    dlg.accept = lambda: accepted.append(True)
    dlg.operator_combo.addItem("A B", "op1")
    warnings = []
    monkeypatch.setattr(
        operator_manager_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args[1:3]),
    )

    OperatorSelectionDialog._on_accept(dlg)
    assert warnings == []
    assert dlg.selected_operator_id == "op1"
    assert manager.set_calls == ["op1"]
    assert accepted == [True]

    dlg_empty = _selection_like(manager)
    OperatorSelectionDialog._on_accept(dlg_empty)
    assert warnings[-1][0] == "No Operator Selected"


def test_new_operator_dialog_load_operator_for_edit_and_password_check(monkeypatch):
    manager = _FakeOperatorManager(
        operators={"op1": {"name": "Alice", "surname": "Smith", "email": "a@example.com"}}
    )
    dlg = _new_operator_like(manager, existing_id="op1")

    NewOperatorDialog._load_operator_for_edit(dlg, "op1")
    assert dlg.id_edit.text() == "op1"
    assert dlg.id_edit.read_only is True
    assert dlg.name_edit.text() == "Alice"

    monkeypatch.setattr(
        operator_manager_module.QInputDialog,
        "getText",
        lambda *args, **kwargs: ("pw", True),
    )
    manager.password_ok = True
    assert NewOperatorDialog._confirm_modify_password(dlg) is True

    manager.password_ok = False
    warnings = []
    monkeypatch.setattr(
        operator_manager_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args[1:3]),
    )
    assert NewOperatorDialog._confirm_modify_password(dlg) is False
    assert warnings[-1][0] == "Invalid Password"


def test_new_operator_dialog_accept_validates_and_saves(monkeypatch):
    manager = _FakeOperatorManager(
        operators={"existing": {"name": "E", "surname": "X", "email": "e@example.com"}}
    )
    warnings = []
    infos = []
    criticals = []
    monkeypatch.setattr(
        operator_manager_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args[1:3]),
    )
    monkeypatch.setattr(
        operator_manager_module.QMessageBox,
        "information",
        lambda *args: infos.append(args[1:3]),
    )
    monkeypatch.setattr(
        operator_manager_module.QMessageBox,
        "critical",
        lambda *args: criticals.append(args[1:3]),
    )

    dlg = _new_operator_like(manager, existing_id=None)
    NewOperatorDialog._on_accept(dlg)
    assert warnings[-1][0] == "Missing Field"

    dlg.id_edit.setText("new_id")
    dlg.name_edit.setText("Alice")
    dlg.surname_edit.setText("Smith")
    dlg.email_edit.setText("a@example.com")
    accepted = []
    dlg.accept = lambda: accepted.append(True)
    NewOperatorDialog._on_accept(dlg)
    assert manager.add_calls[-1]["operator_id"] == "new_id"
    assert dlg.new_operator_id == "new_id"
    assert accepted == [True]
    assert infos[-1][0] == "Operator Created"
    assert criticals == []

    dlg_edit = _new_operator_like(manager, existing_id="existing")
    dlg_edit.id_edit.setText("different")
    dlg_edit.name_edit.setText("E")
    dlg_edit.surname_edit.setText("X")
    dlg_edit.email_edit.setText("e@example.com")
    NewOperatorDialog._on_accept(dlg_edit)
    assert warnings[-1][0] == "Operator ID Locked"
