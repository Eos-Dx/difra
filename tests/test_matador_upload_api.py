from __future__ import annotations

from pathlib import Path

from difra.gui.matador_upload_api import (
    MatadorFindOrCreateSessionRequest,
    MatadorRegisterFileRequest,
    RealMatadorUploadApi,
    StubMatadorUploadApi,
    build_matador_upload_api,
    load_matador_reference_cache,
    normalize_matador_base_url,
    normalize_matador_token,
    refresh_matador_reference_cache,
    save_matador_reference_cache,
    sha256_file,
)


def test_build_matador_upload_api_defaults_to_stub(monkeypatch):
    monkeypatch.delenv("MATADOR_URL", raising=False)
    monkeypatch.delenv("MATADOR_TOKEN", raising=False)

    api = build_matador_upload_api(config={})

    assert isinstance(api, StubMatadorUploadApi)


def test_build_matador_upload_api_uses_real_client_when_env_present(monkeypatch):
    monkeypatch.setenv("MATADOR_URL", "https://dev-gamma.matur.co.uk")
    monkeypatch.setenv("MATADOR_TOKEN", "token-value")

    api = build_matador_upload_api(config={})

    assert isinstance(api, RealMatadorUploadApi)
    assert api.base_url == "https://dev-gamma.matur.co.uk"
    assert api.token == "token-value"


def test_normalize_matador_base_url_accepts_page_urls():
    assert (
        normalize_matador_base_url("https://dev-gamma.matur.co.uk/analytics/studies")
        == "https://dev-gamma.matur.co.uk"
    )
    assert (
        normalize_matador_base_url('"https://dev-gamma.matur.co.uk/difra-api-token"')
        == "https://dev-gamma.matur.co.uk"
    )


def test_normalize_matador_token_strips_quotes_and_bearer_prefix():
    assert normalize_matador_token('"abc.def.ghi"') == "abc.def.ghi"
    assert normalize_matador_token("Bearer abc.def.ghi") == "abc.def.ghi"


def test_stub_ingest_flow_hash_verifies_uploaded_file(tmp_path: Path):
    payload_path = tmp_path / "payload.zip"
    payload_path.write_text("payload", encoding="utf-8")

    api = StubMatadorUploadApi(force_failure=False, failure_probability=0.0)
    session = api.find_or_create_session(
        MatadorFindOrCreateSessionRequest(
            study_id=1701,
            machine_id=1751,
            distance_in_mm=170,
            exposure_time_sec=0.5,
            initiated_by="sad",
        )
    )
    registered = api.register_file(
        MatadorRegisterFileRequest(
            ingest_session_id=session.id,
            file_name=payload_path.name,
            file_type="ZIP_PAYLOAD",
            ingest_kind="MEASUREMENT",
            detector_scope="PRIMARY",
            specimen_id=64101,
            expected_sha256=sha256_file(payload_path),
            expected_size_bytes=int(payload_path.stat().st_size),
        )
    )

    api.upload_file_bytes(registered.presigned_url, payload_path)
    status = api.get_file_status(registered.id)

    assert registered.upload_status == "URL_ISSUED"
    assert status.upload_status == "HASH_VERIFIED"
    assert status.processing_status == "HASH_VERIFIED_PENDING_ACCEPT"


def test_stub_find_or_create_session_uses_session_date_bucket():
    api = StubMatadorUploadApi(force_failure=False, failure_probability=0.0)

    first = api.find_or_create_session(
        MatadorFindOrCreateSessionRequest(
            study_id=1701,
            machine_id=1751,
            distance_in_mm=170,
            exposure_time_sec=0.5,
            initiated_by="sad",
            session_date="2026-04-01",
        )
    )
    same_bucket = api.find_or_create_session(
        MatadorFindOrCreateSessionRequest(
            study_id=1701,
            machine_id=1751,
            distance_in_mm=170,
            exposure_time_sec=0.5,
            initiated_by="sad",
            session_date="2026-04-01",
        )
    )
    different_day = api.find_or_create_session(
        MatadorFindOrCreateSessionRequest(
            study_id=1701,
            machine_id=1751,
            distance_in_mm=170,
            exposure_time_sec=0.5,
            initiated_by="sad",
            session_date="2026-04-02",
        )
    )

    assert same_bucket.id == first.id
    assert different_day.id != first.id


def test_real_client_find_or_create_session_sends_session_date_query(monkeypatch):
    captured = {}

    def _fake_request_json(self, *, method, path, payload=None, query=None):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        captured["query"] = query
        return {
            "id": 42,
            "sessionToken": "token-42",
            "studyId": payload["studyId"],
            "machineId": payload["machineId"],
            "distanceInMm": payload["distanceInMm"],
            "exposureTimeSec": payload["exposureTimeSec"],
            "status": "ACTIVE",
            "initiatedBy": payload["initiatedBy"],
            "initiatedAt": "2026-04-01T10:00:00Z",
            "expiresAt": "2026-04-01T23:59:59Z",
        }

    monkeypatch.setattr(RealMatadorUploadApi, "_request_json", _fake_request_json)
    api = RealMatadorUploadApi(base_url="https://dev-gamma.matur.co.uk", token="token-value")

    response = api.find_or_create_session(
        MatadorFindOrCreateSessionRequest(
            study_id=1701,
            machine_id=1751,
            distance_in_mm=170,
            exposure_time_sec=0.5,
            initiated_by="sad",
            session_date="2026-04-01",
        )
    )

    assert response.id == 42
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/ingest-sessions/find-or-create"
    assert captured["query"] == {"sessionDate": "2026-04-01"}


def test_save_and_load_matador_reference_cache_roundtrip(tmp_path: Path):
    cache_path = tmp_path / "matador_cache.json"

    save_matador_reference_cache(
        studies=[{"id": 1701, "name": "Horizon_Grant1", "projectId": 11, "projectName": "Horizon"}],
        machines=[{"id": 1751, "name": "MOLI"}],
        cache_path=cache_path,
    )

    payload = load_matador_reference_cache(cache_path)

    assert payload["studies"][0]["id"] == 1701
    assert payload["studies"][0]["projectName"] == "Horizon"
    assert payload["machines"][0]["name"] == "MOLI"
    assert payload["savedAt"]


def test_refresh_matador_reference_cache_uses_real_client_and_writes_cache(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(
        RealMatadorUploadApi,
        "list_studies",
        lambda self: [
            {"id": 1701, "name": "Keele_Grant2", "projectId": 21, "projectName": "Keele"}
        ],
    )
    monkeypatch.setattr(
        RealMatadorUploadApi,
        "list_machines",
        lambda self: [{"id": 1751, "name": "MOLI"}],
    )

    payload = refresh_matador_reference_cache(
        base_url="https://dev-gamma.matur.co.uk",
        token="runtime-token",
        cache_path=tmp_path / "matador_cache.json",
    )

    assert payload["studies"][0]["name"] == "Keele_Grant2"
    assert payload["machines"][0]["id"] == 1751
    cached = load_matador_reference_cache(tmp_path / "matador_cache.json")
    assert cached["studies"][0]["projectId"] == 21
