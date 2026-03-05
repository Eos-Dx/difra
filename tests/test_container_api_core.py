from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import difra.gui.container_api as container_api


def test_read_json_returns_empty_dict_for_missing_or_invalid_file(tmp_path: Path):
    missing = tmp_path / "missing.json"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{bad", encoding="utf-8")
    container_api._read_json.cache_clear()

    assert container_api._read_json(missing) == {}
    assert container_api._read_json(invalid) == {}


def test_get_container_version_resolution_order(tmp_path: Path, monkeypatch):
    main_cfg = tmp_path / "main.json"
    global_cfg = tmp_path / "global.json"
    main_cfg.write_text(json.dumps({"container_version": "0.1"}), encoding="utf-8")
    global_cfg.write_text(json.dumps({"container_version": "0.2"}), encoding="utf-8")
    monkeypatch.setattr(container_api, "_MAIN_CONFIG_PATH", main_cfg)
    monkeypatch.setattr(container_api, "_GLOBAL_CONFIG_PATH", global_cfg)
    container_api._read_json.cache_clear()

    assert container_api.get_container_version({"container_version": "0.9"}) == "0.9"
    assert container_api.get_container_version({}) == "0.1"

    main_cfg.write_text("{}", encoding="utf-8")
    container_api._read_json.cache_clear()
    assert container_api.get_container_version({}) == "0.2"

    global_cfg.write_text("{}", encoding="utf-8")
    container_api._read_json.cache_clear()
    assert container_api.get_container_version({}) == container_api.DEFAULT_CONTAINER_VERSION


def test_container_api_accessors_use_loaded_version_module(monkeypatch):
    module = SimpleNamespace(
        schema="schema_mod",
        writer="writer_mod",
        container_manager="manager_mod",
        technical_container="technical_mod",
        technical_validator="validator_mod",
        session_container="session_mod",
    )
    monkeypatch.setattr(container_api, "load_version_module", lambda version: module)
    monkeypatch.setattr(container_api, "normalize_version", lambda version: f"norm-{version}")
    monkeypatch.setattr(container_api, "get_container_version", lambda config: "0.2")

    assert container_api.get_container_module({}) is module
    assert container_api.get_schema({}) == "schema_mod"
    assert container_api.get_writer({}) == "writer_mod"
    assert container_api.get_container_manager({}) == "manager_mod"
    assert container_api.get_technical_container({}) == "technical_mod"
    assert container_api.get_technical_validator({}) == "validator_mod"
    assert container_api.get_session_container({}) == "session_mod"
