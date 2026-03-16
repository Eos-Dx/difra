#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
REPO_PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

GUI_ENV="${DIFRA_GUI_ENV:-eosdx13}"
LEGACY_ENV="${DIFRA_LEGACY_ENV:-ulster37}"
RUN_TESTS="${DIFRA_PREFLIGHT_RUN_TESTS:-1}"

info() {
  echo "[INFO] $*"
}

warn() {
  echo "[WARN] $*"
}

fail() {
  echo "[ERROR] $*"
  exit 1
}

if ! command -v conda >/dev/null 2>&1; then
  fail "'conda' not found on PATH."
fi

info "Repository: $REPO_ROOT"
info "GUI env: $GUI_ENV"
info "Legacy env: $LEGACY_ENV"

CONDA_ENVS_JSON="$(conda env list --json)"

if ! python3 - "$CONDA_ENVS_JSON" "$GUI_ENV" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(sys.argv[1])
target = sys.argv[2].strip()
names = {Path(p).name for p in payload.get("envs", []) if p}
if target not in names:
    raise SystemExit(1)
PY
then
  fail "GUI env '$GUI_ENV' does not exist."
fi

if ! python3 - "$CONDA_ENVS_JSON" "$LEGACY_ENV" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(sys.argv[1])
target = sys.argv[2].strip()
names = {Path(p).name for p in payload.get("envs", []) if p}
if target not in names:
    raise SystemExit(1)
PY
then
  fail "Legacy env '$LEGACY_ENV' does not exist."
fi

GUI_PY="$(conda run --no-capture-output -n "$GUI_ENV" python -c 'import sys; print(sys.version)' | tail -n 1)"
LEGACY_PY="$(conda run --no-capture-output -n "$LEGACY_ENV" python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' | tail -n 1)"
info "GUI python: $GUI_PY"
info "Legacy python: $LEGACY_PY"

if [[ "$LEGACY_PY" != "3.7" && "$LEGACY_PY" != "3.8" ]]; then
  fail "Legacy env '$LEGACY_ENV' must be Python 3.7/3.8, got '$LEGACY_PY'."
fi

info "Validating selected DiFRA config and filesystem paths..."
PYTHONPATH="$REPO_PYTHONPATH" conda run --live-stream --no-capture-output -n "$GUI_ENV" \
  python - <<'PY'
from pathlib import Path
from difra.grpc_server.server import load_difra_config

cfg = load_difra_config(None)
print("[INFO] Loaded config for real-hardware preflight")

required_path_keys = [
    "difra_base_folder",
    "technical_folder",
    "technical_archive_folder",
    "measurements_folder",
    "measurements_archive_folder",
]
missing = []
for key in required_path_keys:
    raw = str(cfg.get(key) or "").strip()
    if not raw:
        missing.append(f"{key}=<empty>")
        continue
    p = Path(raw)
    if not p.exists():
        missing.append(f"{key}={p} (missing)")
        continue
    print(f"[INFO] {key}={p}")

if missing:
    print("[ERROR] Missing required configured paths:")
    for item in missing:
        print(f"[ERROR]   - {item}")
    raise SystemExit(1)

detectors = list(cfg.get("detectors") or [])
active_det_ids = set(cfg.get("active_detectors") or [])
active_det = [d for d in detectors if d.get("id") in active_det_ids]
if not active_det:
    raise SystemExit("[ERROR] No active detectors in config.")
print(f"[INFO] Active detectors: {[d.get('alias') for d in active_det]}")

stages = list(cfg.get("translation_stages") or [])
active_stage_ids = set(cfg.get("active_translation_stages") or [])
active_stage = [s for s in stages if s.get("id") in active_stage_ids]
if not active_stage:
    raise SystemExit("[ERROR] No active translation stages in config.")
print(f"[INFO] Active stage(s): {[s.get('alias') for s in active_stage]}")
PY

info "Ensuring runtime dependencies in '$GUI_ENV'..."
PYTHONPATH="$REPO_PYTHONPATH" conda run --live-stream --no-capture-output -n "$GUI_ENV" \
  python "$REPO_ROOT/src/difra/scripts/ensure_runtime_dependencies.py" \
  --require container --require protocol --require xrdanalysis

if [[ "$RUN_TESTS" == "1" ]]; then
  info "Running critical non-hardware verification tests..."
  conda run --live-stream --no-capture-output -n "$GUI_ENV" \
    bash -lc "cd '$REPO_ROOT' && PYTHONPATH=src pytest -q \
      tests/upstream_snapshot/test_grpc_sidecar_integration.py::test_motion_stop_invokes_state_stop_motion \
      tests/upstream_snapshot/test_grpc_sidecar_integration.py::test_motion_stop_propagates_precondition_errors \
      tests/upstream_snapshot/test_difra_service_state_parallel_capture.py::test_telemetry_overflow_is_mirrored_to_container_runtime_logs"
else
  warn "Skipping pytest checks because DIFRA_PREFLIGHT_RUN_TESTS=$RUN_TESTS"
fi

info "Preflight passed."
info "Next step (real hardware smoke): bash src/difra/bin/run_hardware_stack_tests.sh"
