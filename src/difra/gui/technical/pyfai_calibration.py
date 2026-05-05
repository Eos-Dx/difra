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
) -> list[str]:
    params = parse_poni_parameters(poni_text)
    pixel1, pixel2 = pixel_size_m(detector_config)
    wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
    command = [
        "pyfai-calib2",
        "-c",
        str(calibrant or DEFAULT_CALIBRANT),
        "-w",
        _format_float(wavelength_m * 1e10),
        "-p",
        f"{_format_float(pixel1 * 1e6)},{_format_float(pixel2 * 1e6)}",
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
        str(image_path),
    ]
    return command


def auto_poni_default_config() -> dict:
    return {
        "calibrant": DEFAULT_CALIBRANT,
        "first_visible_ring_by_alias": {
            "PRIMARY": 3,
            "SECONDARY": 5,
        },
        "rings_to_show": 8,
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
    try:
        rings_to_show = int(raw.get("rings_to_show", defaults["rings_to_show"]))
    except (TypeError, ValueError):
        rings_to_show = defaults["rings_to_show"]
    return {
        "calibrant": str(raw.get("calibrant") or defaults["calibrant"]),
        "first_visible_ring_by_alias": first_visible,
        "rings_to_show": max(1, rings_to_show),
    }


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
) -> PyfaiCalib2Review:
    output_root = Path(output_dir) if output_dir is not None else None
    image_path = export_calibration_image_for_pyfai(
        source_image,
        output_dir=output_root,
        alias=alias,
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
    poni_path = output_root / f"{_safe_token(image_path.stem)}_{_safe_token(alias, 'detector')}_seed.poni"
    poni_path.write_text(poni_text, encoding="utf-8")
    command = build_pyfai_calib2_command(
        image_path=image_path,
        poni_text=poni_text,
        detector_config=detector_config,
        calibrant=calibrant,
    )
    return PyfaiCalib2Review(
        image_path=image_path,
        poni_path=poni_path,
        command=command,
        poni_text=poni_text,
    )
