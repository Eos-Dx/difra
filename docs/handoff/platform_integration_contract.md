# Platform Integration Contract

This is the practical contract a non-Qt platform should implement around current DiFRA business logic.

## Principle

The platform should not call Qt widgets or GUI mixins. It should call services or equivalent rewritten platform services.

## Contract 1: Matador Upload Backend

Current service:

```python
from difra.gui.matador_upload_service import build_matador_upload_service
```

Build:

```python
service = build_matador_upload_service(
    {
        "matador_url": "https://portal.matur.co.uk",
        "matador_token": "...",
        "matador_force_stub": False,
    }
)
```

Required operations:

- `find_or_create_session(MatadorFindOrCreateSessionRequest)`
- `register_file(MatadorRegisterFileRequest)`
- `upload_file_bytes(presigned_url, file_path)`
- `get_file_status(file_id)`
- `list_studies()`
- `list_machines()`
- `list_specimens(project_id=None, study_id=None)`
- `list_ingest_sessions(study_id=None)`

Expected behavior:

- If URL/token are not configured, use stub only for local tests.
- Real client must normalize pasted page URLs to origin.
- Real client must strip `Bearer ` and quotes from token.
- Upload flow must verify sha256.

Test anchors:

- `tests/test_matador_upload_api.py`
- `tests/test_matador_upload_service.py`

## Contract 2: Resend Archived Sessions

Current service:

```python
from difra.gui.session_reupload_service import SessionReuploadService
```

Current service still injects legacy `SessionLifecycleActions`. Platform rewrite should replace that dependency with a pure upload orchestration service.

Inputs:

- session H5 paths
- container manager or repository status provider
- uploader ID
- Matador config
- optional specimen ID overrides
- progress callback

Output:

- `SendArchiveResult`

Required output fields:

```text
upload_success
upload_pending
upload_failed
failed[]
archived_paths[]
old_format_paths[]
old_format_failed[]
upload_session_id
```

Rules:

- Missing file increments `upload_failed`.
- `not_complete` is blocked.
- `req_resend` remains resendable.
- Old-format ZIP generation failure produces failed upload result.
- Pending Matador verification increments `upload_pending`.

Test anchors:

- `tests/test_session_reupload_service.py`
- `tests/upstream_snapshot/test_session_lifecycle_actions.py`

## Contract 3: Rewrite Session Technical Section

Current service:

```python
from difra.gui.session_technical_rewrite_service import SessionTechnicalRewriteService
```

Single session:

```python
result = SessionTechnicalRewriteService().rewrite_session_technical_section(
    session_path="session_x.nxs.h5",
    technical_path="technical_corrected.nxs.h5",
    reason="validated corrected PONI",
)
```

Batch by technical ID:

```python
results = SessionTechnicalRewriteService().rewrite_sessions_by_technical_id(
    session_paths=[...],
    technical_paths=[...],
    reason="batch repair",
)
```

Required behavior:

- Fail if session file missing.
- Fail if technical file missing.
- Read technical ID from technical file.
- Read session technical ID from `/entry/technical`, `/entry/calibration_snapshot`, or root attrs.
- Match only by technical ID.
- Create `.h5old`, `.h5old2`, etc.
- Copy corrected technical group into session.
- Preserve measurement data.
- Update root attrs:
  - `transfer_status=req_resend`
  - `technical_rewrite_required_resend=True`
  - `technical_rewrite_reason`
  - `technical_rewrite_timestamp`
  - `technical_rewrite_source_file`
  - `technical_container_id`
  - `source_container_id`
- Update embedded `meta_json`:
  - `CALIBRATION_GROUP_HASH`
  - `technical_container_id`
  - `source_container_id`
  - `technical_rewrite_required_resend`
  - `technical_rewrite_timestamp`
- Update sidecar `_state.json` if present.

Test anchor:

- `tests/test_session_technical_rewrite_service.py`

## Contract 4: Calibration QC

Current service:

```python
from difra.gui.technical.agbh_peak_qc_service import AgbhPeakQcService
```

Inputs:

- image arrays
- PONI text
- validation config

Outputs:

- list of warning strings

Rules:

- Return warnings only.
- Do not block technical container lock by AgBH peak QC.
- Hard-blocking validation is distance/metadata/center validation, not peak QC.

Test anchors:

- `tests/test_technical_poni_distance_guard.py`
- `tests/test_real_technical_container_fixtures.py`

## Contract 5: Daily Report

Current entry point:

```python
from difra.gui import daily_valid_container_reporter
```

Useful lower-level modules:

- `daily_report_integration.integrate_detector_signal`
- `daily_report_rendering.render_report_images`
- `daily_report_rendering.create_zip`

Inputs:

- archive folder
- optional period
- selected session paths or date selection
- SMTP/email config

Outputs:

- report folder
- ZIP file
- `manifest.json`
- PNG images
- copied PONI files
- email send result when enabled

Rules:

- Do not generate fake plot when q range is not covered.
- Include PONI files in ZIP.
- Include diagnostics in manifest.
- Send email only when report has images unless explicit test mode.

Test anchor:

- `tests/test_daily_valid_container_reporter.py`

## Platform API Shape

Suggested platform endpoints/jobs:

```text
POST /technical/validate
POST /sessions/create
POST /sessions/{id}/edit-metadata
POST /sessions/repair-technical
POST /matador/resend
GET  /archive/projects/statistics
POST /reports/daily
POST /reports/selected
```

Each endpoint should return:

```json
{
  "ok": true,
  "warnings": [],
  "errors": [],
  "artifacts": [],
  "updatedContainers": []
}
```

## Non-Negotiable Invariants

- Detector geometry for Ulster/Xena: 55 x 55 um, 256 x 256 px.
- PONI used for embedding must be read from file/H5, not in-memory cache.
- Technical ID is the grouping key for calibration reuse and repair matching.
- `req_resend` means previous Matador data must not be trusted as final.
- `not_complete` must not be sent.
- Old session H5 must be backed up before technical rewrite.
