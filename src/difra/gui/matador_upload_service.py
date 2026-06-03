from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from difra.gui.matador_upload_api import (
    MatadorCreateSessionRequest,
    MatadorCreateSessionResponse,
    MatadorFileStatusResponse,
    MatadorFindOrCreateSessionRequest,
    MatadorIngestSessionResponse,
    MatadorRegisterFileRequest,
    MatadorRegisteredFileResponse,
    MatadorUploadApi,
    MatadorUploadContainerRequest,
    MatadorUploadContainerResponse,
    build_matador_upload_api,
    sha256_file,
)


@dataclass(frozen=True)
class MatadorUploadService:
    """GUI-free facade over the active Matador upload backend."""

    api: MatadorUploadApi

    @property
    def backend_name(self) -> str:
        return type(self.api).__name__

    def sha256_file(self, path: Path) -> str:
        return sha256_file(path)

    def create_session(
        self, request: MatadorCreateSessionRequest
    ) -> MatadorCreateSessionResponse:
        return self.api.create_session(request)

    def upload_container(
        self, request: MatadorUploadContainerRequest, *, container_path: Path
    ) -> MatadorUploadContainerResponse:
        return self.api.upload_container(request, container_path=container_path)

    def create_ingest_session(
        self, request: MatadorFindOrCreateSessionRequest
    ) -> MatadorIngestSessionResponse:
        return self.api.create_ingest_session(request)

    def find_or_create_session(
        self, request: MatadorFindOrCreateSessionRequest
    ) -> MatadorIngestSessionResponse:
        return self.api.find_or_create_session(request)

    def register_file(
        self, request: MatadorRegisterFileRequest
    ) -> MatadorRegisteredFileResponse:
        return self.api.register_file(request)

    def upload_file_bytes(self, presigned_url: str, file_path: Path) -> None:
        self.api.upload_file_bytes(presigned_url, file_path)

    def get_file_status(self, file_id: int) -> MatadorFileStatusResponse:
        return self.api.get_file_status(file_id)

    def get_specimen(self, specimen_id: int) -> Dict[str, Any]:
        return self.api.get_specimen(specimen_id)

    def list_session_files(self, ingest_session_id: int) -> List[MatadorFileStatusResponse]:
        return self.api.list_session_files(ingest_session_id)

    def list_studies(self) -> List[Dict[str, Any]]:
        return self.api.list_studies()

    def list_machines(self) -> List[Dict[str, Any]]:
        return self.api.list_machines()

    def list_specimens(
        self,
        *,
        project_id: Optional[int] = None,
        study_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self.api.list_specimens(project_id=project_id, study_id=study_id)

    def list_ingest_sessions(
        self,
        *,
        study_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return self.api.list_ingest_sessions(study_id=study_id)


def build_matador_upload_service(
    config: Optional[dict] = None,
    *,
    api: Optional[MatadorUploadApi] = None,
) -> MatadorUploadService:
    return MatadorUploadService(api=api or build_matador_upload_api(config=config))
