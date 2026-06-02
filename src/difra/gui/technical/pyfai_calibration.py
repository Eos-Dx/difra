"""Helpers for preparing pyFAI calibration review runs."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Mapping, Sequence

import numpy as np

DEFAULT_CALIBRANT = "AgBh"
DEFAULT_WAVELENGTH_M = 1.5406e-10
DEFAULT_ENERGY_KEV = 8.04
AGBH_D_SPACING_A = (
    58.38,
    29.19,
    19.46,
    14.595,
    11.676,
    9.73,
    8.34,
    7.2975,
    6.48666667,
    5.838,
    5.30727273,
    4.865,
    4.49076923,
    4.17,
    3.892,
    3.64875,
    3.43411765,
    3.24333333,
    3.07263158,
    2.919,
)


@dataclass(frozen=True)
class PyfaiCalib2Review:
    image_path: Path
    poni_path: Path
    command: list[str]
    poni_text: str
    source_path: Path | None = None


@dataclass(frozen=True)
class HeadlessPoniFitResult:
    poni_path: Path
    poni_text: str
    npt_path: Path
    extracted_points: int
    refined: bool
    chi2: float | None


def _to_float(value, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_token(value: str, fallback: str = "detector") -> str:
    token = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or ""))
    return token.strip("_") or fallback


def _format_float(value: float) -> str:
    rounded = round(float(value))
    if abs(float(value) - rounded) <= 1e-12:
        return str(int(rounded))
    return f"{float(value):.16g}"


def energy_kev_to_wavelength_m(energy_kev: float) -> float:
    energy = float(energy_kev)
    if energy <= 0.0:
        return DEFAULT_WAVELENGTH_M
    wavelength_a = 12.398419843320026 / energy
    return wavelength_a * 1e-10


def detector_size_px(detector_config: Mapping | None) -> tuple[int, int]:
    cfg = detector_config if isinstance(detector_config, Mapping) else {}
    size = cfg.get("size")
    width = height = None
    if isinstance(size, Mapping):
        width = size.get("width")
        height = size.get("height")
    elif isinstance(size, Sequence) and not isinstance(size, (str, bytes)) and len(size) >= 2:
        width, height = size[0], size[1]
    width = int(_to_float(width, 256) or 256)
    height = int(_to_float(height, 256) or 256)
    return max(1, width), max(1, height)


def pixel_size_m(detector_config: Mapping | None) -> tuple[float, float]:
    cfg = detector_config if isinstance(detector_config, Mapping) else {}
    raw = cfg.get("pixel_size_um", cfg.get("pixel_size", 55.0))
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        first = raw[0] if len(raw) >= 1 else 55.0
        second = raw[1] if len(raw) >= 2 else first
    else:
        first = second = raw
    pixel1 = float(f"{(float(_to_float(first, 55.0) or 55.0) * 1e-6):.12g}")
    pixel2 = float(f"{(float(_to_float(second, 55.0) or 55.0) * 1e-6):.12g}")
    return pixel1, pixel2


def pyfai_detector_name(detector_config: Mapping | None) -> str:
    cfg = detector_config if isinstance(detector_config, Mapping) else {}
    configured = str(cfg.get("pyfai_detector") or "").strip()
    if configured:
        return configured

    width, height = detector_size_px(cfg)
    pixel1, pixel2 = pixel_size_m(cfg)
    if (width, height) == (256, 256) and abs(pixel1 - 55e-6) < 1e-12 and abs(
        pixel2 - 55e-6
    ) < 1e-12:
        return "Maxipix"
    return "Detector"


def parse_poni_parameters(poni_text: str) -> dict[str, float | dict]:
    text = str(poni_text or "")
    parsed: dict[str, float | dict] = {}
    for key in ("Distance", "Poni1", "Poni2", "Rot1", "Rot2", "Rot3", "Wavelength"):
        match = re.search(rf"^{key}:\s*([0-9.eE+\-]+)", text, flags=re.MULTILINE)
        if match:
            value = _to_float(match.group(1))
            if value is not None:
                parsed[key] = value
    for line in text.splitlines():
        if not line.startswith("Detector_config:"):
            continue
        try:
            payload = line.split(":", 1)[1].strip()
            detector_config = json.loads(payload)
        except (json.JSONDecodeError, IndexError, TypeError, ValueError):
            detector_config = None
        if isinstance(detector_config, dict):
            parsed["Detector_config"] = detector_config
        break
    return parsed


def build_seed_poni_text(
    *,
    detector_config: Mapping | None,
    distance_m: float,
    alias: str = "",
    existing_poni_text: str = "",
    wavelength_m: float | None = None,
    center_px: tuple[float, float] | None = None,
    created_at: str | None = None,
) -> str:
    cfg = detector_config if isinstance(detector_config, Mapping) else {}
    width, height = detector_size_px(cfg)
    pixel1, pixel2 = pixel_size_m(cfg)
    existing = parse_poni_parameters(existing_poni_text)

    if center_px is None and "Poni1" in existing and "Poni2" in existing:
        poni1 = float(existing["Poni1"])
        poni2 = float(existing["Poni2"])
    else:
        if center_px is None:
            row_px = float(height) / 2.0
            col_px = float(width) / 2.0
        else:
            row_px, col_px = center_px
        poni1 = float(row_px) * pixel1
        poni2 = float(col_px) * pixel2

    rot1 = float(existing.get("Rot1", 0.0))
    rot2 = float(existing.get("Rot2", 0.0))
    rot3 = float(existing.get("Rot3", 0.0))
    wavelength = float(
        wavelength_m
        if wavelength_m is not None
        else existing.get("Wavelength", DEFAULT_WAVELENGTH_M)
    )
    orientation = 3
    existing_detector = existing.get("Detector_config")
    if isinstance(existing_detector, dict):
        orientation = int(_to_float(existing_detector.get("orientation"), 3) or 3)

    timestamp = created_at or time.strftime("%a %b %d %H:%M:%S %Y")
    detector_id = str(cfg.get("id") or alias or "Detector")
    alias_text = str(alias or cfg.get("alias") or detector_id)
    detector_payload = {
        "pixel1": pixel1,
        "pixel2": pixel2,
        "max_shape": [height, width],
        "orientation": orientation,
    }
    return "\n".join(
        [
            "# Nota: C-Order, 1 refers to the Y axis, 2 to the X axis",
            f"# Seed calibration generated by DIFRA at {timestamp}",
            "poni_version: 2.1",
            "Detector: Detector",
            f"Detector_config: {json.dumps(detector_payload, separators=(',', ': '))}",
            f"Distance: {_format_float(distance_m)}",
            f"Poni1: {_format_float(poni1)}",
            f"Poni2: {_format_float(poni2)}",
            f"Rot1: {_format_float(rot1)}",
            f"Rot2: {_format_float(rot2)}",
            f"Rot3: {_format_float(rot3)}",
            f"Wavelength: {_format_float(wavelength)}",
            f"# Calibrant: {DEFAULT_CALIBRANT}",
            f"# Detector alias: {alias_text}",
            f"# Detector id: {detector_id}",
            "",
        ]
    )


def _load_array_from_h5ref(source_ref: str) -> np.ndarray:
    payload = str(source_ref or "")[len("h5ref://") :]
    container_path, sep, dataset_path = payload.partition("#")
    if not sep or not container_path or not dataset_path:
        raise ValueError(f"Invalid h5ref: {source_ref}")
    import h5py

    with h5py.File(container_path, "r") as h5f:
        obj = h5f[dataset_path]
        if hasattr(obj, "shape"):
            return np.asarray(obj[()])
        for name in ("processed_signal", "raw_signal", "signal", "image"):
            if name in obj:
                return np.asarray(obj[name][()])
        for child in obj.values():
            if hasattr(child, "shape"):
                return np.asarray(child[()])
    raise ValueError(f"No image dataset in {source_ref}")


def load_calibration_array(source: str | Path) -> np.ndarray:
    text = str(source or "").strip()
    if text.startswith("h5ref://"):
        return _load_array_from_h5ref(text)
    path = Path(text)
    suffix = path.suffix.lower()
    if suffix == ".npy":
        return np.load(path)
    if suffix in {".txt", ".csv", ".dat"}:
        delimiter = "," if suffix == ".csv" else None
        return np.loadtxt(path, delimiter=delimiter)
    raise ValueError(f"Unsupported calibration array source: {path}")


def export_calibration_image_for_pyfai(
    source: str | Path,
    *,
    output_dir: str | Path | None = None,
    alias: str = "",
    output_stem: str | None = None,
) -> Path:
    text = str(source or "").strip()
    if not text:
        raise ValueError("Calibration source is empty")
    if not text.startswith("h5ref://"):
        path = Path(text)
        if path.suffix.lower() in {".tif", ".tiff", ".edf", ".cbf", ".mar3450", ".img", ".mccd"}:
            return path
    arr = np.asarray(load_calibration_array(source), dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(f"Calibration image must be 2D, got shape {arr.shape}")
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

    if output_dir is None:
        if text.startswith("h5ref://"):
            output_root = Path.cwd()
            stem = "h5ref"
        else:
            output_root = Path(text).parent
            stem = Path(text).stem
    else:
        output_root = Path(output_dir)
        stem = "h5ref" if text.startswith("h5ref://") else Path(text).stem
    output_root.mkdir(parents=True, exist_ok=True)
    if output_stem:
        target = output_root / f"{_safe_token(output_stem)}_pyfai.tif"
    else:
        target = output_root / f"{_safe_token(stem)}_{_safe_token(alias, 'detector')}_pyfai.tif"

    from PIL import Image

    Image.fromarray(arr).save(target)
    return target


def build_pyfai_calib2_command(
    *,
    image_path: str | Path,
    poni_text: str,
    detector_config: Mapping | None,
    calibrant: str = DEFAULT_CALIBRANT,
    fix_rotations: bool = True,
) -> list[str]:
    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    command = [
        "pyfai-calib2",
        "-c",
        str(calibrant or DEFAULT_CALIBRANT),
        "-w",
        _format_float(wavelength_m * 1e10),
        "-D",
        pyfai_detector_name(detector_config),
        "--dist",
        _format_float(float(params.get("Distance", 0.1))),
        "--poni1",
        _format_float(float(params.get("Poni1", 0.0))),
        "--poni2",
        _format_float(float(params.get("Poni2", 0.0))),
        "--rot1",
        _format_float(float(params.get("Rot1", 0.0))),
        "--rot2",
        _format_float(float(params.get("Rot2", 0.0))),
        "--rot3",
        _format_float(float(params.get("Rot3", 0.0))),
        "--fix-wavelength",
    ]
    if fix_rotations:
        command.extend(
            [
                "--fix-rot1",
                "--fix-rot2",
                "--fix-rot3",
                "--no-tilt",
            ]
        )
    command.append(str(image_path))
    return command


def write_pyfai_calib2_launcher(
    *,
    output_dir: str | Path,
    command: Sequence[str],
    launcher_stem: str = "run_pyfai_calib2_with_difra_detector",
) -> Path:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    launcher = output_root / f"{_safe_token(launcher_stem)}.py"
    launcher.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import sys",
                "",
                "from pyFAI.app.calib2 import main",
                "from pyFAI.detectors._common import Detector",
                "from pyFAI.detectors import ALL_DETECTORS",
                "",
                "",
                "class Difra256x256Detector55um(Detector):",
                "    aliases = ['DIFRA-256-55UM', 'difra-256-55um']",
                "    MAX_SHAPE = (256, 256)",
                "    force_pixel = True",
                "",
                "    def __init__(self):",
                "        super().__init__(pixel1=55e-6, pixel2=55e-6, max_shape=(256, 256))",
                "",
                "",
                "ALL_DETECTORS['difra-256-55um'] = Difra256x256Detector55um",
                "ALL_DETECTORS['DIFRA-256-55UM'] = Difra256x256Detector55um",
                f"sys.argv = {json.dumps(list(command))}",
                "raise SystemExit(main())",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return launcher


def auto_poni_default_config() -> dict:
    return {
        "calibrant": DEFAULT_CALIBRANT,
        "energy_kev": DEFAULT_ENERGY_KEV,
        "first_visible_ring_by_alias": {
            "PRIMARY": 2,
            "SECONDARY": 5,
        },
        "first_visible_ring_by_distance_cm": {
            "2": {
                "PRIMARY": 2,
                "SECONDARY": 5,
            },
            "17": {
                "PRIMARY": 1,
                "SECONDARY": 1,
            },
            "18": {
                "PRIMARY": 1,
                "SECONDARY": 1,
            },
        },
        "rings_to_search_by_alias": {
            "PRIMARY": 3,
            "SECONDARY": 3,
        },
        "rings_to_search_by_distance_cm": {
            "2": {
                "PRIMARY": 3,
                "SECONDARY": 4,
            },
            "17": {
                "PRIMARY": 3,
                "SECONDARY": 3,
            },
            "18": {
                "PRIMARY": 3,
                "SECONDARY": 3,
            },
        },
        "rings_to_show": 3,
        "seed_distance_cm_by_distance_cm": {
            "2": {
                "PRIMARY": 2.30,
                "SECONDARY": 2.48,
            },
            "17": {
                "PRIMARY": 17.0,
                "SECONDARY": 17.0,
            },
        },
        "seed_center_px_by_alias": {
            "PRIMARY": [128.0, 10.0],
            "SECONDARY": [130.0, 306.0],
        },
    }


def normalized_auto_poni_config(config: Mapping | None) -> dict:
    cfg = config if isinstance(config, Mapping) else {}
    raw = cfg.get("auto_poni_calibration")
    if not isinstance(raw, Mapping):
        raw = cfg.get("auto_poni")
    raw = raw if isinstance(raw, Mapping) else {}
    defaults = auto_poni_default_config()
    first_visible = dict(defaults["first_visible_ring_by_alias"])
    configured = raw.get("first_visible_ring_by_alias")
    if isinstance(configured, Mapping):
        for alias, value in configured.items():
            try:
                ring = int(value)
            except (TypeError, ValueError):
                continue
            if ring > 0:
                first_visible[str(alias or "").strip().upper()] = ring
    by_distance = {
        str(distance_key or "").strip(): {
            str(alias or "").strip().upper(): int(ring)
            for alias, ring in rings.items()
            if str(alias or "").strip() and int(ring) > 0
        }
        for distance_key, rings in (
            defaults["first_visible_ring_by_distance_cm"].items()
        )
    }
    configured_by_distance = raw.get("first_visible_ring_by_distance_cm")
    if isinstance(configured_by_distance, Mapping):
        for distance_key, rings in configured_by_distance.items():
            if not isinstance(rings, Mapping):
                continue
            normalized_rings = {}
            for alias, value in rings.items():
                try:
                    ring = int(value)
                except (TypeError, ValueError):
                    continue
                alias_key = str(alias or "").strip().upper()
                if alias_key and ring > 0:
                    normalized_rings[alias_key] = ring
            if normalized_rings:
                by_distance[str(distance_key or "").strip()] = normalized_rings
    rings_by_alias = dict(defaults["rings_to_search_by_alias"])
    configured_rings_by_alias = raw.get("rings_to_search_by_alias")
    if isinstance(configured_rings_by_alias, Mapping):
        for alias, value in configured_rings_by_alias.items():
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            alias_key = str(alias or "").strip().upper()
            if alias_key and count > 0:
                rings_by_alias[alias_key] = count
    rings_by_distance = {
        str(distance_key or "").strip(): {
            str(alias or "").strip().upper(): int(count)
            for alias, count in rings.items()
            if str(alias or "").strip() and int(count) > 0
        }
        for distance_key, rings in (
            defaults["rings_to_search_by_distance_cm"].items()
        )
    }
    configured_rings_by_distance = raw.get("rings_to_search_by_distance_cm")
    if isinstance(configured_rings_by_distance, Mapping):
        for distance_key, rings in configured_rings_by_distance.items():
            if not isinstance(rings, Mapping):
                continue
            normalized_counts = {}
            for alias, value in rings.items():
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                alias_key = str(alias or "").strip().upper()
                if alias_key and count > 0:
                    normalized_counts[alias_key] = count
            if normalized_counts:
                rings_by_distance[str(distance_key or "").strip()] = normalized_counts
    seed_distance_by_distance = {
        str(distance_key or "").strip(): {
            str(alias or "").strip().upper(): float(distance_cm)
            for alias, distance_cm in distances.items()
            if str(alias or "").strip()
        }
        for distance_key, distances in (
            defaults["seed_distance_cm_by_distance_cm"].items()
        )
    }
    configured_seed_distance_by_distance = raw.get("seed_distance_cm_by_distance_cm")
    if isinstance(configured_seed_distance_by_distance, Mapping):
        for distance_key, distances in configured_seed_distance_by_distance.items():
            if not isinstance(distances, Mapping):
                continue
            normalized_distances = {}
            for alias, value in distances.items():
                try:
                    distance_cm = float(value)
                except (TypeError, ValueError):
                    continue
                alias_key = str(alias or "").strip().upper()
                if alias_key and distance_cm > 0.0:
                    normalized_distances[alias_key] = distance_cm
            if normalized_distances:
                seed_distance_by_distance[str(distance_key or "").strip()] = normalized_distances
    seed_center_by_alias = {
        str(alias or "").strip().upper(): [float(values[0]), float(values[1])]
        for alias, values in defaults["seed_center_px_by_alias"].items()
    }
    configured_seed_center_by_alias = raw.get("seed_center_px_by_alias")
    if isinstance(configured_seed_center_by_alias, Mapping):
        for alias, value in configured_seed_center_by_alias.items():
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                continue
            if len(value) < 2:
                continue
            try:
                center = [float(value[0]), float(value[1])]
            except (TypeError, ValueError):
                continue
            alias_key = str(alias or "").strip().upper()
            if alias_key:
                seed_center_by_alias[alias_key] = center
    try:
        rings_to_show = int(raw.get("rings_to_show", defaults["rings_to_show"]))
    except (TypeError, ValueError):
        rings_to_show = defaults["rings_to_show"]
    energy_source = raw.get(
        "energy_kev",
        cfg.get("xray_energy_kev", cfg.get("beam_energy_kev", defaults["energy_kev"])),
    )
    try:
        energy_kev = float(energy_source)
    except (TypeError, ValueError):
        energy_kev = defaults["energy_kev"]
    return {
        "calibrant": str(raw.get("calibrant") or defaults["calibrant"]),
        "energy_kev": energy_kev,
        "first_visible_ring_by_alias": first_visible,
        "first_visible_ring_by_distance_cm": by_distance,
        "rings_to_search_by_alias": rings_by_alias,
        "rings_to_search_by_distance_cm": rings_by_distance,
        "rings_to_show": max(1, rings_to_show),
        "seed_distance_cm_by_distance_cm": seed_distance_by_distance,
        "seed_center_px_by_alias": seed_center_by_alias,
    }


def auto_poni_distance_key(distance_cm) -> str:
    try:
        value = float(distance_cm)
    except (TypeError, ValueError):
        return ""
    rounded = round(value)
    if abs(value - rounded) <= 0.55:
        return str(int(rounded))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def auto_poni_seed_distance_cm(
    auto_config: Mapping | None,
    *,
    alias: str,
    nominal_distance_cm,
) -> float | None:
    try:
        nominal = float(nominal_distance_cm)
    except (TypeError, ValueError):
        return None
    if nominal <= 0.0:
        return None
    cfg = auto_config if isinstance(auto_config, Mapping) else {}
    by_distance = cfg.get("seed_distance_cm_by_distance_cm", {})
    distance_key = auto_poni_distance_key(nominal)
    alias_key = str(alias or "").strip().upper()
    if isinstance(by_distance, Mapping):
        distance_rules = by_distance.get(distance_key, {})
        if isinstance(distance_rules, Mapping):
            try:
                value = float(distance_rules.get(alias_key))
            except (TypeError, ValueError):
                value = None
            if value is not None and value > 0.0:
                return value
    return nominal


def auto_poni_seed_center_px(
    auto_config: Mapping | None,
    *,
    alias: str,
) -> tuple[float, float] | None:
    cfg = auto_config if isinstance(auto_config, Mapping) else {}
    centers = cfg.get("seed_center_px_by_alias", {})
    if not isinstance(centers, Mapping):
        return None
    alias_key = str(alias or "").strip().upper()
    value = centers.get(alias_key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def ring_two_theta_rad(*, wavelength_m: float, d_spacing_a: float) -> float | None:
    wavelength_a = float(wavelength_m) * 1e10
    d_value = float(d_spacing_a)
    if wavelength_a <= 0.0 or d_value <= 0.0:
        return None
    ratio = wavelength_a / (2.0 * d_value)
    if ratio <= 0.0 or ratio >= 1.0:
        return None
    import math

    return 2.0 * math.asin(ratio)


def build_agbh_ring_overlays(
    *,
    poni_text: str,
    detector_config: Mapping | None,
    first_visible_ring: int,
    rings_to_show: int = 8,
) -> list[dict]:
    params = parse_poni_parameters(poni_text)
    distance_m = float(params.get("Distance", 0.0))
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    pixel1, pixel2 = pixel_size_m(detector_config)
    if distance_m <= 0.0 or pixel1 <= 0.0 or pixel2 <= 0.0:
        return []

    center_row_px = float(params.get("Poni1", 0.0)) / pixel1
    center_col_px = float(params.get("Poni2", 0.0)) / pixel2
    first_ring = max(1, int(first_visible_ring or 1))
    max_ring = min(len(AGBH_D_SPACING_A), first_ring + max(1, int(rings_to_show)) - 1)

    import math

    overlays = []
    pixel_mean = (pixel1 + pixel2) / 2.0
    for ring_index in range(first_ring, max_ring + 1):
        two_theta = ring_two_theta_rad(
            wavelength_m=wavelength_m,
            d_spacing_a=AGBH_D_SPACING_A[ring_index - 1],
        )
        if two_theta is None:
            continue
        radius_m = distance_m * math.tan(two_theta)
        radius_px = radius_m / pixel_mean
        if not math.isfinite(radius_px) or radius_px <= 0.0:
            continue
        overlays.append(
            {
                "ring_index": ring_index,
                "d_spacing_a": AGBH_D_SPACING_A[ring_index - 1],
                "two_theta_rad": two_theta,
                "center_row_px": center_row_px,
                "center_col_px": center_col_px,
                "radius_px": radius_px,
            }
        )
    return overlays


def write_agbh_control_points_npt(
    *,
    poni_text: str,
    detector_config: Mapping | None,
    output_path: str | Path,
    first_visible_ring: int,
    rings_to_show: int = 4,
    calibrant: str = DEFAULT_CALIBRANT,
    points_per_ring: int = 24,
) -> Path:
    width, height = detector_size_px(detector_config)
    overlays = build_agbh_ring_overlays(
        poni_text=poni_text,
        detector_config=detector_config,
        first_visible_ring=first_visible_ring,
        rings_to_show=rings_to_show,
    )
    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))

    import math

    lines = [
        "# set of control point used by pyFAI to calibrate the geometry of a scattering experiment",
        "# angles are in radians, wavelength in meter and positions in pixels",
        f"calibrant: {calibrant} {wavelength_m}",
        f"wavelength: {wavelength_m}",
        "dspacing:" + " ".join(str(value) for value in AGBH_D_SPACING_A),
    ]
    group_index = 0
    point_count = max(8, int(points_per_ring))
    for overlay in overlays:
        ring_index = int(overlay["ring_index"])
        radius = float(overlay["radius_px"])
        center_col = float(overlay["center_col_px"])
        center_row = float(overlay["center_row_px"])
        points = []
        for idx in range(point_count):
            angle = 2.0 * math.pi * float(idx) / float(point_count)
            col = center_col + radius * math.cos(angle)
            row = center_row + radius * math.sin(angle)
            if 0.0 <= col < float(width) and 0.0 <= row < float(height):
                points.append((col, row))
        if len(points) < 3:
            continue
        lines.extend(
            [
                "",
                f"New group of points: {group_index}",
                f"2theta: {float(overlay['two_theta_rad'])}",
                f"ring: {ring_index - 1}",
            ]
        )
        for col, row in points:
            lines.append(f"point: x={_format_float(col)} y={_format_float(row)}")
        group_index += 1

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def write_agbh_clicked_points_npt(
    *,
    poni_text: str,
    output_path: str | Path,
    ring_index: int,
    points_col_row: Sequence[tuple[float, float]],
    calibrant: str = DEFAULT_CALIBRANT,
) -> Path:
    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    ring = max(1, int(ring_index or 1))
    two_theta = ring_two_theta_rad(
        wavelength_m=wavelength_m,
        d_spacing_a=AGBH_D_SPACING_A[ring - 1],
    )
    lines = [
        "# set of control point used by pyFAI to calibrate the geometry of a scattering experiment",
        "# angles are in radians, wavelength in meter and positions in pixels",
        f"calibrant: {calibrant} {wavelength_m}",
        f"wavelength: {wavelength_m}",
        "dspacing:" + " ".join(str(value) for value in AGBH_D_SPACING_A),
        "",
        "New group of points: 0",
        f"2theta: {_format_float(two_theta or 0.0)}",
        f"ring: {ring - 1}",
    ]
    for col, row in points_col_row:
        lines.append(f"point: x={_format_float(col)} y={_format_float(row)}")

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def write_agbh_points_by_ring_npt(
    *,
    poni_text: str,
    output_path: str | Path,
    points_by_ring: Mapping[int, Sequence[tuple[float, float]]],
    calibrant: str = DEFAULT_CALIBRANT,
) -> Path:
    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    lines = [
        "# set of control point used by pyFAI to calibrate the geometry of a scattering experiment",
        "# angles are in radians, wavelength in meter and positions in pixels",
        f"calibrant: {calibrant} {wavelength_m}",
        f"wavelength: {wavelength_m}",
        "dspacing:" + " ".join(str(value) for value in AGBH_D_SPACING_A),
    ]
    group_index = 0
    for ring in sorted(int(key) for key in points_by_ring.keys()):
        points = list(points_by_ring.get(ring, []) or [])
        if not points:
            continue
        d_spacing_index = min(len(AGBH_D_SPACING_A) - 1, max(0, ring - 1))
        two_theta = ring_two_theta_rad(
            wavelength_m=wavelength_m,
            d_spacing_a=AGBH_D_SPACING_A[d_spacing_index],
        )
        lines.extend(
            [
                "",
                f"New group of points: {group_index}",
                f"2theta: {_format_float(two_theta or 0.0)}",
                f"ring: {max(0, ring - 1)}",
            ]
        )
        for col, row in points:
            lines.append(f"point: x={_format_float(col)} y={_format_float(row)}")
        group_index += 1

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def refine_poni_from_clicked_ring_points(
    *,
    poni_text: str,
    detector_config: Mapping | None,
    ring_index: int,
    points_col_row: Sequence[tuple[float, float]],
    alias: str = "",
) -> str:
    points = np.asarray(points_col_row, dtype=float)
    if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2:
        raise ValueError("At least 3 clicked points are required")
    ring = max(1, int(ring_index or 1))
    params = parse_poni_parameters(poni_text)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    two_theta = ring_two_theta_rad(
        wavelength_m=wavelength_m,
        d_spacing_a=AGBH_D_SPACING_A[ring - 1],
    )
    if two_theta is None:
        raise ValueError(f"Ring {ring} is not available at current wavelength")

    x = points[:, 0]
    y = points[:, 1]
    matrix = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    rhs = x * x + y * y
    center_col, center_row, offset = np.linalg.lstsq(matrix, rhs, rcond=None)[0]
    radius_sq = float(offset + center_col * center_col + center_row * center_row)
    if radius_sq <= 0.0:
        raise ValueError("Clicked points do not define a valid ring")

    import math

    pixel1, pixel2 = pixel_size_m(detector_config)
    radius_px = math.sqrt(radius_sq)
    distance_m = radius_px * ((pixel1 + pixel2) / 2.0) / math.tan(two_theta)
    if not math.isfinite(distance_m) or distance_m <= 0.0:
        raise ValueError("Clicked points produced invalid distance")
    return build_seed_poni_text(
        detector_config=detector_config,
        distance_m=distance_m,
        alias=alias,
        existing_poni_text=poni_text,
        wavelength_m=wavelength_m,
        center_px=(float(center_row), float(center_col)),
    )


def run_headless_agbh_fit(
    *,
    source_image: str | Path,
    detector_config: Mapping | None,
    distance_m: float,
    output_dir: str | Path,
    alias: str = "",
    center_px: tuple[float, float] | None = None,
    wavelength_m: float | None = None,
    calibrant: str = DEFAULT_CALIBRANT,
    first_visible_ring: int = 1,
    rings_to_show: int = 8,
    points_per_degree: float = 0.25,
    output_prefix: str | None = None,
    retry_count: int = 5,
) -> HeadlessPoniFitResult:
    image = np.asarray(load_calibration_array(source_image), dtype=np.float64)
    if image.ndim != 2:
        raise ValueError(f"Calibration image must be 2D, got shape {image.shape}")

    from pyFAI.calibrant import get_calibrant
    from pyFAI.control_points import ControlPoints
    from pyFAI.detectors._common import Detector
    from pyFAI.goniometer import SingleGeometry

    width, height = detector_size_px(detector_config)
    pixel1, pixel2 = pixel_size_m(detector_config)
    if center_px is None:
        center_px = (float(height) / 2.0, float(width) / 2.0)
    row_px, col_px = center_px
    wavelength = float(wavelength_m or DEFAULT_WAVELENGTH_M)

    cal = get_calibrant(str(calibrant or DEFAULT_CALIBRANT))
    try:
        cal.wavelength = wavelength
    except Exception:
        cal.set_wavelength(wavelength)
    detector = Detector(pixel1=pixel1, pixel2=pixel2, max_shape=(height, width))
    geometry = {
        "dist": float(distance_m),
        "poni1": float(row_px) * pixel1,
        "poni2": float(col_px) * pixel2,
        "rot1": 0.0,
        "rot2": 0.0,
        "rot3": 0.0,
        "wavelength": wavelength,
        "detector": detector,
    }

    max_ring = max(1, int(first_visible_ring) + max(1, int(rings_to_show)) - 1)
    first_zero_based = max(0, int(first_visible_ring) - 1)
    last_zero_based = first_zero_based + max(1, int(rings_to_show)) - 1

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    alias_token = _safe_token(alias, "detector")
    if output_prefix:
        prefix = _safe_token(output_prefix, alias_token)
        npt_path = output_root / f"{prefix}.npt"
        poni_path = output_root / f"{prefix}.poni"
    else:
        stem = _safe_token(Path(str(source_image)).stem)
        npt_path = output_root / f"{stem}_{alias_token}_headless_fit.npt"
        poni_path = output_root / f"{stem}_{alias_token}_headless_fit.poni"

    best = None
    attempts = max(1, int(retry_count or 1))
    for attempt in range(attempts):
        single_geometry = SingleGeometry(
            label=str(alias or "detector"),
            image=image,
            calibrant=cal,
            detector=detector,
            geometry=dict(geometry),
        )
        extracted = single_geometry.extract_cp(
            max_rings=max_ring,
            pts_per_deg=float(points_per_degree),
        )

        filtered = ControlPoints(calibrant=cal)
        for row, col, ring in extracted.getList():
            ring_i = int(ring)
            if first_zero_based <= ring_i <= last_zero_based:
                filtered.append([(float(row), float(col))], ring_i)

        data = np.asarray(filtered.getList(), dtype=np.float64)
        if data.size == 0:
            continue

        refinement = single_geometry.geometry_refinement
        refinement.data = data
        chi2 = None
        refined = False
        if data.shape[0] >= 3:
            chi2 = float(
                refinement.refine3(
                    fix=["wavelength", "rot1", "rot2", "rot3"],
                )
            )
            refined = True

        attempt_poni_path = poni_path.with_name(
            f"{poni_path.stem}_attempt_{attempt}{poni_path.suffix}"
        )
        if attempt_poni_path.exists():
            attempt_poni_path.unlink()
        refinement.save(str(attempt_poni_path))
        poni_text = attempt_poni_path.read_text(encoding="utf-8")
        score = float(chi2) if chi2 is not None else float("inf")
        candidate = (
            score,
            -int(data.shape[0]),
            filtered,
            poni_text,
            int(data.shape[0]),
            bool(refined),
            chi2,
            attempt_poni_path,
        )
        if best is None or candidate[:2] < best[:2]:
            best = candidate

    if best is None:
        raise ValueError("pyFAI did not extract any control points")

    _score, _neg_points, filtered, poni_text, point_count, refined, chi2, best_attempt_path = best
    filtered.save(str(npt_path))
    if poni_path.exists():
        poni_path.unlink()
    poni_path.write_text(poni_text, encoding="utf-8")
    for attempt_path in output_root.glob(f"{poni_path.stem}_attempt_*{poni_path.suffix}"):
        if attempt_path != best_attempt_path:
            try:
                attempt_path.unlink()
            except OSError:
                pass
    try:
        best_attempt_path.unlink()
    except OSError:
        pass
    return HeadlessPoniFitResult(
        poni_path=poni_path,
        poni_text=poni_text,
        npt_path=npt_path,
        extracted_points=point_count,
        refined=refined,
        chi2=chi2,
    )


def is_headless_agbh_fit_plausible(
    fit_result: HeadlessPoniFitResult,
    *,
    seed_poni_text: str,
    detector_config: Mapping | None,
    min_points: int = 12,
    max_distance_change_fraction: float = 0.35,
    max_center_shift_px: float = 64.0,
) -> bool:
    if fit_result.extracted_points < int(min_points):
        return False

    seed = parse_poni_parameters(seed_poni_text)
    fitted = parse_poni_parameters(fit_result.poni_text)
    seed_distance = _to_float(seed.get("Distance"))
    fit_distance = _to_float(fitted.get("Distance"))
    if seed_distance is None or fit_distance is None or seed_distance <= 0.0:
        return False
    distance_fraction = abs(fit_distance - seed_distance) / seed_distance
    if distance_fraction > float(max_distance_change_fraction):
        return False

    pixel1, pixel2 = pixel_size_m(detector_config)
    seed_poni1 = _to_float(seed.get("Poni1"))
    seed_poni2 = _to_float(seed.get("Poni2"))
    fit_poni1 = _to_float(fitted.get("Poni1"))
    fit_poni2 = _to_float(fitted.get("Poni2"))
    if None in (seed_poni1, seed_poni2, fit_poni1, fit_poni2):
        return False
    row_shift = abs(float(fit_poni1) - float(seed_poni1)) / pixel1
    col_shift = abs(float(fit_poni2) - float(seed_poni2)) / pixel2
    return max(row_shift, col_shift) <= float(max_center_shift_px)


def prepare_agbh_calib2_review(
    *,
    source_image: str | Path,
    detector_config: Mapping | None,
    distance_m: float,
    alias: str = "",
    output_dir: str | Path | None = None,
    existing_poni_text: str = "",
    wavelength_m: float | None = None,
    calibrant: str = DEFAULT_CALIBRANT,
    center_px: tuple[float, float] | None = None,
    first_visible_ring: int | None = None,
    rings_to_show: int = 4,
    output_prefix: str | None = None,
) -> PyfaiCalib2Review:
    prefix = _safe_token(output_prefix, _safe_token(alias, "detector")) if output_prefix else ""
    output_root = Path(output_dir) if output_dir is not None else None
    image_path = export_calibration_image_for_pyfai(
        source_image,
        output_dir=output_root,
        alias=alias,
        output_stem=prefix or None,
    )
    if output_root is None:
        output_root = image_path.parent
    output_root.mkdir(parents=True, exist_ok=True)

    poni_text = build_seed_poni_text(
        detector_config=detector_config,
        distance_m=float(distance_m),
        alias=alias,
        existing_poni_text=existing_poni_text,
        wavelength_m=None if wavelength_m is None else float(wavelength_m),
        center_px=center_px,
    )
    if prefix:
        poni_path = output_root / f"{prefix}.poni"
    else:
        poni_path = output_root / f"{_safe_token(image_path.stem)}_{_safe_token(alias, 'detector')}_seed.poni"
    poni_path.write_text(poni_text, encoding="utf-8")
    command = build_pyfai_calib2_command(
        image_path=image_path,
        poni_text=poni_text,
        detector_config=detector_config,
        calibrant=calibrant,
    )
    if first_visible_ring is not None:
        if prefix:
            npt_path = output_root / f"{prefix}.npt"
        else:
            npt_path = output_root / f"{_safe_token(image_path.stem)}_{_safe_token(alias, 'detector')}_seed.npt"
        write_agbh_control_points_npt(
            poni_text=poni_text,
            detector_config=detector_config,
            output_path=npt_path,
            first_visible_ring=int(first_visible_ring),
            rings_to_show=int(rings_to_show),
            calibrant=calibrant,
        )
        command = [*command[:-1], "-n", str(npt_path), command[-1]]
    return PyfaiCalib2Review(
        image_path=image_path,
        poni_path=poni_path,
        command=command,
        poni_text=poni_text,
        source_path=None if str(source_image).startswith("h5ref://") else Path(source_image),
    )
