from __future__ import annotations

import json
from pathlib import Path

import difra.gui.operator_manager as operator_manager_module
from difra.gui.operator_manager import (
    DEFAULT_MODIFICATION_PASSWORD_HASH,
    OperatorManager,
    _hash_password,
)


def test_hash_password_is_stable():
    assert _hash_password("secret") == _hash_password("secret")
    assert _hash_password("secret") != _hash_password("other")


def test_operator_manager_creates_default_config_when_missing(tmp_path: Path):
    config_path = tmp_path / "operators.json"

    manager = OperatorManager(config_path=config_path)

    assert config_path.exists()
    assert "default_operator" in manager.get_all_operators()
    assert manager.get_current_operator_id() == "default_operator"
    assert manager.operator_modify_password_hash == DEFAULT_MODIFICATION_PASSWORD_HASH


def test_operator_manager_bootstraps_missing_password_hash_in_existing_config(tmp_path: Path):
    config_path = tmp_path / "operators.json"
    config_path.write_text(
        json.dumps(
            {
                "operators": {
                    "op1": {
                        "name": "Alice",
                        "surname": "Smith",
                        "email": "alice@example.com",
                    }
                },
                "current_operator_id": "op1",
            }
        ),
        encoding="utf-8",
    )

    manager = OperatorManager(config_path=config_path)
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert manager.get_current_operator_id() == "op1"
    assert manager.operator_modify_password_hash == DEFAULT_MODIFICATION_PASSWORD_HASH
    assert saved["operator_modify_password_hash"] == DEFAULT_MODIFICATION_PASSWORD_HASH


def test_operator_manager_load_invalid_json_falls_back_to_default(tmp_path: Path, monkeypatch):
    warnings = []
    config_path = tmp_path / "operators.json"
    config_path.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setattr(
        operator_manager_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args[1:3]),
    )

    manager = OperatorManager(config_path=config_path)

    assert warnings and warnings[0][0] == "Operator Config Error"
    assert manager.get_current_operator_id() == "default_operator"
    assert "default_operator" in manager.get_all_operators()


def test_operator_manager_add_remove_and_current_operator_flow(tmp_path: Path):
    manager = OperatorManager(config_path=tmp_path / "operators.json")

    manager.add_operator(
        operator_id="op2",
        name="Bob",
        surname="Jones",
        email="bob@example.com",
        phone="+123",
        institution="Eos-Dx",
    )
    assert manager.get_operator("op2")["email"] == "bob@example.com"
    assert manager.set_current_operator("op2") is True
    assert manager.get_current_operator_id() == "op2"
    assert manager.get_current_operator()["name"] == "Bob"
    assert manager.get_operator_display_name("op2") == "Bob Jones (bob@example.com)"

    assert manager.remove_operator("op2") is True
    assert manager.get_operator("op2") is None
    assert manager.get_current_operator_id() is None
    assert manager.remove_operator("op2") is False
    assert manager.set_current_operator("op2") is False
    assert manager.get_operator_display_name("unknown") == "unknown"


def test_operator_manager_verify_modify_password(tmp_path: Path):
    manager = OperatorManager(config_path=tmp_path / "operators.json")
    manager.operator_modify_password_hash = _hash_password("letmein")

    assert manager.verify_modify_password("letmein") is True
    assert manager.verify_modify_password("wrong") is False
    assert manager.verify_modify_password("") is False
