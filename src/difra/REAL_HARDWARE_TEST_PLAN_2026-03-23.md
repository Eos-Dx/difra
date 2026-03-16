# ULSTER Real Hardware Test Plan (Monday, March 23, 2026)

## Goal

Validate real-hardware stability on ULSTER with focus on:
1. motion stop correctness (`Motion.Stop`);
2. behavior of `Abort` during hardware execution;
3. release/startup stability (avoid auto-update drift);
4. telemetry overflow visibility in all required logs.

## Already prepared locally (done)

1. `Motion.Stop` now calls real stage stop path (not exposure abort fallback).
2. Stage stop API added end-to-end (`grpc -> state -> client -> controller`).
3. Telemetry queue overflow warnings now go to:
   - sidecar/global log;
   - session container runtime logs;
   - technical container runtime logs.
4. Added preflight script:
   - `scripts/ulster_real_test_preflight.sh`
5. Added standalone stop drill script:
   - `src/difra/scripts/motion_stop_drill.py`
6. Added/updated targeted tests for stop and telemetry bridge.

## Hard constraints and interpretation

1. `Abort` cannot physically interrupt detector firmware call already executing at hardware layer.
2. `Abort` is treated as control-plane intent: server state transitions to stopping/interrupted and completes when hardware call returns.
3. Real stop guarantee must be validated on stage motion path via `Motion.Stop` drill.

## Monday execution steps (ULSTER machine)

1. Freeze runtime inputs before first run:
```bash
cd /Users/sad/dev/difra
git rev-parse --short HEAD
conda env list
```
2. Run preflight in `eosdx13`:
```bash
cd /Users/sad/dev/difra
bash scripts/ulster_real_test_preflight.sh
```
3. Run real hardware smoke suite:
```bash
cd /Users/sad/dev/difra
bash src/difra/bin/run_hardware_stack_tests.sh
```
4. Run dedicated motion-stop drill (mandatory):
```bash
cd /Users/sad/dev/difra
conda run -n eosdx13 python src/difra/scripts/motion_stop_drill.py \
  --host 127.0.0.1 \
  --port 50061 \
  --axis x \
  --delta-mm 4.0 \
  --stop-delay-s 0.15 \
  --assert-partial-stop
```
5. Repeat drill on second axis:
```bash
cd /Users/sad/dev/difra
conda run -n eosdx13 python src/difra/scripts/motion_stop_drill.py \
  --host 127.0.0.1 \
  --port 50061 \
  --axis y \
  --delta-mm 4.0 \
  --stop-delay-s 0.15 \
  --assert-partial-stop
```
6. Optional pytest-gated stop drill inside real-hardware suite:
```bash
cd /Users/sad/dev/difra
DIFRA_REAL_HW_ENABLE_STOP_DRILL=1 \
conda run -n eosdx13 pytest -q -s \
  tests/upstream_snapshot/manual_hardware_real_legacy_e2e.py::test_real_hardware_grpc_motion_stop_drill
```

## Telemetry overflow evidence check

1. During/after load test, search sidecar/global log for overflow warning:
```bash
cd /Users/sad/dev/difra
rg -n "Telemetry queue overflow|telemetry_queue_overflow" /Users/sad/dev/Data/difra -S
```
2. Confirm event appears in active session and technical container runtime logs (same event key):
   - `event_type = telemetry_queue_overflow`
   - `source = difra_grpc_sidecar`

## Pass/Fail gates

1. Preflight passes without errors.
2. Smoke hardware suite passes.
3. `motion_stop_drill` returns exit code `0` for both axes.
4. For stop drill with `--assert-partial-stop`, final position remains measurably before requested target.
5. If telemetry queue overflow occurs under load, warning is visible in all three channels:
   - sidecar/global log;
   - session container runtime log;
   - technical container runtime log.

## Immediate fallback rules (if failing)

1. If stop drill fails on any axis: block release and keep machine in controlled/manual mode.
2. If hardware suite fails due sidecar/runtime mismatch: re-run only after env/runtime freeze is restored.
3. If overflow appears only in one log channel: block release and fix log bridge before next measurement campaign.
