from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


@dataclass(frozen=True)
class MatadorCreateSessionRequest:
    username: str
    password: str
    operator_id: str
    workstation_id: str
    client_version: str


@dataclass(frozen=True)
class MatadorCreateSessionResponse:
    success: bool
    upload_session_id: str
    message: str
    issued_at: str
    expires_at: str


@dataclass(frozen=True)
class MatadorUploadContainerRequest:
    upload_session_id: str
    operator_id: str
    local_container_id: str
    file_name: str
    file_size_bytes: int
    file_sha256: str


@dataclass(frozen=True)
class MatadorUploadContainerResponse:
    success: bool
    message: str
    upload_id: str
    remote_container_id: str
    received_sha256: str


@dataclass(frozen=True)
class MatadorFindOrCreateSessionRequest:
    study_id: int
    machine_id: int
    distance_in_mm: int
    exposure_time_sec: float
    initiated_by: str
    session_date: str = ""


@dataclass(frozen=True)
class MatadorIngestSessionResponse:
    id: int
    session_token: str
    study_id: int
    machine_id: int
    distance_in_mm: int
    exposure_time_sec: float
    status: str
    initiated_by: str
    initiated_at: str
    expires_at: str
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_comment: Optional[str] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class MatadorRegisterFileRequest:
    ingest_session_id: int
    file_name: str
    file_type: str
    ingest_kind: str
    detector_scope: str
    expected_sha256: str
    expected_size_bytes: int
    specimen_id: Optional[int] = None
    paired_file_id: Optional[int] = None
    upload_status: str = "PENDING"
    validation_status: str = "PENDING"
    processing_status: str = "NOT_STARTED"


@dataclass(frozen=True)
class MatadorRegisteredFileResponse:
    id: int
    ingest_session_id: int
    ingest_kind: str
    detector_scope: str
    file_name: str
    file_type: str
    specimen_id: Optional[int]
    paired_file_id: Optional[int]
    s3_key: str
    presigned_url: str
    upload_status: str
    processing_status: str
    manifest_presigned_url: str = ""


@dataclass(frozen=True)
class MatadorFileStatusResponse:
    id: int
    ingest_session_id: int
    file_name: str
    file_type: str
    upload_status: str
    processing_status: str
    validation_status: str = ""
    expected_sha256: str = ""
    actual_sha256: str = ""
    error_message: str = ""
    specimen_id: Optional[int] = None


class MatadorUploadApi(Protocol):
    def create_session(
        self, request: MatadorCreateSessionRequest
    ) -> MatadorCreateSessionResponse:
        ...

    def upload_container(
        self, request: MatadorUploadContainerRequest, *, container_path: Path
    ) -> MatadorUploadContainerResponse:
        ...

    def create_ingest_session(
        self, request: MatadorFindOrCreateSessionRequest
    ) -> MatadorIngestSessionResponse:
        ...

    def find_or_create_session(
        self, request: MatadorFindOrCreateSessionRequest
    ) -> MatadorIngestSessionResponse:
        ...

    def register_file(
        self, request: MatadorRegisterFileRequest
    ) -> MatadorRegisteredFileResponse:
        ...

    def upload_file_bytes(self, presigned_url: str, file_path: Path) -> None:
        ...

    def get_file_status(self, file_id: int) -> MatadorFileStatusResponse:
        ...

    def get_specimen(self, specimen_id: int) -> Dict[str, Any]:
        ...

    def list_session_files(
        self, ingest_session_id: int
    ) -> List[MatadorFileStatusResponse]:
        ...

    def list_studies(self) -> List[Dict[str, Any]]:
        ...

    def list_machines(self) -> List[Dict[str, Any]]:
        ...

    def list_specimens(
        self,
        *,
        project_id: Optional[int] = None,
        study_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        ...

    def list_ingest_sessions(
        self,
        *,
        study_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        ...
