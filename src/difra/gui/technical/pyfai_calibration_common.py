"""Shared pyFAI calibration types, constants, and seed helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Mapping, Sequence

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
    token = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or "")
    )
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
    elif (
        isinstance(size, Sequence)
        and not isinstance(size, (str, bytes))
        and len(size) >= 2
    ):
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
    if (
        (width, height) == (256, 256)
        and abs(pixel1 - 55e-6) < 1e-12
        and abs(pixel2 - 55e-6) < 1e-12
    ):
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
