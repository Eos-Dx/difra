# Matador Upload and Resend Runbook

This runbook explains current Matador send/resend behavior and recovery paths.

## Core Concepts

Matador upload uses two logical payloads:

1. calibration ZIP
2. session/measurement ZIP and H5 file

DiFRA groups uploads by technical container ID so sessions sharing the same calibration reuse the same calibration upload in a batch.

## Upload API Sequence

Code:

- `src/difra/gui/matador_upload_api.py`
- `src/difra/gui/matador_upload_service.py`
- `src/difra/gui/session_lifecycle_upload_execute_mixin.py`
- `src/difra/gui/session_reupload_service.py`

Flow:

```text
resolve metadata
resolve Matador specimen ID
find or create ingest session
register calibration ZIP if needed
upload calibration ZIP bytes
poll calibration file status
register session ZIP
upload session ZIP bytes
register H5 file
upload H5 bytes
write upload metadata into H5
mark pending or sent
```

Matador file status examples:

```text
URL_ISSUED
HASH_VERIFIED
FAILED
```

Local pending status:

```text
pending_verification
```

## Polling

Default:

```text
attempts = 24
delay = 5 sec
```

Reason: Matador can accept bytes but update file status slowly.

## Resend Rules

Code:

- `src/difra/gui/session_reupload_service.py`

Rules:

- Missing session file fails.
- `not_complete` fails and is not sent.
- `req_resend` can be sent.
- Existing `sent` can be sent again when user explicitly requests.
- If upload is pending, result increments `upload_pending`.
- If upload succeeds and metadata writes succeed, status can become sent.

## REQ_RESEND Repair Flow

Use when session data is valid but embedded technical calibration was wrong.

Code:

- `src/difra/gui/session_technical_rewrite_service.py`

Flow:

```text
correct technical PONI
validate technical container
match sessions by technical container ID
backup each session as .h5old/.h5old2
replace embedded technical section
update meta_json and sidecar state JSON
mark transfer_status=req_resend
ask Matador/DB team to deactivate previous measurements
resend sessions
verify Matador accepted new files
```

## Deactivation Data Needed For Matador/DB

When already-uploaded measurements must be deactivated, provide:

```text
resolved Matador specimen ID
specimen text
created timestamp
operator ID
previous Matador upload session ID
previous Matador ZIP file ID
previous Matador H5 file ID
previous Matador send timestamp
```

Use old Matador-resolved specimen ID where available to avoid changing DB entities unnecessarily.

## Common Failures

### Specimen not found

Example:

```text
Matador HTTP 500 ... Specimen not found: <id>
```

Meaning:

- DiFRA selected/resolved a specimen ID not present in Matador for this project/study.

Action:

- Do not force send.
- Check both numeric parts from specimen text.
- Verify `/api/specimen/{id}` if token available.
- Verify returned `study_id` matches session study.
- Fix specimen mapping before resend.

### Calibration uploaded but sessions pending

Meaning:

- Matador file bytes are uploaded, but file status has not caught up.

Action:

- Keep DiFRA/archive open if relying on background polling.
- Reopen archive to trigger pending verification check.
- Do not manually mark sent unless status is verified.

### SMTP host not configured

Meaning:

- Error email/report sending is not configured.
- Matador upload itself may still have happened.

Action:

- Configure daily report/error email credentials.
- Do not treat this as proof upload failed.

### Old-format ZIP not generated

Meaning:

- exporter could not build Matador legacy payload.

Action:

- Inspect old-format export errors.
- Check technical section and raw files.
- Do not upload H5-only to real Matador unless explicitly using stub/test mode.

## Operator-Facing Archive Behavior

Archive status filter must include:

```text
All statuses
Unsent
REQ_RESEND
Sent
Not complete
```

Archive status filter must not include durable statuses:

```text
Pending
Failed
```

Those are upload attempt conditions, not archive transfer states.

Tests:

- `tests/test_session_transfer_status.py`
- `tests/upstream_snapshot/test_gui_session_send_queue.py`

## Verification Commands

Run core tests:

```bash
conda run -n eosdx13 python -m pytest \
  tests/test_matador_upload_api.py \
  tests/test_matador_upload_service.py \
  tests/test_session_reupload_service.py \
  tests/test_session_technical_rewrite_service.py \
  tests/upstream_snapshot/test_session_lifecycle_actions.py
```

Run full suite:

```bash
conda run -n eosdx13 python -m pytest
```
