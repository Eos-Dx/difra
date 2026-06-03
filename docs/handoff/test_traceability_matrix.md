# Test Traceability Matrix

Use this file when changing or porting DiFRA logic. Each row maps a business rule to source files and tests that should fail if the rule is broken.

## Calibration And PONI

| Rule | Source | Tests |
| --- | --- | --- |
| PONI must be read from file/H5, not stale memory cache. | `src/difra/gui/main_window_ext/technical/h5_management_mixin.py` | `tests/test_technical_poni_distance_guard.py::test_sync_reads_poni_from_file_not_memory_cache` |
| Detector pixel size is 55 x 55 um. | `src/difra/resources/config/*.json`, `src/difra/gui/main_window_ext/technical/poni_center_validation.py` | `tests/test_detector_geometry_defaults.py`, `tests/test_technical_poni_distance_guard.py` |
| Detector shape is 256 x 256 px. | `src/difra/resources/config/*.json`, `src/difra/gui/main_window_ext/technical/poni_center_validation.py` | `tests/test_poni_center_validation.py`, `tests/test_technical_poni_distance_guard.py` |
| 2 cm PONI can fit around 1.8 to 3.0 cm; 17 cm PONI can fit around 16.5 to 18.0 cm. | `src/difra/resources/config/main.json`, `src/difra/resources/config/setups/Ulster (Xena).json` | `tests/test_technical_poni_distance_guard.py`, `tests/test_real_technical_container_fixtures.py` |
| 2 cm/17 cm swaps are blocking errors. | `src/difra/gui/main_window_ext/technical/h5_management_mixin.py` | `tests/test_technical_poni_distance_guard.py::test_poni_distance_validation_rejects_17cm_poni_for_nominal_2cm_range` |
| AgBH peak QC is warning-only. | `src/difra/gui/technical/agbh_peak_qc_service.py` | `tests/test_real_technical_container_fixtures.py`, `tests/test_technical_poni_distance_guard.py::test_agbh_peak_alignment_warns_when_late_peaks_drift` |
| Technical H5 must not duplicate SAXS/WAXS PONI aliases unless those are real detector aliases. | `container.v0_2.technical_container`, `src/difra/gui/main_window_ext/technical/h5_generation_*` | Covered by real fixture validation; add focused test before changing writer behavior. |

## Session Containers

| Rule | Source | Tests |
| --- | --- | --- |
| Session embeds technical snapshot. | `container.v0_2.writer`, `src/difra/gui/session_manager_*` | `tests/upstream_snapshot/test_session_manager.py`, `tests/upstream_snapshot/test_session_container.py` |
| Measurements are preserved when technical section is rewritten. | `src/difra/gui/session_technical_rewrite_service.py` | `tests/test_session_technical_rewrite_service.py` |
| Rewrite creates `.h5old`, `.h5old2`, etc. before modifying H5. | `src/difra/gui/session_technical_rewrite_service.py` | `tests/test_session_technical_rewrite_service.py` |
| Rewrite updates `meta_json` calibration hash and sidecar state JSON. | `src/difra/gui/session_technical_rewrite_service.py` | `tests/test_session_technical_rewrite_service.py` |
| Rewrite marks session `transfer_status=req_resend`. | `src/difra/gui/session_technical_rewrite_service.py` | `tests/test_session_technical_rewrite_service.py`, `tests/test_session_transfer_status.py` |
| `not_complete` containers cannot be sent. | `src/difra/gui/session_reupload_service.py`, `src/difra/gui/session_transfer_status.py` | `tests/test_session_reupload_service.py`, `tests/test_session_transfer_status.py` |

## Matador Upload

| Rule | Source | Tests |
| --- | --- | --- |
| Real Matador URL is normalized to origin. | `src/difra/gui/matador_upload_api.py` | `tests/test_matador_upload_api.py` |
| Matador token is normalized before HTTP use. | `src/difra/gui/matador_upload_api.py` | `tests/test_matador_upload_api.py` |
| Upload uses register -> upload bytes -> status poll. | `src/difra/gui/matador_upload_api.py`, `src/difra/gui/session_lifecycle_upload_*` | `tests/test_matador_upload_api.py`, `tests/test_matador_upload_service.py`, `tests/upstream_snapshot/test_session_lifecycle_actions.py` |
| Calibration ZIP is uploaded/reused per technical group. | `src/difra/gui/session_reupload_service.py`, `src/difra/gui/session_lifecycle_upload_*` | `tests/test_session_reupload_service.py`, `tests/upstream_snapshot/test_gui_session_send_queue.py` |
| Pending verification is not a durable archive status. | `src/difra/gui/session_transfer_status.py`, `src/difra/gui/session_tab_presenter.py` | `tests/test_session_transfer_status.py`, `tests/upstream_snapshot/test_session_tab_presenter.py` |

## Daily Report

| Rule | Source | Tests |
| --- | --- | --- |
| Report uses pyFAI q integration, not raw pixel axis. | `src/difra/gui/daily_report_integration.py` | `tests/test_daily_valid_container_reporter.py` |
| 2 cm containers use WAXS 2-21 nm^-1. | `src/difra/gui/daily_report_common.py` | `tests/test_daily_valid_container_reporter.py::test_collect_report_series_uses_container_distance_for_all_detector_ranges` |
| 17 cm containers use SAXS 1-3 nm^-1. | `src/difra/gui/daily_report_common.py` | `tests/test_daily_valid_container_reporter.py::test_collect_report_series_uses_container_distance_for_all_detector_ranges` |
| Report must not fake q coverage. | `src/difra/gui/daily_report_integration.py`, `src/difra/gui/daily_valid_container_reporter.py` | `tests/test_daily_valid_container_reporter.py::test_build_daily_report_does_not_fallback_when_backend_q_range_is_empty`, `tests/test_daily_valid_container_reporter.py::test_resample_range_requires_full_q_coverage` |
| One PNG per specimen can contain multiple detector panels. | `src/difra/gui/daily_report_rendering.py` | `tests/test_daily_valid_container_reporter.py::test_build_daily_report_renders_one_combined_image_per_specimen` |
| ZIP includes manifest and PONI files. | `src/difra/gui/daily_report_rendering.py` | `tests/test_daily_valid_container_reporter.py` |
| Email subject is `DifraReport:<date>`. | `src/difra/gui/daily_valid_container_reporter.py` | `tests/test_daily_valid_container_reporter.py::test_build_daily_report_email_includes_summary_and_subject` |

## GUI And Operator Flows

| Rule | Source | Tests |
| --- | --- | --- |
| Archive exposes `REQ_RESEND` as selectable status. | `src/difra/gui/session_transfer_status.py`, archive tab mixins | `tests/test_session_transfer_status.py`, `tests/upstream_snapshot/test_session_tab_presenter.py` |
| Archived metadata edits are audited. | archive edit dialog/actions | `tests/test_archive_session_edit_dialog.py` |
| Auto PONI ring choice is user-controllable. | `src/difra/gui/pyfai_calibration_dialog.py`, technical calibration helpers | `tests/test_pyfai_calibration.py`, `tests/test_technical_capture_mixin.py` |

## Full Regression Command

```bash
conda run -n eosdx13 python -m pytest
```
