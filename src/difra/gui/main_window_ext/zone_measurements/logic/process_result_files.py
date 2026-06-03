"""File helpers for zone measurement capture results."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

import numpy as np


def measurement_ref_to_filename(measurement_ref: Any) -> str:
    if isinstance(measurement_ref, dict):
        value = measurement_ref.get("filename")
    else:
        value = measurement_ref
    return str(value or "").strip()


def find_nearby_dsc(
    folder: Path,
    *,
    base_name: str,
    alias: str,
    reference_path: Path,
) -> Optional[Path]:
    folder = Path(folder)
    exact_candidates = [
        folder / f"{base_name}.txt.dsc",
        folder / f"{base_name}.dsc",
    ]
    for candidate in exact_candidates:
        if candidate.exists():
            return candidate

    alias_token = str(alias or "").strip().upper()
    candidates = []
    for candidate in folder.glob("*.dsc"):
        name_upper = candidate.name.upper()
        if alias_token and alias_token not in name_upper:
            continue
        candidates.append(candidate)
    if not candidates:
        return None

    try:
        reference_mtime = Path(reference_path).stat().st_mtime
    except Exception:
        reference_mtime = None
    if reference_mtime is None:
        return sorted(candidates, key=lambda path: path.name)[0]
    return min(candidates, key=lambda path: abs(path.stat().st_mtime - reference_mtime))


def build_session_measurement_result_refs(
    *,
    session_manager: Any,
    measurement_path: Optional[str],
    result_files: Mapping,
    detector_lookup: Mapping,
) -> dict:
    """Return container-backed refs for a just-written session measurement."""
    refs = dict(result_files or {})
    if not session_manager or not measurement_path:
        return refs

    session_path = getattr(session_manager, "session_path", None)
    schema = getattr(session_manager, "schema", None)
    if not session_path or schema is None:
        return refs

    dataset_name = getattr(schema, "DATASET_PROCESSED_SIGNAL", "processed_signal")
    format_detector_role = getattr(schema, "format_detector_role", None)

    for alias, source_path in list(refs.items()):
        if not source_path:
            continue

        role = None
        if callable(format_detector_role):
            try:
                role = str(format_detector_role(alias) or "").strip()
            except (AttributeError, TypeError, ValueError):
                role = None

        if not role:
            detector_meta = detector_lookup.get(alias, {})
            detector_id = str(detector_meta.get("id") or alias or "").strip()
            if not detector_id:
                continue
            role = (
                detector_id
                if detector_id.startswith("det_")
                else f"det_{detector_id.lower()}"
            )

        refs[alias] = (
            f"h5ref://{session_path}"
            f"#{measurement_path}/{role}/{dataset_name}"
        )

    return refs


def collect_capture_payloads(
    *,
    result_files: Mapping,
    detector_lookup: Mapping,
    detector_controller: Mapping,
    logger: Any,
) -> dict:
    all_data = {}
    raw_files_data = {}
    raw_paths_by_alias = {}
    poni_alias_map = {}

    for alias, npy_file in (result_files or {}).items():
        if not npy_file:
            continue
        npy_path = Path(npy_file)
        if not npy_path.exists():
            logger.warning(
                "Capture file missing on disk",
                detector_alias=alias,
                file=str(npy_file),
            )
            continue
        detector_meta = detector_lookup.get(alias, {})
        detector_id = detector_meta.get("id", alias)
        poni_alias_map[alias] = detector_id
        logger.info(f"Loading {alias} data from: {npy_path.name}")
        all_data[detector_id] = np.load(npy_file)
        logger.info(f"  Data shape: {all_data[detector_id].shape}")

        base_name = npy_path.stem
        folder = npy_path.parent

        detector = detector_controller.get(alias)
        if detector and hasattr(detector, "get_raw_file_patterns"):
            patterns = detector.get_raw_file_patterns()
        else:
            patterns = ["*.txt", "*.dsc", "*.t3pa"]
            logger.warning(
                f"Detector {alias} has no get_raw_file_patterns(), using default patterns"
            )

        raw_files = {}
        raw_paths = {}
        for pattern in patterns:
            ext = pattern[1:] if pattern.startswith("*") else pattern
            raw_file = folder / f"{base_name}{ext}"
            if ext == ".dsc" and not raw_file.exists():
                nearby = find_nearby_dsc(
                    folder,
                    base_name=base_name,
                    alias=alias,
                    reference_path=npy_path,
                )
                if nearby is not None:
                    raw_file = nearby
            if raw_file.exists():
                try:
                    with open(raw_file, "rb") as file_handle:
                        file_format = ext[1:] if ext.startswith(".") else ext
                        blob_key = f"raw_{file_format}"
                        raw_files[blob_key] = file_handle.read()
                        raw_paths[blob_key] = str(raw_file)
                    logger.debug(f"Read raw file for blob: {raw_file.name} -> {blob_key}")
                except OSError as exc:
                    logger.warning(f"Failed to read raw file {raw_file}: {exc}")
        if "raw_txt" in raw_files and "raw_dsc" not in raw_files:
            nearby_dsc = sorted(folder.glob("*.dsc"))
            logger.warning(
                "Raw TXT found but matching DSC is missing",
                detector_alias=alias,
                expected=str(folder / f"{base_name}.dsc"),
                nearby_dsc=[str(path) for path in nearby_dsc],
            )

        if raw_files:
            raw_files_data[detector_id] = raw_files
            raw_paths_by_alias[alias] = raw_paths
            logger.info(
                f"  Found {len(raw_files)} raw files for {alias}: {list(raw_files.keys())}"
            )
        else:
            logger.warning(f"  No raw files found for {alias} using patterns {patterns}")
            raw_paths_by_alias.setdefault(alias, {})

    return {
        "all_data": all_data,
        "raw_files_data": raw_files_data,
        "raw_paths_by_alias": raw_paths_by_alias,
        "poni_alias_map": poni_alias_map,
    }
