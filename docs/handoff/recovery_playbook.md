# Recovery Playbook

Use this when rebuilding DiFRA behavior from repository state only.

## 1. Identify Runtime

Expected local runtime:

```text
GUI env: eosdx13
detector sidecar env: ulster37 or ulster38
gRPC endpoint: 127.0.0.1:50061
sidecar endpoint: 127.0.0.1:51001
```

Launchers:

```bash
bash src/difra/bin/run_difra_dual_env.sh
./src/difra/bin/run_difra.sh
src\difra\bin\run_difra.bat
```

Preflight:

```bash
bash scripts/ulster_real_test_preflight.sh
```

## 2. Validate Codebase

Use `eosdx13` because it contains pyFAI:

```bash
conda run -n eosdx13 python -m pytest
```

Focused recovery checks:

```bash
conda run -n eosdx13 python -m pytest \
  tests/test_technical_poni_distance_guard.py \
  tests/test_real_technical_container_fixtures.py \
  tests/test_session_technical_rewrite_service.py \
  tests/test_session_reupload_service.py \
  tests/test_daily_valid_container_reporter.py
```

## 3. Validate Configuration

Primary files:

```text
src/difra/resources/config/main.json
src/difra/resources/config/main_win.json
src/difra/resources/config/global.json
src/difra/resources/config/setups/Ulster (Xena).json
src/difra/resources/config/daily_report_email.json
```

Required detector geometry:

```text
pixel_size_um = [55, 55]
shape = [256, 256]
energy = 8.04 keV
```

Required PONI nominal ranges:

```text
2 cm: 1.8 to 3.0 cm
17 cm: 16.5 to 18.0 cm
```

## 4. Validate Technical Containers

Technical container logic is acceptable only if:

```text
PONI source is file/H5, not memory cache
PONI metadata is 55 x 55 um, 256 x 256 px
PONI distance is inside configured nominal range
AgBH QC has no peak_shift warnings for production calibration
technical aliases are real detector aliases
```

If a technical container is corrected:

```text
backup original
rewrite corrected technical H5
rewrite matching session technical sections by technical_container_id
mark affected sessions req_resend
```

## 5. Validate Session Containers

Session container must keep:

```text
/entry/measurements
/entry/analytical_measurements
/entry/technical
root attrs: specimen/project/study/operator/created/transfer_status
embedded meta_json
optional sidecar _state.json
```

Technical rewrite must not modify measurement data.

## 6. Validate Matador Upload

Do not upload until:

```text
container is complete
specimen ID resolves in Matador
technical group is corrected
old-format ZIP can be generated
previous bad Matador entries are deactivated when required
```

Upload success requires:

```text
calibration ZIP status HASH_VERIFIED
session ZIP/H5 status HASH_VERIFIED
local transfer_status becomes sent only after verification
```

## 7. Validate Daily Report

Report must produce:

```text
one PNG per specimen
one panel per detector alias
SAXS 1-3 nm^-1 for far/17 cm containers
WAXS 2-21 nm^-1 for near/2 cm containers
manifest.json
poni/*.poni files
source hashes and diagnostics
```

No plot should be produced when q coverage is missing.

## 8. Handoff Minimum

Before passing code to another team, keep these documents current:

```text
docs/handoff/business_logic_map.md
docs/handoff/calibration_qc_and_poni_rules.md
docs/handoff/platform_integration_contract.md
docs/handoff/test_traceability_matrix.md
```
