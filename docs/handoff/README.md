# DiFRA Handoff Documentation

Purpose: make DiFRA business logic recoverable without prior chat context or RLM memory, and make the logic portable into the platform being developed.

This folder documents current behavior from code and tests. It does not replace the source code. It names the service boundaries, data invariants, state transitions, and failure handling rules that must survive any rewrite.

## Read Order

1. [Architecture Overview](architecture_overview.md)
2. [Business Logic Map](business_logic_map.md)
3. [Session State Machine](session_state_machine.md)
4. [Platform Integration Contract](platform_integration_contract.md)
5. [Calibration QC and PONI Rules](calibration_qc_and_poni_rules.md)
6. [Matador Upload and Resend Runbook](matador_upload_resend_runbook.md)
7. [Daily Report Pipeline](daily_report_pipeline.md)
8. [Recovery Playbook](recovery_playbook.md)
9. [Test Traceability Matrix](test_traceability_matrix.md)

## Existing Specs

- HDF5 data model: `src/difra/DIFRA_HDF5_Data_Model_FINAL.md`
- Container boundary: `src/difra/CONTAINER_INTEROP_BOUNDARY.md`
- Adapter spec: `src/difra/CONTAINER_ADAPTER_SPEC_V0_2.md`
- Matador requirements: `MATADOR_DATA_REQUIREMENTS_SPEC.md`
- Technical validation details: `docs/technical_container_validation.md`

## Main Service Boundaries

- Matador API facade: `src/difra/gui/matador_upload_service.py`
- Matador resend workflow: `src/difra/gui/session_reupload_service.py`
- Session technical rewrite/repair: `src/difra/gui/session_technical_rewrite_service.py`
- AgBH peak QC: `src/difra/gui/technical/agbh_peak_qc_service.py`
- Daily report pieces:
  - `src/difra/gui/daily_report_common.py`
  - `src/difra/gui/daily_report_models.py`
  - `src/difra/gui/daily_report_integration.py`
  - `src/difra/gui/daily_report_rendering.py`
  - `src/difra/gui/daily_report_credentials.py`
  - `src/difra/gui/daily_valid_container_reporter.py`

## Test Anchors

- Matador API/service: `tests/test_matador_upload_api.py`, `tests/test_matador_upload_service.py`
- Resend workflow: `tests/test_session_reupload_service.py`, `tests/upstream_snapshot/test_session_lifecycle_actions.py`
- Technical rewrite: `tests/test_session_technical_rewrite_service.py`
- Transfer status: `tests/test_session_transfer_status.py`, `tests/upstream_snapshot/test_session_tab_presenter.py`
- PONI/QC: `tests/test_technical_poni_distance_guard.py`, `tests/test_real_technical_container_fixtures.py`
- Auto PONI: `tests/test_pyfai_calibration.py`, `tests/test_technical_capture_mixin.py`
- Daily report: `tests/test_daily_valid_container_reporter.py`

## Current Validation Command

Use the environment that contains `pyFAI`:

```bash
conda run -n eosdx13 python -m pytest
```

Latest known local result when these docs were written:

```text
384 passed, 2 warnings
```

## Handoff Rule

Any platform rewrite should preserve this chain:

```text
business rule -> service contract -> H5 invariant -> regression test
```

If one link is missing, add the missing test or document before moving the logic.
