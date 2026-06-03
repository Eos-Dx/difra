# Calibration QC and PONI Rules

This document summarizes current hard and soft calibration rules.

Detailed existing spec:

- `docs/technical_container_validation.md`

## Detector Geometry

Ulster/Xena expected detector geometry:

```text
pixel size: 55 x 55 um
shape: 256 x 256 px
energy: 8.04 keV
```

Code:

- `src/difra/gui/technical/pyfai_calibration_common.py`

Tests:

- `tests/test_detector_geometry_defaults.py`
- `tests/test_poni_center_validation.py`
- `tests/test_technical_poni_distance_guard.py`

## PONI Source Rule

PONI embedded into technical H5 must come from:

- selected PONI file
- generated PONI file on disk
- existing PONI in H5

It must not come from stale in-memory cache.

Test anchor:

- `tests/test_technical_poni_distance_guard.py::test_sync_reads_poni_from_file_not_memory_cache`

## PONI Distance Rules

PONI file stores distance in meters:

```text
Distance: <meters>
```

DiFRA compares in centimeters:

```text
distance_cm = Distance * 100
```

Configured nominal ranges:

```text
2 cm nominal: 1.8 to 3.0 cm
17 cm nominal: 16.5 to 18.0 cm
```

Reason:

- real fitted "2 cm" geometry can be around 2.3 to 2.5 cm
- 17 cm should stay close to nominal
- 2 cm/17 cm swaps must be blocked

Hard failures:

- 2 cm container with 17 cm PONI
- 17 cm container with 2 cm PONI
- missing `Distance`
- invalid/nonpositive distance

Tests:

- `tests/test_technical_poni_distance_guard.py`
- `tests/test_real_technical_container_fixtures.py`

## PONI Metadata Rules

Configured metadata validation checks:

```text
expected_energy_keV = 8.04
energy_tolerance_keV = 0.1
expected_pixel_size_um = [55, 55]
pixel_tolerance_um = 0.25
expected_shape = [256, 256]
```

Hard failures:

- 50 x 50 um PONI
- missing pixel size
- detector shape not 256 x 256
- missing wavelength/energy
- energy outside tolerance

Tests:

- `tests/test_technical_poni_distance_guard.py::test_sync_blocks_poni_with_wrong_pixel_size`
- `tests/test_technical_poni_distance_guard.py::test_poni_metadata_validation_catches_wrong_pixel_size`

## Beam Center Rules

Current center calculation:

```text
row_px = Poni1 / pixel1
col_px = Poni2 / pixel2
```

Current default rules:

```text
PRIMARY:
  row around 128 px
  column near left edge

SECONDARY:
  row around 128 px
  column outside/right of detector geometry
```

Exact tolerances live in config and validation code.

Code:

- `src/difra/gui/main_window_ext/technical/poni_center_validation.py`

Tests:

- `tests/test_poni_center_validation.py`
- `tests/test_technical_poni_distance_guard.py`

## AgBH Peak QC

Current behavior:

- non-blocking warning
- integrates AgBH technical image
- compares observed peak maxima to theoretical AgBH q positions
- warns on peak shifts

Config values used in tests/docs:

```text
npt = 300 or 600
peak_window_nm_inv = 0.20
peak_shift_warning_nm_inv = 0.25
min_checked_peaks = 4
PRIMARY q range = 1.0 to 18.0 nm^-1
SECONDARY q range = 4.5 to 23.0 nm^-1
```

Important distinction:

```text
peak_shift warning:
    calibration likely bad

not_enough_agbh_peaks_checked:
    QC coverage limitation, not necessarily bad calibration
```

Real fixture behavior:

- validated 2 cm fixture has no peak warnings
- validated 17 cm fixture can report `not_enough_agbh_peaks_checked`
- neither fixture should report `peak_shift` or `outside tolerance`

Tests:

- `tests/test_real_technical_container_fixtures.py`
- `tests/test_technical_poni_distance_guard.py::test_agbh_peak_alignment_warns_when_late_peaks_drift`

## Auto PONI Requirements

Business intent:

- operator can choose ring number
- hovering/changing label updates selected ring control
- user can force spinbox ring number
- generated PONI must use 55 x 55 um and 256 x 256 detector geometry
- generated PONI must reproduce validated technical fixture PONIs within expected tolerance

Tests:

- `tests/test_pyfai_calibration.py`
- `tests/test_technical_capture_mixin.py`

## Platform Rules

- Treat distance/metadata/center checks as blocking.
- Treat AgBH peak QC as warning until validated enough to block.
- Store PONI text and PONI source path/hash for traceability.
- Never embed duplicate `poni_saxs` or `poni_waxs` unless actual detector aliases are SAXS/WAXS.
- Keep real fixture tests or platform equivalents.
