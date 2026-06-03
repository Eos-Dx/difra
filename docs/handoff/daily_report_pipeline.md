# Daily Report Pipeline

This document describes current daily/selected valid-container report behavior.

## Business Goal

For each measured specimen, generate analyst-facing images that show integrated detector signals and enough diagnostics to verify which PONI and raw data were used.

## Main Entry Point

Code:

```text
src/difra/gui/daily_valid_container_reporter.py
```

Split helper modules:

```text
src/difra/gui/daily_report_common.py
src/difra/gui/daily_report_models.py
src/difra/gui/daily_report_integration.py
src/difra/gui/daily_report_rendering.py
src/difra/gui/daily_report_credentials.py
```

Tests:

```text
tests/test_daily_valid_container_reporter.py
```

## Pipeline

```text
select containers
filter valid containers
collect detector datasets
resolve PONI text
integrate with pyFAI
check q coverage
group by specimen
render one PNG per specimen
write PONI files into output folder
write manifest diagnostics
zip PNG + PONI + manifest
send email when configured
record sent date/fingerprint
```

## Container Selection

Inputs can be:

- daily scheduled report
- selected archived containers
- manual test report

Valid containers exclude:

- `transfer_status` in `not_complete`, `failed`, `error`
- draft/recovery-only states
- containers without reportable detector data

## Detector Grouping

Current rendering rule:

```text
one PNG per specimen
one subplot per detector alias/key
PRIMARY/LEFT ordered before SECONDARY/RIGHT
```

Important:

- Primary/Secondary are aliases, not guaranteed physical truth.
- The report must support one detector, two detectors, or more detectors.
- Alias labels should not be hard-coded as the only possible detector names.

## Q Ranges

Current range assignment:

```text
near/2 cm containers -> WAXS range
far/17 cm containers -> SAXS range
```

Current configured ranges:

```text
SAXS: 1 to 3 nm^-1
WAXS: 2 to 21 nm^-1
```

The report must not fake data outside actual integrated q coverage.

Rule:

```text
if integration does not cover requested q range:
    skip that series/image
    record diagnostic
```

Tests:

- `test_collect_report_series_uses_container_distance_for_all_detector_ranges`
- `test_build_daily_report_does_not_fallback_when_backend_q_range_is_empty`
- `test_resample_range_requires_full_q_coverage`

## PONI Resolution

Preferred source:

1. explicit detector `poni_path`
2. detector-specific PONI dataset
3. alias PONI dataset
4. other candidate PONI only if integration result is valid

Rule:

- If explicit PONI integration returns empty or zero signal, next candidate may be tried.
- Manifest must record actual PONI source used.

Tests:

- `test_resolve_poni_text_prefers_explicit_detector_poni_path`
- `test_collect_report_series_uses_next_poni_when_explicit_integration_is_empty`

## Manifest

ZIP contains:

```text
manifest.json
*.png
poni/*.poni
```

Manifest must include:

- project IDs
- container counts
- Matador uploaded count
- image list
- detector panels
- series diagnostics
- source data sha256
- source data shape/min/median/max
- integration backend
- PONI source
- PONI file
- PONI sha256

Test:

- `test_build_daily_report_renders_one_combined_image_per_specimen`

## Email

Current behavior:

- daily report email can be disabled
- SMTP password can come from local storage/keychain flow
- test email can send simple two-image ZIP
- report email should not send if there are no images

Recipients currently configured by app config, not hard-coded in report logic.

Test anchors:

- `tests/test_daily_valid_container_reporter.py`
- `src/difra/gui/daily_report_credentials.py`

## Platform Requirements

Expose report pipeline as a service/job:

```text
generate_report(container_paths, output_dir, send_email=False)
```

Return:

```json
{
  "zipPath": "...",
  "images": [],
  "manifestPath": "...",
  "scanned": 0,
  "validContainers": 0,
  "skipped": []
}
```

Do not couple report generation to Qt dialogs.

## QA Checklist

- PNG has readable labels and no hidden detector panels.
- PONI files in ZIP match manifest.
- Manifest source hashes are present.
- 2 cm and 17 cm range assignment is correct.
- Empty q coverage does not produce misleading plots.
- Email body includes project ID, number of containers, and Matador uploaded count.
