"""Metadata-driven validation helpers for DIFRA v0.2 containers."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

import h5py

from container import loader
from container.registry import load_version_module


VALID_KINDS = {"session", "technical"}
SUPPORTED_SCHEMA_VERSION = "0.2"


@dataclass(frozen=True)
class ValidationMessage:
    """Normalized validation message."""

    severity: str
    path: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    """Normalized validation result."""

    file_path: str
    schema_version: str
    container_kind: str
    is_valid: bool
    messages: List[ValidationMessage]

    @property
    def errors(self) -> List[ValidationMessage]:
        return [item for item in self.messages if item.severity.upper() == "ERROR"]

    @property
    def warnings(self) -> List[ValidationMessage]:
        return [item for item in self.messages if item.severity.upper() != "ERROR"]


def detect_container_type(file_path: Union[str, Path]) -> str:
    """Detect whether a container is session or technical."""
    with h5py.File(file_path, "r") as file_handle:
        container_type = file_handle.attrs.get("container_type")
        if isinstance(container_type, bytes):
            container_type = container_type.decode("utf-8", errors="replace")
        container_type = str(container_type or "").strip().lower()
        if container_type in VALID_KINDS:
            return container_type

        # Fallback for partially populated files.
        if "/entry/measurements" in file_handle or "/measurements" in file_handle:
            return "session"
        if "/entry/technical" in file_handle or "/technical" in file_handle:
            return "technical"

    raise ValueError("Cannot detect container type; specify --kind explicitly")


def validate_container(
    file_path: Union[str, Path],
    container_kind: Optional[str] = None,
) -> ValidationReport:
    """Validate a DIFRA container using schema_version from metadata."""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Container not found: {file_path}")

    resolved_version = loader.detect_version(file_path)
    if resolved_version != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version in metadata: {resolved_version}. "
            f"This validator supports only {SUPPORTED_SCHEMA_VERSION}"
        )
    normalized_key = resolved_version.replace(".", "_")
    version_module = load_version_module(normalized_key)

    kind = (container_kind or "auto").strip().lower()
    if kind == "auto":
        kind = detect_container_type(file_path)
    if kind not in VALID_KINDS:
        raise ValueError(f"Unsupported container kind: {container_kind}")

    if kind == "session":
        validator = version_module.validator.SessionContainerValidator(file_path)
        is_valid, raw_messages = validator.validate()
        messages = [
            ValidationMessage(
                severity=str(getattr(item, "severity", "ERROR")),
                path=str(getattr(item, "path", "/")),
                message=str(getattr(item, "message", item)),
            )
            for item in raw_messages
        ]
    else:
        validator = version_module.technical_validator.TechnicalContainerValidator(
            str(file_path),
            strict=False,
        )
        is_valid, errors, warnings = validator.validate()
        messages = [ValidationMessage("ERROR", "/", str(item)) for item in errors]
        messages.extend(ValidationMessage("WARNING", "/", str(item)) for item in warnings)

    return ValidationReport(
        file_path=str(file_path),
        schema_version=resolved_version,
        container_kind=kind,
        is_valid=is_valid,
        messages=messages,
    )


def format_report(report: ValidationReport) -> str:
    """Render a human-readable validation report."""
    lines = [
        "=" * 70,
        "DIFRA Container Validation Report",
        "=" * 70,
        f"File: {report.file_path}",
        f"Schema Version: {report.schema_version}",
        f"Container Kind: {report.container_kind}",
        f"Status: {'VALID' if report.is_valid else 'INVALID'}",
    ]

    if report.messages:
        lines.append("")
        lines.append("Messages:")
        for index, item in enumerate(report.messages, 1):
            lines.append(f"  {index}. {item.severity} [{item.path}] {item.message}")

    lines.append("=" * 70)
    return "\n".join(lines)
