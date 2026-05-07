"""Standalone Auto PONI review GUI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

import numpy as np


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from difra.gui.technical.pyfai_calibration import (
    DEFAULT_ENERGY_KEV,
    energy_kev_to_wavelength_m,
    load_calibration_array,
    normalized_auto_poni_config,
    prepare_agbh_calib2_review,
    write_pyfai_calib2_launcher,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PACKAGE_ROOT / "resources" / "config"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_default_config() -> dict:
    global_config = _read_json(CONFIG_ROOT / "global.json")
    setup_name = str(global_config.get("default_setup") or "").strip()
    setup_config = {}
    if setup_name:
        setup_config = _read_json(CONFIG_ROOT / "setups" / f"{setup_name}.json")
    merged = dict(global_config)
    for key, value in setup_config.items():
        if key not in merged:
            merged[key] = value
    return merged


def infer_alias(path: str | Path) -> str:
    name = Path(path).name.upper()
    if "SECONDARY" in name or "WAXS" in name:
        return "SECONDARY"
    if "PRIMARY" in name or "SAXS" in name:
        return "PRIMARY"
    return Path(path).stem.split("_")[-1].upper()


def detector_config_for_alias(config: dict, alias: str) -> dict:
    alias_key = str(alias or "").strip().upper()
    for detector in config.get("detectors", []) or []:
        if str(detector.get("alias") or "").strip().upper() == alias_key:
            result = dict(detector)
            result["alias"] = alias_key
            return result
    return {
        "alias": alias_key,
        "id": alias_key,
        "size": {"width": 256, "height": 256},
        "pixel_size_um": [50, 50],
    }


def center_px_from_validation(config: dict, alias: str, detector_config: dict):
    validation = config.get("poni_center_validation", {})
    if not isinstance(validation, dict) or not validation.get("enabled", False):
        return None
    rules = dict(validation.get("defaults", {}) or {})
    detector_rules = validation.get("detectors", {}) or {}
    rule_alias = str(detector_config.get("poni_center_rule_alias") or alias).upper()
    for key, value in detector_rules.items():
        if str(key).upper() == rule_alias and isinstance(value, dict):
            rules.update(value)
            break

    size = detector_config.get("size", {}) if isinstance(detector_config, dict) else {}
    width = float(size.get("width", 256) if isinstance(size, dict) else 256)
    height = float(size.get("height", 256) if isinstance(size, dict) else 256)

    def _float(value, default=None):
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    row = _float(rules.get("row_target_px"), height / 2.0)
    col = _float(rules.get("col_target_px"))
    col_min = _float(rules.get("col_min_px"))
    col_max = _float(rules.get("col_max_px"))
    col_gt = _float(rules.get("col_gt_px"))
    col_lt = _float(rules.get("col_lt_px"))
    if col is None:
        if col_gt is not None and col_max is not None and col_max > col_gt:
            col = col_max
        elif col_min is not None and col_lt is not None and col_lt > col_min:
            col = col_min
        elif col_gt is not None and col_lt is not None and col_lt > col_gt:
            col = (col_gt + col_lt) / 2.0
        elif col_gt is not None:
            col = col_gt + 1.0
        elif col_min is not None and col_max is not None and col_max >= col_min:
            col = (col_min + col_max) / 2.0
        elif col_min is not None:
            col = col_min
        elif col_lt is not None:
            col = col_lt - 1.0
        elif col_max is not None:
            col = col_max
        else:
            col = width / 2.0
    if col_gt is not None and not (col > col_gt):
        col = col_gt + 1.0
    if col_min is not None and col < col_min:
        col = col_min
    if col_lt is not None and not (col < col_lt):
        col = col_lt - 1.0
    if col_max is not None and col > col_max:
        col = col_max
    return float(row), float(col)


def distance_key(distance_cm: float) -> str:
    rounded = round(float(distance_cm))
    if abs(float(distance_cm) - rounded) <= 0.25:
        return str(int(rounded))
    return f"{float(distance_cm):.3f}".rstrip("0").rstrip(".")


def ring_defaults(auto_config: dict, aliases: list[str], distance_cm: float):
    key = distance_key(distance_cm)
    first_by_distance = auto_config.get("first_visible_ring_by_distance_cm", {}) or {}
    count_by_distance = auto_config.get("rings_to_search_by_distance_cm", {}) or {}
    first_by_alias = auto_config.get("first_visible_ring_by_alias", {}) or {}
    count_by_alias = auto_config.get("rings_to_search_by_alias", {}) or {}
    first = {}
    count = {}
    for alias in aliases:
        alias_key = str(alias).upper()
        first[alias_key] = int(
            (first_by_distance.get(key, {}) or {}).get(
                alias_key,
                first_by_alias.get(alias_key, 1),
            )
            or 1
        )
        count[alias_key] = int(
            (count_by_distance.get(key, {}) or {}).get(
                alias_key,
                count_by_alias.get(alias_key, auto_config.get("rings_to_show", 3)),
            )
            or 3
        )
    return first, count


def save_validated_ponis(reviews: dict) -> list[Path]:
    written = []
    for alias, review in reviews.items():
        source_path = Path(getattr(review, "source_path", "") or "")
        target = source_path.with_suffix(".poni") if source_path else Path(review.poni_path)
        target.write_text(str(review.poni_text or ""), encoding="utf-8")
        written.append(target)
        print(f"{alias}: saved {target}")
    return written


def launch_pyfai_calib2(reviews: dict, env: str):
    commands = []
    folder = None
    for alias, review in reviews.items():
        command = list(review.command)
        if "DIFRA-256-50UM" in command:
            launcher = write_pyfai_calib2_launcher(
                output_dir=Path(review.image_path).parent,
                command=command,
                launcher_stem=f"run_pyfai_calib2_{alias}",
            )
            command = ["python", str(launcher)]
        commands.append(["conda", "run", "--live-stream", "--no-capture-output", "-n", env, *command])
        folder = folder or Path(review.image_path).parent
    if not commands:
        return
    if sys.platform == "darwin":
        script = "\n".join(
            [
                "#!/bin/bash",
                f"cd {shlex.quote(str(folder or Path.cwd()))}",
                *(" ".join(shlex.quote(part) for part in cmd) for cmd in commands),
                "",
            ]
        )
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".command", delete=False)
        try:
            handle.write(script)
            script_path = handle.name
        finally:
            handle.close()
        os.chmod(script_path, 0o755)
        subprocess.Popen(["open", "-a", "Terminal", script_path])
    else:
        for command in commands:
            subprocess.Popen(command, cwd=str(folder or Path.cwd()))


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Standalone DIFRA Auto PONI GUI")
    parser.add_argument("images", nargs="*", help="AgBH image paths; alias inferred from filename")
    parser.add_argument("--primary", help="PRIMARY AgBH image path")
    parser.add_argument("--secondary", help="SECONDARY AgBH image path")
    parser.add_argument("--distance-cm", type=float, default=17.0)
    parser.add_argument("--energy-kev", type=float, default=None)
    parser.add_argument("--output-dir", default=None)
    default_pyfai_env = (
        os.environ.get("DIFRA_PYFAI_CALIB2_ENV")
        or os.environ.get("SIDECAR_ENV")
        or os.environ.get("DIFRA_SIDECAR_ENV")
        or "ulster38"
    )
    parser.add_argument("--pyfai-env", default=default_pyfai_env)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sources = {}
    if args.primary:
        sources["PRIMARY"] = args.primary
    if args.secondary:
        sources["SECONDARY"] = args.secondary
    for image in args.images:
        sources[infer_alias(image)] = image
    if not sources:
        print("No images provided.", file=sys.stderr)
        return 2

    from PyQt5.QtWidgets import QApplication
    from difra.gui.technical.capture import show_auto_poni_review_window

    config = load_default_config()
    auto_config = normalized_auto_poni_config(config)
    energy_kev = float(args.energy_kev if args.energy_kev is not None else auto_config.get("energy_kev", DEFAULT_ENERGY_KEV))
    wavelength_m = energy_kev_to_wavelength_m(energy_kev)
    aliases = sorted(sources.keys())
    first_visible, rings_to_show = ring_defaults(auto_config, aliases, args.distance_cm)
    output_root = Path(args.output_dir) if args.output_dir else Path(next(iter(sources.values()))).parent / "auto_poni"
    output_root.mkdir(parents=True, exist_ok=True)

    reviews = {}
    images = {}
    detector_configs = {}
    for alias in aliases:
        source = Path(sources[alias]).expanduser().resolve()
        detector_config = detector_config_for_alias(config, alias)
        detector_configs[alias] = detector_config
        center_px = center_px_from_validation(config, alias, detector_config)
        reviews[alias] = prepare_agbh_calib2_review(
            source_image=source,
            detector_config=detector_config,
            distance_m=float(args.distance_cm) / 100.0,
            alias=alias,
            output_dir=output_root,
            existing_poni_text=str(detector_config.get("default_poni") or ""),
            wavelength_m=wavelength_m,
            calibrant=str(auto_config.get("calibrant") or "AgBh"),
            center_px=center_px,
            first_visible_ring=first_visible.get(alias, 1),
            rings_to_show=rings_to_show.get(alias, 3),
        )
        images[alias] = np.asarray(load_calibration_array(source), dtype=float)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    payload = show_auto_poni_review_window(
        aliases=aliases,
        review_by_alias=reviews,
        images_by_alias=images,
        detector_config_by_alias=detector_configs,
        first_visible_ring_by_alias=first_visible,
        rings_to_show=rings_to_show,
        parent=None,
    )
    decision = str((payload or {}).get("decision") or "cancel").lower()
    if decision == "validate":
        save_validated_ponis(reviews)
    elif decision == "correct":
        launch_pyfai_calib2(reviews, args.pyfai_env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
