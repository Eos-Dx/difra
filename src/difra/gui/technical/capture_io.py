"""Capture file and HDF5-reference helpers."""

import os
from pathlib import Path
import shutil

import numpy as np


def _dsc_candidates(path: Path):
    path = Path(path)
    candidates = [Path(str(path) + ".dsc"), path.with_suffix(".dsc")]
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        yield candidate


def _decode_h5_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _inspect_embedded_poni(
    measurement_filename: str,
    provided_poni_text: str | None = None,
) -> dict:
    info = {
        "measurement_ref": str(measurement_filename or "").strip(),
        "container_path": "",
        "measurement_dataset_path": "",
        "detector_group_path": "",
        "detector_alias": "",
        "detector_id": "",
        "measurement_source_file": "",
        "resolved_poni_path": "",
        "resolved_poni_filename": "",
        "resolved_poni_text": "",
        "provided_poni_text": str(provided_poni_text or ""),
        "resolution_error": "",
    }

    ref_value = info["measurement_ref"]
    if not ref_value.startswith("h5ref://"):
        return info

    try:
        import h5py
    except Exception as exc:
        info["resolution_error"] = f"h5py unavailable: {exc}"
        return info

    payload = ref_value[len("h5ref://") :]
    container_path, sep, dataset_path = payload.partition("#")
    info["container_path"] = str(container_path or "").strip()
    info["measurement_dataset_path"] = str(dataset_path or "").strip()
    if not sep or not info["container_path"] or not info["measurement_dataset_path"]:
        info["resolution_error"] = f"Invalid H5 reference: {measurement_filename}"
        return info

    try:
        with h5py.File(info["container_path"], "r") as h5f:
            if info["measurement_dataset_path"] not in h5f:
                info["resolution_error"] = (
                    "Measurement dataset not found in container: "
                    f"{info['measurement_dataset_path']}"
                )
                return info

            dataset = h5f[info["measurement_dataset_path"]]
            detector_group = dataset.parent
            info["detector_group_path"] = str(detector_group.name or "")
            info["detector_alias"] = _decode_h5_text(
                detector_group.attrs.get("detector_alias", "")
            ).strip()
            info["detector_id"] = _decode_h5_text(
                detector_group.attrs.get("detector_id", "")
            ).strip()
            info["measurement_source_file"] = _decode_h5_text(
                detector_group.attrs.get("source_file", "")
            ).strip()

            candidate_paths = []
            for attr_name in ("poni_ref", "poni_path"):
                ref_path = _decode_h5_text(
                    detector_group.attrs.get(attr_name, "")
                ).strip()
                if ref_path and ref_path not in candidate_paths:
                    candidate_paths.append(ref_path)

            role_name = str(detector_group.name.rsplit("/", 1)[-1] or "").strip()
            if role_name.startswith("det_"):
                for suffix in (role_name[4:], role_name):
                    canonical_path = f"/entry/technical/poni/poni_{suffix}"
                    if canonical_path not in candidate_paths:
                        candidate_paths.append(canonical_path)

            for ref_path in candidate_paths:
                if not ref_path or ref_path not in h5f:
                    continue
                try:
                    poni_dataset = h5f[ref_path]
                    info["resolved_poni_path"] = ref_path
                    info["resolved_poni_filename"] = _decode_h5_text(
                        poni_dataset.attrs.get("poni_filename", "")
                    ).strip()
                    info["resolved_poni_text"] = _decode_h5_text(
                        poni_dataset[()]
                    ).strip()
                    if info["resolved_poni_text"]:
                        break
                except Exception as exc:
                    info["resolution_error"] = (
                        f"Failed reading PONI dataset '{ref_path}': {exc}"
                    )
    except Exception as exc:
        info["resolution_error"] = f"Failed reading H5 diagnostics: {exc}"

    return info


def _load_measurement_array(measurement_filename: str) -> np.ndarray:
    value = str(measurement_filename or "").strip()
    if value.startswith("h5ref://"):
        # Format: h5ref://<absolute-container-path>#<dataset_path>
        import h5py

        payload = value[len("h5ref://") :]
        container_path, sep, dataset_path = payload.partition("#")
        if not sep or not container_path or not dataset_path:
            raise ValueError(f"Invalid H5 reference: {measurement_filename}")

        container = Path(container_path)
        if not container.exists():
            raise FileNotFoundError(f"H5 container does not exist: {container}")

        with h5py.File(container, "r") as h5f:
            if dataset_path not in h5f:
                raise KeyError(
                    f"Dataset not found in container: {container}#{dataset_path}"
                )
            data = h5f[dataset_path][()]
            arr = np.asarray(data, dtype=float)
            if arr.ndim != 2:
                raise ValueError(f"Expected 2D array, got shape {arr.shape}")
            return arr

    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(f"Measurement file does not exist: {path}")

    suffix = path.suffix.lower()
    if suffix == ".txt":
        loaders = (np.loadtxt, np.load)
    elif suffix == ".npy":
        loaders = (np.load, np.loadtxt)
    else:
        loaders = (np.load, np.loadtxt)

    last_error = None
    for loader in loaders:
        try:
            data = loader(path)
            arr = np.asarray(data, dtype=float)
            if arr.ndim != 2:
                raise ValueError(f"Expected 2D array, got shape {arr.shape}")
            return arr
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Failed to load measurement file '{path}': {last_error}")


def _place_raw_capture_file(
    src_raw: str, target_txt: Path, allow_move: bool = True
) -> None:
    """Place raw detector output at target path, preferring move over copy."""
    src_path = Path(src_raw)
    target_txt = Path(target_txt)
    target_txt.parent.mkdir(parents=True, exist_ok=True)
    dst_dsc = Path(str(target_txt) + ".dsc")
    src_dsc = next((path for path in _dsc_candidates(src_path) if path.exists()), None)

    if src_path.resolve() == target_txt.resolve():
        if src_dsc is not None and not dst_dsc.exists():
            shutil.copy2(src_dsc, dst_dsc)
        return

    moved = False
    if allow_move:
        try:
            shutil.move(str(src_path), str(target_txt))
            moved = True
        except Exception:
            moved = False

    if not moved:
        shutil.copy2(src_path, target_txt)

    if src_dsc is not None:
        if moved:
            try:
                shutil.move(str(src_dsc), str(dst_dsc))
            except Exception:
                shutil.copy2(src_dsc, dst_dsc)
        else:
            shutil.copy2(src_dsc, dst_dsc)


def validate_folder(path: str):
    if not path:
        path = os.getcwd()
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        path = os.getcwd()
    if not os.access(path, os.W_OK):
        path = os.getcwd()
    return Path(path)
