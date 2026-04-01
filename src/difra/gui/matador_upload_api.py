"""Matador upload API helpers.

This module now supports the real Matador ingest contract while keeping a stub
fallback for local development and tests when `MATADOR_URL` / `MATADOR_TOKEN`
are not configured.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import random
import time
from typing import Any, Dict, List, Optional, Protocol
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

_DEFAULT_MATADOR_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "config" / "matador_cache.json"
)


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


def _strip_trailing_slash(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def _strip_wrapping_quotes(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1].strip()
    return text


def normalize_matador_base_url(value: str) -> str:
    """Normalize Matador URL to origin-only form so pasted page URLs still work."""
    text = _strip_wrapping_quotes(value)
    if not text:
        return ""
    parsed = urllib_parse.urlparse(text)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return _strip_trailing_slash(text)


def normalize_matador_token(value: str) -> str:
    """Normalize Matador JWT pasted from shell exports or quoted snippets."""
    text = _strip_wrapping_quotes(value)
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def default_matador_cache_path() -> Path:
    """Return the JSON cache used for Matador studies/machines."""
    return _DEFAULT_MATADOR_CACHE_PATH


def load_matador_reference_cache(cache_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load locally cached Matador studies/machines."""
    path = Path(cache_path or default_matador_cache_path())
    if not path.exists():
        return {"studies": [], "machines": [], "savedAt": ""}
    with open(path, "r", encoding="utf-8") as file_handle:
        payload = json.load(file_handle)
    studies = payload.get("studies")
    machines = payload.get("machines")
    return {
        "studies": studies if isinstance(studies, list) else [],
        "machines": machines if isinstance(machines, list) else [],
        "savedAt": _as_text(payload.get("savedAt")),
    }


def save_matador_reference_cache(
    *,
    studies: List[Dict[str, Any]],
    machines: List[Dict[str, Any]],
    cache_path: Optional[Path] = None,
) -> Path:
    """Persist Matador studies/machines locally for offline use."""
    path = Path(cache_path or default_matador_cache_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "studies": studies,
        "machines": machines,
    }
    with open(path, "w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, indent=2, ensure_ascii=False)
    return path


def refresh_matador_reference_cache(
    *,
    base_url: str,
    token: str,
    cache_path: Optional[Path] = None,
    timeout_sec: float = 30.0,
) -> Dict[str, Any]:
    """Fetch Matador studies/machines from API and update the local cache."""
    api = RealMatadorUploadApi(
        base_url=normalize_matador_base_url(base_url),
        token=normalize_matador_token(token),
        timeout_sec=timeout_sec,
    )
    studies = api.list_studies()
    machines = api.list_machines()
    saved_path = save_matador_reference_cache(
        studies=studies,
        machines=machines,
        cache_path=cache_path,
    )
    return {
        "studies": studies,
        "machines": machines,
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cachePath": str(saved_path),
    }


@dataclass(frozen=True)
class MatadorCreateSessionRequest:
    """Legacy stub request retained for compatibility."""

    username: str
    password: str
    operator_id: str
    workstation_id: str
    client_version: str


@dataclass(frozen=True)
class MatadorCreateSessionResponse:
    """Legacy stub response retained for compatibility."""

    success: bool
    upload_session_id: str
    message: str
    issued_at: str
    expires_at: str


@dataclass(frozen=True)
class MatadorUploadContainerRequest:
    """Legacy stub request retained for compatibility."""

    upload_session_id: str
    operator_id: str
    local_container_id: str
    file_name: str
    file_size_bytes: int
    file_sha256: str


@dataclass(frozen=True)
class MatadorUploadContainerResponse:
    """Legacy stub response retained for compatibility."""

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


class MatadorUploadApi(Protocol):
    """Contract for Matador upload API implementations."""

    # Legacy stub methods.
    def create_session(
        self, request: MatadorCreateSessionRequest
    ) -> MatadorCreateSessionResponse:
        ...

    def upload_container(
        self, request: MatadorUploadContainerRequest, *, container_path: Path
    ) -> MatadorUploadContainerResponse:
        ...

    # Real ingest contract.
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

    def list_session_files(self, ingest_session_id: int) -> List[MatadorFileStatusResponse]:
        ...

    def list_studies(self) -> List[Dict[str, Any]]:
        ...

    def list_machines(self) -> List[Dict[str, Any]]:
        ...


class StubMatadorUploadApi:
    """Stub API used when real Matador credentials are not configured."""

    def __init__(self, force_failure: bool = False, failure_probability: float = 0.0):
        self.force_failure = bool(force_failure)
        self.failure_probability = max(0.0, min(1.0, float(failure_probability)))
        self._next_session_id = 68000
        self._next_file_id = 68600
        self._sessions: Dict[int, Dict[str, Any]] = {}
        self._files: Dict[int, Dict[str, Any]] = {}

    def _should_fail(self) -> bool:
        return self.force_failure or random.random() < self.failure_probability

    def create_session(
        self, request: MatadorCreateSessionRequest
    ) -> MatadorCreateSessionResponse:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
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
        if self._should_fail():
            return MatadorUploadContainerResponse(
                success=False,
                message="Matador upload failed (stub)",
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

    def find_or_create_session(
        self, request: MatadorFindOrCreateSessionRequest
    ) -> MatadorIngestSessionResponse:
        day_token = time.strftime("%Y%m%d")
        existing = None
        for session in self._sessions.values():
            if (
                session["study_id"] == int(request.study_id)
                and session["machine_id"] == int(request.machine_id)
                and session["distance_in_mm"] == int(request.distance_in_mm)
                and float(session["exposure_time_sec"]) == float(request.exposure_time_sec)
                and session["day_token"] == day_token
            ):
                existing = session
                break

        if existing is None:
            self._next_session_id += 1
            session_id = self._next_session_id
            existing = {
                "id": session_id,
                "session_token": (
                    f"upload_{_safe_token(request.initiated_by)}_"
                    f"{time.strftime('%Y%m%d_%H%M%S')}"
                ),
                "study_id": int(request.study_id),
                "machine_id": int(request.machine_id),
                "distance_in_mm": int(request.distance_in_mm),
                "exposure_time_sec": float(request.exposure_time_sec),
                "status": "ACTIVE",
                "initiated_by": request.initiated_by,
                "initiated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expires_at": time.strftime("%Y-%m-%dT23:59:59Z"),
                "day_token": day_token,
            }
            self._sessions[session_id] = existing

        return MatadorIngestSessionResponse(
            id=int(existing["id"]),
            session_token=str(existing["session_token"]),
            study_id=int(existing["study_id"]),
            machine_id=int(existing["machine_id"]),
            distance_in_mm=int(existing["distance_in_mm"]),
            exposure_time_sec=float(existing["exposure_time_sec"]),
            status=str(existing["status"]),
            initiated_by=str(existing["initiated_by"]),
            initiated_at=str(existing["initiated_at"]),
            expires_at=str(existing["expires_at"]),
        )

    def register_file(
        self, request: MatadorRegisterFileRequest
    ) -> MatadorRegisteredFileResponse:
        self._next_file_id += 1
        file_id = self._next_file_id
        session = self._sessions.get(int(request.ingest_session_id), {})
        s3_key = (
            f"ingest/session-id={request.ingest_session_id}/instrument-id="
            f"{session.get('machine_id', 'unknown')}/study-id={session.get('study_id', 'unknown')}/"
            f"{_safe_token(request.file_name, 'payload')}"
        )
        payload = {
            "id": file_id,
            "ingest_session_id": int(request.ingest_session_id),
            "file_name": request.file_name,
            "file_type": request.file_type,
            "ingest_kind": request.ingest_kind,
            "detector_scope": request.detector_scope,
            "specimen_id": request.specimen_id,
            "paired_file_id": request.paired_file_id,
            "s3_key": s3_key,
            "presigned_url": f"stub://upload/{file_id}",
            "upload_status": "URL_ISSUED",
            "processing_status": "NOT_STARTED",
            "validation_status": request.validation_status,
            "expected_sha256": request.expected_sha256,
            "actual_sha256": "",
            "error_message": "",
        }
        self._files[file_id] = payload
        return MatadorRegisteredFileResponse(
            id=file_id,
            ingest_session_id=int(request.ingest_session_id),
            ingest_kind=request.ingest_kind,
            detector_scope=request.detector_scope,
            file_name=request.file_name,
            file_type=request.file_type,
            specimen_id=request.specimen_id,
            paired_file_id=request.paired_file_id,
            s3_key=s3_key,
            presigned_url=str(payload["presigned_url"]),
            upload_status="URL_ISSUED",
            processing_status="NOT_STARTED",
            manifest_presigned_url="",
        )

    def upload_file_bytes(self, presigned_url: str, file_path: Path) -> None:
        file_id = int(str(presigned_url).rsplit("/", 1)[-1])
        payload = self._files[file_id]
        payload["actual_sha256"] = sha256_file(Path(file_path))
        if self._should_fail():
            payload["upload_status"] = "FAILED"
            payload["processing_status"] = "FAILED"
            payload["error_message"] = "Matador upload failed (stub)"
            return
        payload["upload_status"] = "HASH_VERIFIED"
        payload["processing_status"] = "HASH_VERIFIED_PENDING_ACCEPT"

    def get_file_status(self, file_id: int) -> MatadorFileStatusResponse:
        payload = self._files[int(file_id)]
        return MatadorFileStatusResponse(
            id=int(payload["id"]),
            ingest_session_id=int(payload["ingest_session_id"]),
            file_name=str(payload["file_name"]),
            file_type=str(payload["file_type"]),
            upload_status=str(payload["upload_status"]),
            processing_status=str(payload["processing_status"]),
            validation_status=str(payload.get("validation_status", "")),
            expected_sha256=str(payload.get("expected_sha256", "")),
            actual_sha256=str(payload.get("actual_sha256", "")),
            error_message=str(payload.get("error_message", "")),
        )

    def list_session_files(self, ingest_session_id: int) -> List[MatadorFileStatusResponse]:
        files = []
        for payload in self._files.values():
            if int(payload["ingest_session_id"]) != int(ingest_session_id):
                continue
            files.append(self.get_file_status(int(payload["id"])))
        return files

    def list_studies(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": 1701,
                "name": "Keele_Grant2",
                "projectId": 1,
                "projectName": "Keele",
            },
            {
                "id": 1702,
                "name": "Ulster_Grant3",
                "projectId": 2,
                "projectName": "Ulster",
            },
        ]

    def list_machines(self) -> List[Dict[str, Any]]:
        return [
            {"id": 1751, "name": "MOLI"},
            {"id": 1752, "name": "SILVER_1"},
        ]


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
            raise RuntimeError(f"Matador request failed for {method} {path}: {exc}") from exc

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
        )

    def create_session(
        self, request: MatadorCreateSessionRequest
    ) -> MatadorCreateSessionResponse:
        raise NotImplementedError("Legacy stub create_session is not supported in real mode")

    def upload_container(
        self, request: MatadorUploadContainerRequest, *, container_path: Path
    ) -> MatadorUploadContainerResponse:
        raise NotImplementedError("Legacy stub upload_container is not supported in real mode")

    def find_or_create_session(
        self, request: MatadorFindOrCreateSessionRequest
    ) -> MatadorIngestSessionResponse:
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

    def list_session_files(self, ingest_session_id: int) -> List[MatadorFileStatusResponse]:
        data = self._request_json(
            method="GET",
            path="/api/ingest-session-files",
            query={
                "ingestSessionId.equals": int(ingest_session_id),
                "size": 100,
            },
        )
        if not isinstance(data, list):
            return []
        return [self._coerce_status(item) for item in data if isinstance(item, dict)]

    def list_studies(self) -> List[Dict[str, Any]]:
        data = self._request_json(
            method="GET",
            path="/api/studies",
            query={
                "page": 0,
                "size": 500,
                "sort": "id,asc",
                "eagerload": "true",
            },
        )
        studies = []
        for item in self._coerce_collection(data):
            project = item.get("project") if isinstance(item.get("project"), dict) else {}
            studies.append(
                {
                    "id": int(item.get("id") or 0),
                    "name": _as_text(item.get("name")),
                    "projectId": (
                        None
                        if project.get("id") is None
                        else int(project.get("id"))
                    ),
                    "projectName": _as_text(project.get("name")),
                }
            )
        return studies

    def list_machines(self) -> List[Dict[str, Any]]:
        data = self._request_json(
            method="GET",
            path="/api/machines",
            query={
                "page": 0,
                "size": 200,
                "sort": "id,asc",
            },
        )
        machines = []
        for item in self._coerce_collection(data):
            machines.append(
                {
                    "id": int(item.get("id") or 0),
                    "name": _as_text(item.get("machineName") or item.get("name")),
                }
            )
        return machines


def build_matador_upload_api(config: Optional[dict] = None) -> MatadorUploadApi:
    """Return the active Matador upload client.

    When `MATADOR_URL` and `MATADOR_TOKEN` are configured, use the real client.
    Otherwise keep the stub so local workflows and tests remain deterministic.
    """

    cfg = config or {}
    base_url = _strip_trailing_slash(
        _as_text(cfg.get("matador_url") or os.environ.get("MATADOR_URL"), "")
    )
    base_url = normalize_matador_base_url(base_url)
    token = normalize_matador_token(
        _as_text(cfg.get("matador_token") or os.environ.get("MATADOR_TOKEN"), "")
    )

    if base_url and token and not bool(cfg.get("matador_force_stub", False)):
        timeout_sec = cfg.get("matador_timeout_sec")
        if timeout_sec is None:
            timeout_sec = 30.0
        return RealMatadorUploadApi(
            base_url=base_url,
            token=token,
            timeout_sec=float(timeout_sec),
        )

    force_failure = bool(cfg.get("upload_stub_force_failure", False))
    failure_probability = cfg.get("upload_stub_failure_probability")
    if failure_probability is None:
        failure_probability = 0.0 if os.environ.get("PYTEST_CURRENT_TEST") else 0.3
    return StubMatadorUploadApi(
        force_failure=force_failure,
        failure_probability=float(failure_probability),
    )
