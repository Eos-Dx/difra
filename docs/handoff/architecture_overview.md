# Architecture Overview

DiFRA is currently a Qt5 desktop application with business logic gradually separated into GUI-free services. The platform rewrite should preserve those service contracts and move UI-only code out of the path.

## Runtime Layers

```text
Qt GUI
  -> GUI mixins/presenters
    -> GUI-free services
      -> container.v0_2 writer/reader
      -> pyFAI/xrdanalysis integration
      -> Matador HTTP API
      -> filesystem archive
```

## Important Folders

- `src/difra/gui/main_window_ext/*`: large legacy GUI mixins.
- `src/difra/gui/*service.py`: extracted business logic entry points.
- `src/difra/gui/technical/*`: calibration, capture, pyFAI, PONI, AgBH QC.
- `src/difra/gui/daily_report_*`: daily/selected report pipeline pieces.
- `tests/`: stable behavior tests.
- `tests/upstream_snapshot/`: broad regression tests for old GUI/session behavior.

## Core Container Types

### Technical container

Purpose: calibration and technical measurements.

Canonical H5 group:

```text
/entry/technical
/entry/technical/poni
```

Contains:

- detector config
- PONI datasets
- AgBH, dark, empty, background technical events
- root/container attributes including `container_id`

### Session container

Purpose: specimen measurements linked to a technical calibration.

Important groups:

```text
/entry/technical
/entry/measurements
/entry/analytical_measurements
```

Current `container.v0_2` copies technical data into `/entry/technical`. Some code also supports `/entry/calibration_snapshot` for compatibility.

## Service Boundaries

### MatadorUploadService

File:

```text
src/difra/gui/matador_upload_service.py
```

Wraps active Matador backend:

- `StubMatadorUploadApi` for local deterministic tests
- `RealMatadorUploadApi` when `MATADOR_URL` and `MATADOR_TOKEN` are configured

Preserves:

- URL/token normalization
- find-or-create ingest session
- file registration
- byte upload
- file status polling
- specimen/study/machine listing

### SessionReuploadService

File:

```text
src/difra/gui/session_reupload_service.py
```

Runs archived session resend without moving containers.

Preserves:

- grouping/order by Matador group
- old-format ZIP rebuild
- calibration ZIP reuse within a batch
- H5/ZIP upload registration
- pending verification status
- metadata and upload attempt logs
- `NOT_COMPLETE` blocking
- `REQ_RESEND` preservation until verified

### SessionTechnicalRewriteService

File:

```text
src/difra/gui/session_technical_rewrite_service.py
```

Repairs already-created session containers by replacing embedded technical calibration.

Preserves:

- original session backup as `.h5old`, `.h5old2`, ...
- measurements remain untouched
- technical section is copied from a corrected technical H5
- embedded `meta_json` calibration hash updated
- sidecar `_state.json` updated when present
- session marked `transfer_status=req_resend`

### AgbhPeakQcService

File:

```text
src/difra/gui/technical/agbh_peak_qc_service.py
```

Non-blocking QC for AgBH peak alignment.

Preserves:

- theoretical AgBH q positions
- pyFAI integration via PONI text
- peak search window and warning thresholds
- warning-only behavior

## Current Coupling Risks

These are not blockers, but they matter for handoff:

- GUI mixins still call many static methods on `SessionLifecycleActions`.
- Some service methods still depend on action class injection to reuse legacy helpers.
- Daily report pipeline is split but still orchestrated by `daily_valid_container_reporter.py`.
- Matador upload/resend still has old-format compatibility inside workflow.
- Technical validation is partly in GUI mixins and partly in GUI-free modules.

## Platform Migration Strategy

Use services as business logic contracts, not final implementation:

1. Replace Qt GUI with platform UI/API.
2. Keep service input/output semantics.
3. Keep H5 invariants and test fixtures.
4. Replace `container.v0_2` calls only after equivalent platform writer is validated.
5. Keep Matador API sequence exactly unless Matador API changes.
