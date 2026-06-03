from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from difra.gui.matador_upload_models import (
    MatadorCreateSessionRequest,
    MatadorCreateSessionResponse,
    MatadorFileStatusResponse,
    MatadorFindOrCreateSessionRequest,
    MatadorIngestSessionResponse,
    MatadorRegisterFileRequest,
    MatadorRegisteredFileResponse,
    MatadorUploadContainerRequest,
    MatadorUploadContainerResponse,
)
from difra.gui.matador_upload_utils import (
    _as_text,
    _normalize_iso_date,
    _strip_trailing_slash,
    normalize_matador_base_url,
    normalize_matador_token,
)


class RealMatadorUploadApi:
    """HTTP client for the real Matador ingest API."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_sec: float = 30.0,
    ):
        self.base_url = _strip_trailing_slash(base_url)
        self.base_url = normalize_matador_base_url(self.base_url)
        self.token = normalize_matador_token(token)
        self.timeout_sec = max(float(timeout_sec), 1.0)
        if not self.base_url:
            raise ValueError("Matador base URL is required")
        if not self.token:
            raise ValueError("Matador token is required")

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Any:
        full_path = str(path or "")
        if query:
            encoded = urllib_parse.urlencode(query, doseq=True)
            separator = "&" if "?" in full_path else "?"
            full_path = f"{full_path}{separator}{encoded}"
        url = f"{self.base_url}{full_path}"
        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib_request.Request(
            url=url,
            data=body,
            headers=headers,
            method=str(method or "GET").upper(),
        )
        try:
            with urllib_request.urlopen(request, timeout=self.timeout_sec) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Matador HTTP {exc.code} for {method} {path}: {body_text or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(
                f"Matador request failed for {method} {path}: {exc}"
            ) from exc

        if not raw.strip():
            return {}
        return json.loads(raw)

    @staticmethod
    def _coerce_collection(data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            content = data.get("content")
            if isinstance(content, list):
                return [item for item in content if isinstance(item, dict)]
        return []

    @staticmethod
    def _coerce_session(data: Dict[str, Any]) -> MatadorIngestSessionResponse:
        return MatadorIngestSessionResponse(
            id=int(data.get("id") or 0),
            session_token=_as_text(data.get("sessionToken")),
            study_id=int(data.get("studyId") or 0),
            machine_id=int(data.get("machineId") or 0),
            distance_in_mm=int(data.get("distanceInMm") or 0),
            exposure_time_sec=float(data.get("exposureTimeSec") or 0.0),
            status=_as_text(data.get("status")),
            initiated_by=_as_text(data.get("initiatedBy")),
            initiated_at=_as_text(data.get("initiatedAt")),
            expires_at=_as_text(data.get("expiresAt")),
            reviewed_by=data.get("reviewedBy"),
            reviewed_at=data.get("reviewedAt"),
            review_comment=data.get("reviewComment"),
            error_message=data.get("errorMessage"),
        )

    @staticmethod
    def _coerce_registered_file(data: Dict[str, Any]) -> MatadorRegisteredFileResponse:
        specimen = data.get("specimenId")
        paired = data.get("pairedFileId")
        return MatadorRegisteredFileResponse(
            id=int(data.get("id") or 0),
            ingest_session_id=int(data.get("ingestSessionId") or 0),
            ingest_kind=_as_text(data.get("ingestKind")),
            detector_scope=_as_text(data.get("detectorScope")),
            file_name=_as_text(data.get("fileName")),
            file_type=_as_text(data.get("fileType")),
            specimen_id=None if specimen is None else int(specimen),
            paired_file_id=None if paired is None else int(paired),
            s3_key=_as_text(data.get("s3Key")),
            presigned_url=_as_text(data.get("presignedUrl")),
            upload_status=_as_text(data.get("uploadStatus")),
            processing_status=_as_text(data.get("processingStatus")),
            manifest_presigned_url=_as_text(data.get("manifestPresignedUrl")),
        )

    @staticmethod
    def _coerce_status(data: Dict[str, Any]) -> MatadorFileStatusResponse:
        specimen = data.get("specimenId")
        return MatadorFileStatusResponse(
            id=int(data.get("id") or 0),
            ingest_session_id=int(data.get("ingestSessionId") or 0),
            file_name=_as_text(data.get("fileName")),
            file_type=_as_text(data.get("fileType")),
            upload_status=_as_text(data.get("uploadStatus")),
            processing_status=_as_text(data.get("processingStatus")),
            validation_status=_as_text(data.get("validationStatus")),
            expected_sha256=_as_text(data.get("expectedSha256")),
            actual_sha256=_as_text(data.get("actualSha256")),
            error_message=_as_text(data.get("errorMessage")),
            specimen_id=None if specimen is None else int(specimen),
        )

    def _request_paged_collection(
        self,
        *,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        page_size: int = 500,
        max_pages: int = 200,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for page in range(max(int(max_pages), 1)):
            page_query = dict(query or {})
            page_query.setdefault("page", page)
            page_query.setdefault("size", int(page_size))
            data = self._request_json(method="GET", path=path, query=page_query)
            page_items = self._coerce_collection(data)
            if not page_items:
                break
            items.extend(page_items)
            if len(page_items) < int(page_query["size"]):
                break
        return items

    def create_session(
        self, request: MatadorCreateSessionRequest
    ) -> MatadorCreateSessionResponse:
        raise NotImplementedError(
            "Legacy stub create_session is not supported in real mode"
        )

    def upload_container(
        self, request: MatadorUploadContainerRequest, *, container_path: Path
    ) -> MatadorUploadContainerResponse:
        raise NotImplementedError(
            "Legacy stub upload_container is not supported in real mode"
        )

    def find_or_create_session(
        self, request: MatadorFindOrCreateSessionRequest
    ) -> MatadorIngestSessionResponse:
        session_date = _normalize_iso_date(request.session_date)
        payload = {
            "studyId": int(request.study_id),
            "machineId": int(request.machine_id),
            "distanceInMm": int(request.distance_in_mm),
            "exposureTimeSec": float(request.exposure_time_sec),
            "initiatedBy": str(request.initiated_by),
        }
        data = self._request_json(
            method="POST",
            path="/api/ingest-sessions/find-or-create",
            payload=payload,
            query={"sessionDate": session_date} if session_date else None,
        )
        return self._coerce_session(data)

    def create_ingest_session(
        self, request: MatadorFindOrCreateSessionRequest
    ) -> MatadorIngestSessionResponse:
        session_date = _normalize_iso_date(request.session_date)
        payload = {
            "studyId": int(request.study_id),
            "machineId": int(request.machine_id),
            "distanceInMm": int(request.distance_in_mm),
            "exposureTimeSec": float(request.exposure_time_sec),
            "initiatedBy": str(request.initiated_by),
            "status": "ACTIVE",
            "initiatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "expiresAt": time.strftime("%Y-%m-%dT23:59:59Z", time.gmtime()),
        }
        if session_date:
            payload["sessionDate"] = session_date
        data = self._request_json(
            method="POST",
            path="/api/ingest-sessions",
            payload=payload,
        )
        return self._coerce_session(data)

    def register_file(
        self, request: MatadorRegisterFileRequest
    ) -> MatadorRegisteredFileResponse:
        payload: Dict[str, Any] = {
            "ingestSessionId": int(request.ingest_session_id),
            "fileName": str(request.file_name),
            "fileType": str(request.file_type),
            "ingestKind": str(request.ingest_kind),
            "detectorScope": str(request.detector_scope),
            "expectedSha256": str(request.expected_sha256),
            "expectedSizeBytes": int(request.expected_size_bytes),
            "uploadStatus": str(request.upload_status),
            "validationStatus": str(request.validation_status),
            "processingStatus": str(request.processing_status),
        }
        if request.specimen_id is not None:
            payload["specimenId"] = int(request.specimen_id)
        if request.paired_file_id is not None:
            payload["pairedFileId"] = int(request.paired_file_id)
        data = self._request_json(
            method="POST",
            path="/api/ingest-session-files",
            payload=payload,
        )
        return self._coerce_registered_file(data)

    def upload_file_bytes(self, presigned_url: str, file_path: Path) -> None:
        path = Path(file_path)
        body = path.read_bytes()
        request = urllib_request.Request(
            url=str(presigned_url),
            data=body,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(body)),
            },
            method="PUT",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.timeout_sec):
                return
        except urllib_error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"S3 upload failed with HTTP {exc.code}: {body_text or exc.reason}"
            ) from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"S3 upload failed: {exc}") from exc

    def get_file_status(self, file_id: int) -> MatadorFileStatusResponse:
        data = self._request_json(
            method="GET",
            path=f"/api/ingest-session-files/{int(file_id)}",
        )
        return self._coerce_status(data)

    def get_specimen(self, specimen_id: int) -> Dict[str, Any]:
        data = self._request_json(
            method="GET",
            path=f"/api/specimen/{int(specimen_id)}",
        )
        return data if isinstance(data, dict) else {}

    def list_session_files(
        self, ingest_session_id: int
    ) -> List[MatadorFileStatusResponse]:
        items = self._request_paged_collection(
            path="/api/ingest-session-files",
            query={
                "ingestSessionId.equals": int(ingest_session_id),
                "sort": "id,asc",
            },
        )
        return [self._coerce_status(item) for item in items]

    def list_studies(self) -> List[Dict[str, Any]]:
        items = self._request_paged_collection(
            path="/api/studies",
            query={
                "sort": "id,asc",
                "eagerload": "true",
            },
        )
        studies = []
        for item in items:
            project = item.get("project") if isinstance(item.get("project"), dict) else {}
            studies.append(
                {
                    "id": int(item.get("id") or 0),
                    "name": _as_text(item.get("name")),
                    "projectId": (
                        None if project.get("id") is None else int(project.get("id"))
                    ),
                    "projectName": _as_text(project.get("name")),
                }
            )
        return studies

    def list_machines(self) -> List[Dict[str, Any]]:
        items = self._request_paged_collection(
            path="/api/machines",
            query={
                "sort": "id,asc",
            },
        )
        return [
            {
                "id": int(item.get("id") or 0),
                "name": _as_text(item.get("machineName") or item.get("name")),
            }
            for item in items
        ]

    def list_specimens(
        self,
        *,
        project_id: Optional[int] = None,
        study_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {
            "sort": "id,asc",
            "eagerload": "true",
        }
        items = self._request_paged_collection(path="/api/specimen", query=query)
        study_project_map: Dict[int, int] = {}
        if project_id is not None:
            for study in self.list_studies():
                candidate_project_id = study.get("projectId")
                candidate_study_id = study.get("id")
                if candidate_project_id is None or candidate_study_id is None:
                    continue
                study_project_map[int(candidate_study_id)] = int(candidate_project_id)
        specimens = []
        for item in items:
            project = item.get("project") if isinstance(item.get("project"), dict) else {}
            study = item.get("study") if isinstance(item.get("study"), dict) else {}
            item_project_id = item.get("projectId", project.get("id"))
            item_study_id = item.get("studyId", study.get("id"))
            if item_project_id is None and item_study_id is not None:
                item_project_id = study_project_map.get(int(item_study_id))
            if project_id is not None and int(item_project_id or 0) != int(project_id):
                continue
            if study_id is not None and int(item_study_id or 0) != int(study_id):
                continue
            specimens.append(
                {
                    "id": int(item.get("id") or 0),
                    "specimenId": _as_text(
                        item.get("id")
                        or item.get("specimenId")
                        or item.get("specimenCode")
                        or item.get("externalId")
                    ),
                    "externalId": _as_text(item.get("externalId")),
                    "projectId": (
                        None if item_project_id is None else int(item_project_id)
                    ),
                    "projectName": _as_text(
                        item.get("projectName") or project.get("name")
                    ),
                    "studyId": None if item_study_id is None else int(item_study_id),
                    "studyName": _as_text(item.get("studyName") or study.get("name")),
                }
            )
        return specimens

    def list_ingest_sessions(
        self,
        *,
        study_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {
            "sort": "id,asc",
            "eagerload": "true",
        }
        if study_id is not None:
            query["studyId.equals"] = int(study_id)
        items = self._request_paged_collection(path="/api/ingest-sessions", query=query)
        sessions = []
        for item in items:
            study = item.get("study") if isinstance(item.get("study"), dict) else {}
            item_study_id = item.get("studyId", study.get("id"))
            sessions.append(
                {
                    "id": int(item.get("id") or 0),
                    "studyId": None if item_study_id is None else int(item_study_id),
                    "machineId": int(item.get("machineId") or 0),
                    "status": _as_text(item.get("status")),
                }
            )
        return sessions
