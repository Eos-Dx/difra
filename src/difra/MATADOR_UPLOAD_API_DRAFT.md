# Matador Upload API Draft (v1)

This draft defines the API shape DIFRA already follows through a local stub.
When Matador backend is ready, the stub can be replaced with a real HTTP client
without changing DIFRA workflow code.

## 1) Create Upload Session

`POST /api/v1/upload-sessions`

Request JSON:

```json
{
  "username": "operator_1",
  "password": "secret",
  "operator_id": "operator_1",
  "workstation_id": "DIFRA-01",
  "client_version": "0.2"
}
```

Response JSON:

```json
{
  "success": true,
  "upload_session_id": "upload_operator_1_20260309_101530",
  "message": "Session created",
  "issued_at": "2026-03-09 10:15:30",
  "expires_at": "2026-03-09 18:15:30"
}
```

## 2) Upload Container

`POST /api/v1/upload-sessions/{upload_session_id}/containers`

Multipart/form-data:

- `meta` (JSON string):

```json
{
  "operator_id": "operator_1",
  "local_container_id": "session_abc123",
  "file_name": "session_abc123.nxs.h5",
  "file_size_bytes": 1876543,
  "file_sha256": "5a4f..."
}
```

- `container_file`: binary `.nxs.h5`

Response JSON:

```json
{
  "success": true,
  "message": "Accepted",
  "upload_id": "upl_session_abc123_101533",
  "remote_container_id": "matador://upload_operator_1_20260309_101530/session_abc123",
  "received_sha256": "5a4f..."
}
```

## 3) Optional Session Status

`GET /api/v1/upload-sessions/{upload_session_id}`

Response can include per-container outcomes for audit/monitoring.

## 4) Local DIFRA Metadata Requirements

For each upload attempt DIFRA writes into local container:

- `uploaded_by`
- `upload_timestamp`
- `upload_session_id`
- `upload_status` (`success` / `failed`)
- `upload_result_message`
- `upload_bytes`
- `upload_local_checksum_sha256`
- `upload_response_checksum_sha256`
- `upload_remote_container_id`
- `upload_finished_at`
- `upload_attempts_log` (text log of attempts)

Transfer flag:

- `transfer_status=sent` only when upload is successful
- `transfer_status=unsent` when upload fails
