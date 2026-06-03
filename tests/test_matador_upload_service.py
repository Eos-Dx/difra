from __future__ import annotations

from pathlib import Path

from difra.gui.matador_upload_api import (
    MatadorFindOrCreateSessionRequest,
    MatadorRegisterFileRequest,
    StubMatadorUploadApi,
)
from difra.gui.matador_upload_service import (
    MatadorUploadService,
    build_matador_upload_service,
)


def test_build_matador_upload_service_defaults_to_stub(monkeypatch):
    monkeypatch.delenv("MATADOR_URL", raising=False)
    monkeypatch.delenv("MATADOR_TOKEN", raising=False)

    service = build_matador_upload_service(config={})

    assert isinstance(service, MatadorUploadService)
    assert isinstance(service.api, StubMatadorUploadApi)
    assert service.backend_name == "StubMatadorUploadApi"


def test_build_matador_upload_service_accepts_injected_api():
    api = StubMatadorUploadApi(force_failure=False, failure_probability=0.0)

    service = build_matador_upload_service(api=api)

    assert service.api is api


def test_matador_upload_service_delegates_ingest_upload_flow(tmp_path: Path):
    payload_path = tmp_path / "payload.zip"
    payload_path.write_text("payload", encoding="utf-8")
    service = build_matador_upload_service(
        api=StubMatadorUploadApi(force_failure=False, failure_probability=0.0)
    )

    session = service.find_or_create_session(
        MatadorFindOrCreateSessionRequest(
            study_id=1701,
            machine_id=1751,
            distance_in_mm=170,
            exposure_time_sec=0.5,
            initiated_by="sad",
            session_date="2026-04-01",
        )
    )
    registered = service.register_file(
        MatadorRegisterFileRequest(
            ingest_session_id=session.id,
            file_name=payload_path.name,
            file_type="ZIP_PAYLOAD",
            ingest_kind="MEASUREMENT",
            detector_scope="PRIMARY",
            specimen_id=64101,
            expected_sha256=service.sha256_file(payload_path),
            expected_size_bytes=int(payload_path.stat().st_size),
        )
    )

    service.upload_file_bytes(registered.presigned_url, payload_path)
    status = service.get_file_status(registered.id)
    session_files = service.list_session_files(session.id)

    assert status.upload_status == "HASH_VERIFIED"
    assert status.processing_status == "HASH_VERIFIED_PENDING_ACCEPT"
    assert [file.id for file in session_files] == [registered.id]
