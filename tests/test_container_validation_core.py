from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import h5py
import pytest

from difra.utils import container_validation
from difra.utils.container_validation import ValidationMessage, ValidationReport


def _create_h5(path: Path, *, container_type=None, groups=()):
    with h5py.File(path, "w") as handle:
        if container_type is not None:
            handle.attrs["container_type"] = container_type
        for group in groups:
            handle.require_group(group)


def test_detect_container_type_uses_metadata_and_group_fallbacks(tmp_path: Path):
    session_file = tmp_path / "session.h5"
    _create_h5(session_file, container_type="session")
    assert container_validation.detect_container_type(session_file) == "session"

    technical_file = tmp_path / "technical.h5"
    _create_h5(technical_file, groups=("/entry/technical",))
    assert container_validation.detect_container_type(technical_file) == "technical"

    unknown_file = tmp_path / "unknown.h5"
    _create_h5(unknown_file)
    with pytest.raises(ValueError):
        container_validation.detect_container_type(unknown_file)


def test_validate_container_session_and_technical_paths(tmp_path: Path, monkeypatch):
    path = tmp_path / "container.h5"
    _create_h5(path, container_type="session")

    monkeypatch.setattr(container_validation.loader, "detect_version", lambda p: "0.2")

    class _SessionValidator:
        def __init__(self, file_path):
            self.file_path = file_path

        def validate(self):
            return True, [SimpleNamespace(severity="WARNING", path="/entry", message="ok")]

    class _TechnicalValidator:
        def __init__(self, file_path, strict=False):
            self.file_path = file_path
            self.strict = strict

        def validate(self):
            return False, ["missing field"], ["deprecated field"]

    module = SimpleNamespace(
        validator=SimpleNamespace(SessionContainerValidator=_SessionValidator),
        technical_validator=SimpleNamespace(TechnicalContainerValidator=_TechnicalValidator),
    )
    monkeypatch.setattr(container_validation, "load_version_module", lambda version: module)

    session_report = container_validation.validate_container(path, container_kind="session")
    assert session_report.is_valid is True
    assert session_report.container_kind == "session"
    assert session_report.messages[0].severity == "WARNING"

    technical_report = container_validation.validate_container(path, container_kind="technical")
    assert technical_report.is_valid is False
    assert technical_report.container_kind == "technical"
    assert [m.severity for m in technical_report.messages] == ["ERROR", "WARNING"]


def test_validate_container_rejects_unsupported_version_or_kind(tmp_path: Path, monkeypatch):
    path = tmp_path / "container.h5"
    _create_h5(path, container_type="session")
    monkeypatch.setattr(container_validation.loader, "detect_version", lambda p: "0.1")

    with pytest.raises(ValueError):
        container_validation.validate_container(path)

    monkeypatch.setattr(container_validation.loader, "detect_version", lambda p: "0.2")
    with pytest.raises(ValueError):
        container_validation.validate_container(path, container_kind="broken")


def test_validation_report_helpers_and_format_report_render_messages():
    report = ValidationReport(
        file_path="/tmp/demo.h5",
        schema_version="0.2",
        container_kind="session",
        is_valid=False,
        messages=[
            ValidationMessage("ERROR", "/entry", "missing"),
            ValidationMessage("WARNING", "/entry", "deprecated"),
        ],
    )

    assert [m.message for m in report.errors] == ["missing"]
    assert [m.message for m in report.warnings] == ["deprecated"]

    text = container_validation.format_report(report)
    assert "DIFRA Container Validation Report" in text
    assert "INVALID" in text
    assert "ERROR [/entry] missing" in text
