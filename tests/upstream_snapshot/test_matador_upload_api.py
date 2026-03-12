"""Unit tests for Matador API contract stub."""

from pathlib import Path

from difra.gui.matador_upload_api import (
    MatadorCreateSessionRequest,
    MatadorUploadContainerRequest,
    StubMatadorUploadApi,
    sha256_file,
)


def test_stub_create_session_returns_id():
    api = StubMatadorUploadApi(force_failure=False, failure_probability=0.0)
    response = api.create_session(
        MatadorCreateSessionRequest(
            username="sad",
            password="secret",
            operator_id="sad",
            workstation_id="DIFRA-01",
            client_version="0.2",
        )
    )
    assert response.success is True
    assert response.upload_session_id.startswith("upload_sad_")


def test_stub_upload_container_success_and_failure(tmp_path):
    container_path = tmp_path / "session_001.nxs.h5"
    container_path.write_text("stub payload")
    checksum = sha256_file(container_path)

    request = MatadorUploadContainerRequest(
        upload_session_id="upload_sad_20260309_120000",
        operator_id="sad",
        local_container_id="session_001",
        file_name=container_path.name,
        file_size_bytes=int(container_path.stat().st_size),
        file_sha256=checksum,
    )

    success_api = StubMatadorUploadApi(force_failure=False, failure_probability=0.0)
    success_response = success_api.upload_container(request, container_path=container_path)
    assert success_response.success is True
    assert success_response.received_sha256 == checksum

    failed_api = StubMatadorUploadApi(force_failure=True, failure_probability=0.0)
    failed_response = failed_api.upload_container(request, container_path=container_path)
    assert failed_response.success is False
