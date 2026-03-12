"""Matador upload API contract and stub implementation.

The goal of this module is to keep DIFRA upload flow stable while Matador
backend is not yet available. Workflow code depends only on this contract, so
switching to a real HTTP client later is a drop-in replacement.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import random
import time
from typing import Optional, Protocol


def sha256_file(path: Path) -> str:
    """Return SHA-256 for file content."""
    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        while True:
            chunk = file_handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_token(value: Optional[str], fallback: str = "unknown") -> str:
    token = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or "")
    ).strip("_")
    return token or fallback


@dataclass(frozen=True)
class MatadorCreateSessionRequest:
    """Login/session creation request."""

    username: str
    password: str
    operator_id: str
    workstation_id: str
    client_version: str


@dataclass(frozen=True)
class MatadorCreateSessionResponse:
    """Login/session creation response."""

    success: bool
    upload_session_id: str
    message: str
    issued_at: str
    expires_at: str


@dataclass(frozen=True)
class MatadorUploadContainerRequest:
    """Container upload request payload."""

    upload_session_id: str
    operator_id: str
    local_container_id: str
    file_name: str
    file_size_bytes: int
    file_sha256: str


@dataclass(frozen=True)
class MatadorUploadContainerResponse:
    """Container upload response payload."""

    success: bool
    message: str
    upload_id: str
    remote_container_id: str
    received_sha256: str


class MatadorUploadApi(Protocol):
    """Contract for Matador upload API implementations."""

    def create_session(
        self, request: MatadorCreateSessionRequest
    ) -> MatadorCreateSessionResponse:
        ...

    def upload_container(
        self, request: MatadorUploadContainerRequest, *, container_path: Path
    ) -> MatadorUploadContainerResponse:
        ...


class StubMatadorUploadApi:
    """Stub API used before real Matador backend is integrated."""

    def __init__(self, force_failure: bool = False, failure_probability: float = 0.0):
        self.force_failure = bool(force_failure)
        self.failure_probability = max(0.0, min(1.0, float(failure_probability)))

    def create_session(
        self, request: MatadorCreateSessionRequest
    ) -> MatadorCreateSessionResponse:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        # Keep a deterministic shape aligned with realistic backend output.
        session_id = (
            f"upload_{_safe_token(request.username)}_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        return MatadorCreateSessionResponse(
            success=True,
            upload_session_id=session_id,
            message="Matador session created (stub)",
            issued_at=now,
            expires_at="",
        )

    def upload_container(
        self, request: MatadorUploadContainerRequest, *, container_path: Path
    ) -> MatadorUploadContainerResponse:
        if self.force_failure:
            return MatadorUploadContainerResponse(
                success=False,
                message="Matador upload failed (stub)",
                upload_id="",
                remote_container_id="",
                received_sha256="",
            )
        if random.random() < self.failure_probability:
            return MatadorUploadContainerResponse(
                success=False,
                message="Matador upload temporary failure (stub)",
                upload_id="",
                remote_container_id="",
                received_sha256="",
            )

        upload_id = (
            f"upl_{_safe_token(request.local_container_id)}_{time.strftime('%H%M%S')}"
        )
        remote_container_id = (
            f"matador://{request.upload_session_id}/{_safe_token(request.local_container_id)}"
        )
        return MatadorUploadContainerResponse(
            success=True,
            message="Matador upload accepted (stub)",
            upload_id=upload_id,
            remote_container_id=remote_container_id,
            received_sha256=request.file_sha256,
        )


def build_matador_upload_api(config: Optional[dict] = None) -> MatadorUploadApi:
    """Return current Matador API backend implementation.

    TODO: replace with real HTTP client implementation once backend is available.
    """
    cfg = config or {}
    force_failure = bool(cfg.get("upload_stub_force_failure", False))
    failure_probability = cfg.get("upload_stub_failure_probability")
    if failure_probability is None:
        # Keep tests deterministic; runtime default follows requested behavior.
        failure_probability = 0.0 if os.environ.get("PYTEST_CURRENT_TEST") else 0.3
    return StubMatadorUploadApi(
        force_failure=force_failure,
        failure_probability=float(failure_probability),
    )
