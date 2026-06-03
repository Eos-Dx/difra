from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    _normalize_iso_date,
    _safe_token,
    sha256_file,
)


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
            f"matador://{request.upload_session_id}/"
            f"{_safe_token(request.local_container_id)}"
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
        day_token = _normalize_iso_date(request.session_date) or time.strftime(
            "%Y-%m-%d"
        )
        existing = None
        for session in self._sessions.values():
            if (
                session["study_id"] == int(request.study_id)
                and session["machine_id"] == int(request.machine_id)
                and session["distance_in_mm"] == int(request.distance_in_mm)
                and float(session["exposure_time_sec"])
                == float(request.exposure_time_sec)
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

        return self._session_response(existing)

    def create_ingest_session(
        self, request: MatadorFindOrCreateSessionRequest
    ) -> MatadorIngestSessionResponse:
        day_token = _normalize_iso_date(request.session_date) or time.strftime(
            "%Y-%m-%d"
        )
        self._next_session_id += 1
        session_id = self._next_session_id
        payload = {
            "id": session_id,
            "session_token": (
                f"upload_{_safe_token(request.initiated_by)}_"
                f"{time.strftime('%Y%m%d_%H%M%S')}_{session_id}"
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
        self._sessions[session_id] = payload
        return self._session_response(payload)

    @staticmethod
    def _session_response(payload: Dict[str, Any]) -> MatadorIngestSessionResponse:
        return MatadorIngestSessionResponse(
            id=int(payload["id"]),
            session_token=str(payload["session_token"]),
            study_id=int(payload["study_id"]),
            machine_id=int(payload["machine_id"]),
            distance_in_mm=int(payload["distance_in_mm"]),
            exposure_time_sec=float(payload["exposure_time_sec"]),
            status=str(payload["status"]),
            initiated_by=str(payload["initiated_by"]),
            initiated_at=str(payload["initiated_at"]),
            expires_at=str(payload["expires_at"]),
        )

    def register_file(
        self, request: MatadorRegisterFileRequest
    ) -> MatadorRegisteredFileResponse:
        self._next_file_id += 1
        file_id = self._next_file_id
        session = self._sessions.get(int(request.ingest_session_id), {})
        s3_key = (
            f"ingest/session-id={request.ingest_session_id}/instrument-id="
            f"{session.get('machine_id', 'unknown')}/study-id="
            f"{session.get('study_id', 'unknown')}/"
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
            specimen_id=(
                None
                if payload.get("specimen_id") is None
                else int(payload.get("specimen_id"))
            ),
        )

    def get_specimen(self, specimen_id: int) -> Dict[str, Any]:
        return {
            "id": int(specimen_id),
            "study": {"id": 1701},
        }

    def list_session_files(
        self, ingest_session_id: int
    ) -> List[MatadorFileStatusResponse]:
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

    def list_specimens(
        self,
        *,
        project_id: Optional[int] = None,
        study_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        specimens = [
            {"id": 326111, "specimenId": "326111", "projectId": 1, "studyId": 1701},
            {"id": 326112, "specimenId": "326112", "projectId": 1, "studyId": 1701},
            {"id": 326113, "specimenId": "326113", "projectId": 2, "studyId": 1702},
        ]
        if project_id is not None:
            specimens = [
                item
                for item in specimens
                if int(item.get("projectId") or 0) == int(project_id)
            ]
        if study_id is not None:
            specimens = [
                item
                for item in specimens
                if int(item.get("studyId") or 0) == int(study_id)
            ]
        return specimens

    def list_ingest_sessions(
        self,
        *,
        study_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        sessions = []
        for payload in self._sessions.values():
            if study_id is not None and int(payload.get("study_id") or 0) != int(
                study_id
            ):
                continue
            sessions.append(
                {
                    "id": int(payload.get("id") or 0),
                    "studyId": int(payload.get("study_id") or 0),
                    "machineId": int(payload.get("machine_id") or 0),
                    "status": str(payload.get("status") or ""),
                }
            )
        return sessions
