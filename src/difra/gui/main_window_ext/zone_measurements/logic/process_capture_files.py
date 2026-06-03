"""File and settings helpers for zone measurement capture."""

from __future__ import annotations

from pathlib import Path


def find_capture_dsc(
    folder: Path,
    *,
    base_name: str,
    alias: str,
    reference_path: Path,
) -> Path | None:
    candidates = [
        folder / f"{base_name}.txt.dsc",
        folder / f"{base_name}.dsc",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    alias_token = str(alias or "").strip().upper()
    nearby = []
    for candidate in folder.glob("*.dsc"):
        if alias_token and alias_token not in candidate.name.upper():
            continue
        nearby.append(candidate)
    if not nearby:
        return None

    try:
        reference_mtime = reference_path.stat().st_mtime
    except (OSError, RuntimeError):
        return sorted(nearby, key=lambda path: path.name)[0]
    return min(nearby, key=lambda path: abs(path.stat().st_mtime - reference_mtime))


def read_capture_raw_sidecars(npy_file: str, alias: str) -> tuple[dict, dict]:
    npy_path = Path(npy_file)
    folder = npy_path.parent
    base_name = npy_path.stem
    raw_files = {}
    raw_paths = {}

    txt_path = next(
        (path for path in (folder / f"{base_name}.txt", folder / base_name) if path.exists()),
        None,
    )
    if txt_path is not None:
        raw_files["raw_txt"] = txt_path.read_bytes()
        raw_paths["raw_txt"] = str(txt_path)

    dsc_path = find_capture_dsc(
        folder,
        base_name=base_name,
        alias=alias,
        reference_path=npy_path,
    )
    if dsc_path is not None:
        raw_files["raw_dsc"] = dsc_path.read_bytes()
        raw_paths["raw_dsc"] = str(dsc_path)

    return raw_files, raw_paths


def attenuation_capture_settings(config) -> tuple[int, float]:
    att = {}
    if isinstance(config, dict):
        raw_att = config.get("attenuation", {})
        att = raw_att if isinstance(raw_att, dict) else {}

    def _positive_int(value, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return int(default)
        return max(parsed, 1)

    def _positive_float(value, default: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return float(default)
        if parsed <= 0:
            return float(default)
        return parsed

    frames = _positive_int(att.get("frames"), 100)
    integration_time_s = _positive_float(
        att.get("integration_time_s"),
        0.000001,
    )
    return frames, integration_time_s
