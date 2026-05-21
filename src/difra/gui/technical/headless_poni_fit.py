"""Headless AgBH PONI fit.

Runs a fully automatic calibration from a single AgBH .npy image and writes
a .poni file alongside it, with no GUI required.

If pyFAI / Cython extensions are broken in the current Python environment
(e.g. Python 3.13), the script transparently re-dispatches itself into
``--pyfai-env`` (default: ulster38) via ``conda run``.

Usage::

    conda run -n eosdx13 python -m difra.gui.technical.headless_poni_fit \\
        --primary PATH.npy \\
        --distance-cm 17 \\
        [--alias PRIMARY] \\
        [--pyfai-env ulster38] \\
        [--output PATH.poni] \\
        [--first-ring N] \\
        [--rings N]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import traceback
from pathlib import Path

# Allow `python headless_poni_fit.py` (script mode, no package prefix)
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

_SCRIPT_PATH = Path(__file__).resolve()
_SRC_DIR = str(_SCRIPT_PATH.parents[3])

DEFAULT_PYFAI_ENV = "ulster38"
DEFAULT_ALIAS = "PRIMARY"


# ---------------------------------------------------------------------------
# Config helpers (duplicated from auto_poni_standalone to keep this self-contained)
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    from difra.gui.technical.auto_poni_standalone import load_default_config
    return load_default_config()


def _resolve_pyfai_env(config: dict, cli_env: str | None) -> str:
    if cli_env:
        return cli_env
    auto_cfg = config.get("auto_poni_calibration", {})
    if isinstance(auto_cfg, dict):
        env = str(auto_cfg.get("pyfai_calib2_env") or "").strip()
        if env:
            return env
    for var in ("DIFRA_PYFAI_CALIB2_ENV", "SIDECAR_ENV", "DIFRA_SIDECAR_ENV"):
        env = str(os.environ.get(var) or "").strip()
        if env:
            return env
    return DEFAULT_PYFAI_ENV


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless AgBH PONI fit — auto-dispatches to a working pyFAI env if needed"
    )
    parser.add_argument("--primary", required=True, help="Path to AgBH calibration .npy file")
    parser.add_argument("--distance-cm", type=float, required=True, help="Sample-to-detector distance in cm")
    parser.add_argument("--alias", default=DEFAULT_ALIAS, help="Detector alias (default: PRIMARY)")
    parser.add_argument(
        "--pyfai-env",
        default=None,
        help=f"conda env with working pyFAI for fallback dispatch (default: {DEFAULT_PYFAI_ENV})",
    )
    parser.add_argument("--output", default=None, help="Output .poni path (default: <source>.poni alongside .npy)")
    parser.add_argument("--first-ring", type=int, default=None, help="First visible AgBH ring index (1-based)")
    parser.add_argument("--rings", type=int, default=None, help="Number of rings to fit")
    # Internal flag: set when re-dispatched into the pyfai-env to prevent loops
    parser.add_argument("--_dispatched", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Core fit
# ---------------------------------------------------------------------------

def _run_fit(args: argparse.Namespace, config: dict) -> int:
    from difra.gui.technical.pyfai_calibration import (
        DEFAULT_ENERGY_KEV,
        build_seed_poni_text,
        energy_kev_to_wavelength_m,
        is_headless_agbh_fit_plausible,
        normalized_auto_poni_config,
        run_headless_agbh_fit,
    )
    from difra.gui.technical.auto_poni_standalone import (
        detector_config_for_alias,
        ring_defaults,
    )

    auto_config = normalized_auto_poni_config(config)
    alias = str(args.alias or DEFAULT_ALIAS).strip().upper()
    distance_cm = float(args.distance_cm)
    distance_m = distance_cm / 100.0
    calibrant = str(auto_config.get("calibrant") or "AgBh")
    energy_kev = float(auto_config.get("energy_kev", DEFAULT_ENERGY_KEV))
    wavelength_m = energy_kev_to_wavelength_m(energy_kev)

    detector_config = detector_config_for_alias(config, alias)
    first_visible, rings_count = ring_defaults(auto_config, [alias], distance_cm)
    first_ring = int(args.first_ring or first_visible.get(alias, 1))
    rings = int(args.rings or rings_count.get(alias, 3))

    source = Path(args.primary).expanduser().resolve()
    if not source.exists():
        print(f"[headless_poni_fit] ERROR: source file not found: {source}", file=sys.stderr)
        return 2

    output_root = source.parent / "auto_poni"
    output_root.mkdir(parents=True, exist_ok=True)

    seed_poni = build_seed_poni_text(
        detector_config=detector_config,
        distance_m=distance_m,
        alias=alias,
        wavelength_m=wavelength_m,
    )

    print(f"[headless_poni_fit] Source   : {source}")
    print(f"[headless_poni_fit] Alias    : {alias}")
    print(f"[headless_poni_fit] Distance : {distance_cm} cm")
    print(f"[headless_poni_fit] Energy   : {energy_kev} keV")
    print(f"[headless_poni_fit] Rings    : {first_ring} .. {first_ring + rings - 1}")
    print(f"[headless_poni_fit] Running pyFAI headless fit ...", flush=True)

    result = run_headless_agbh_fit(
        source_image=source,
        detector_config=detector_config,
        distance_m=distance_m,
        output_dir=output_root,
        alias=alias,
        wavelength_m=wavelength_m,
        calibrant=calibrant,
        first_visible_ring=first_ring,
        rings_to_show=rings,
    )

    print(f"[headless_poni_fit] Extracted : {result.extracted_points} points")
    print(f"[headless_poni_fit] Refined   : {result.refined}")
    print(f"[headless_poni_fit] Chi2      : {result.chi2}")

    plausible = is_headless_agbh_fit_plausible(
        result,
        seed_poni_text=seed_poni,
        detector_config=detector_config,
    )
    if not plausible:
        print(
            "[headless_poni_fit] WARNING: fit result may be implausible "
            "(large center/distance shift or too few points)."
        )

    output_poni = Path(args.output) if args.output else source.with_suffix(".poni")
    output_poni.write_text(result.poni_text, encoding="utf-8")
    print(f"[headless_poni_fit] Saved     : {output_poni}")
    print()
    print("--- PONI ---")
    print(result.poni_text)
    return 0


# ---------------------------------------------------------------------------
# Self-dispatch into a working pyFAI conda env
# ---------------------------------------------------------------------------

def _dispatch_to_env(pyfai_env: str, original_argv: list[str]) -> int:
    """Re-invoke this script inside ``pyfai_env`` via ``conda run``."""
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        _SRC_DIR + os.pathsep + existing_pythonpath
        if existing_pythonpath
        else _SRC_DIR
    )
    cmd = [
        "conda", "run", "--live-stream", "--no-capture-output",
        "-n", pyfai_env,
        "python", str(_SCRIPT_PATH),
        "--_dispatched",
        *original_argv,
    ]
    print(f"[headless_poni_fit] Dispatching to conda env '{pyfai_env}' ...")
    print(f"[headless_poni_fit] CMD: {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, env=env)
    return result.returncode


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = _load_config()
    pyfai_env = _resolve_pyfai_env(config, args.pyfai_env)

    if args._dispatched:
        # Already inside the target env — fail loudly instead of looping.
        return _run_fit(args, config)

    # Attempt fit in the current env; fall back to pyfai_env on any failure.
    try:
        return _run_fit(args, config)
    except Exception as exc:
        print(f"[headless_poni_fit] Fit failed in current env ({exc.__class__.__name__}: {exc})")
        traceback.print_exc()
        print(f"[headless_poni_fit] Will retry in env '{pyfai_env}' ...", flush=True)

    original_argv = [a for a in (argv if argv is not None else sys.argv[1:]) if a != "--_dispatched"]
    return _dispatch_to_env(pyfai_env, original_argv)


if __name__ == "__main__":
    raise SystemExit(main())
