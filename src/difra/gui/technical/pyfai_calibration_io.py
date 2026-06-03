"""pyFAI calibration image IO and calib2 command helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from difra.gui.technical.pyfai_calibration_common import (
    DEFAULT_CALIBRANT,
    DEFAULT_WAVELENGTH_M,
    _format_float,
    _safe_token,
    parse_poni_parameters,
    pyfai_detector_name,
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
        if path.suffix.lower() in {
            ".tif",
            ".tiff",
            ".edf",
            ".cbf",
            ".mar3450",
            ".img",
            ".mccd",
        }:
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
        target = (
            output_root
            / f"{_safe_token(stem)}_{_safe_token(alias, 'detector')}_pyfai.tif"
        )

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
