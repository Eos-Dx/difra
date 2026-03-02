"""Tests for metadata-driven v0.2 container validation."""

import sys
from pathlib import Path

import h5py
import pytest

# Add project src to path
SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hardware.difra.utils import container_validation


def _write_container(path: Path, schema_version: str, container_type: str) -> None:
    with h5py.File(path, "w") as file_handle:
        file_handle.attrs["schema_version"] = schema_version
        file_handle.attrs["container_type"] = container_type


def test_validate_container_uses_schema_version_from_metadata(monkeypatch, tmp_path):
    container_path = tmp_path / "container.nxs.h5"
    _write_container(container_path, "0.2", "technical")

    called = {}

    class DummyTechnicalValidator:
        def __init__(self, file_path: str, strict: bool = True):
            called["file_path"] = file_path
            called["strict"] = strict

        def validate(self):
            return True, [], []

    class DummyModule:
        class technical_validator:
            TechnicalContainerValidator = DummyTechnicalValidator

    monkeypatch.setattr(container_validation, "load_version_module", lambda version: DummyModule)

    report = container_validation.validate_container(container_path)

    assert report.is_valid is True
    assert report.schema_version == "0.2"
    assert report.container_kind == "technical"
    assert called == {"file_path": str(container_path), "strict": False}


def test_validate_container_rejects_non_v0_2_metadata(tmp_path):
    container_path = tmp_path / "legacy.nxs.h5"
    _write_container(container_path, "0.1", "technical")

    with pytest.raises(ValueError, match="supports only 0.2"):
        container_validation.validate_container(container_path)
