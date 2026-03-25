"""Export session containers into legacy folder layout used by older DIFRA flows."""

import base64
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import io
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import h5py
import numpy as np
from PIL import Image


@dataclass
class OldFormatExportSummary:
    """Summary for one legacy export operation."""

    export_dir: Path
    state_path: Path
    raw_file_count: int
    technical_file_count: int


class SessionOldFormatExporter:
    """Create old-style folder structure from a session container."""

    TECH_TYPE_FILE_PREFIX = {
        "DARK": "DC",
        "EMPTY": "Empty",
        "AGBH": "AgBH",
        "BACKGROUND": "Bg",
    }

    @staticmethod
    def _as_text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _safe_token(value: str, fallback: str = "unknown") -> str:
        token = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (value or ""))
        token = token.strip("_")
        return token or fallback

    @staticmethod
    def _read_blob_bytes(dataset) -> bytes:
        payload = dataset[()]
        if isinstance(payload, bytes):
            return payload
        array = np.asarray(payload)
        return array.tobytes()

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _npy_bytes(array: Any) -> bytes:
        buffer = io.BytesIO()
        np.save(buffer, np.asarray(array))
        return buffer.getvalue()

    @staticmethod
    def _write_bytes_if_changed(path: Path, payload: bytes) -> bool:
        path = Path(path)
        try:
            if path.exists() and path.is_file() and path.read_bytes() == payload:
                return False
        except Exception:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return True

    @staticmethod
    def _unique_path(folder: Path, filename: str) -> Path:
        folder = Path(folder)
        candidate = folder / filename
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        idx = 2
        while True:
            alt = folder / f"{stem}_{idx}{suffix}"
            if not alt.exists():
                return alt
            idx += 1

    @classmethod
    def _normalize_date_token(cls, value: str) -> Optional[str]:
        text = cls._as_text(value, "").strip()
        if not text:
            return None

        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, fmt).strftime("%Y%m%d")
            except Exception:
                pass

        match = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
        if match:
            return f"{match.group(1)}{match.group(2)}{match.group(3)}"
        return None

    @classmethod
    def _normalize_timestamp_token(cls, value: Any, fallback_day: str) -> str:
        text = cls._as_text(value, "").strip()
        if not text:
            return f"{fallback_day}_{time.strftime('%H%M%S')}"

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y%m%d_%H%M%S",
            "%Y%m%d%H%M%S",
        ):
            try:
                return datetime.strptime(text, fmt).strftime("%Y%m%d_%H%M%S")
            except Exception:
                pass

        match = re.search(
            r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})[ T_]?([0-2]\d):?([0-5]\d):?([0-5]\d)",
            text,
        )
        if match:
            return (
                f"{match.group(1)}{match.group(2)}{match.group(3)}_"
                f"{match.group(4)}{match.group(5)}{match.group(6)}"
            )

        day = cls._normalize_date_token(text) or fallback_day
        return f"{day}_{time.strftime('%H%M%S')}"

    @classmethod
    def _resolve_day_token(cls, *, acquisition_date: str, fallback_timestamps: List[str]) -> str:
        token = cls._normalize_date_token(acquisition_date)
        if token:
            return token
        for ts in fallback_timestamps:
            token = cls._normalize_date_token(ts)
            if token:
                return token
        return time.strftime("%Y%m%d")

    @staticmethod
    def _format_coord_token(value: Optional[float]) -> str:
        try:
            v = float(value)
        except Exception:
            return "0.00"
        if abs(v) < 0.005:
            v = 0.0
        return f"{v:.2f}"

    @staticmethod
    def _distance_int(value: Optional[float], default: int = 17) -> int:
        if value is None:
            return int(default)
        try:
            return max(1, int(round(float(value))))
        except Exception:
            return int(default)

    @staticmethod
    def _distance_token(distance_int: int) -> str:
        return f"{int(distance_int)}cm"

    @classmethod
    def _integration_token(cls, seconds: Optional[float], event_type: str) -> str:
        if seconds is None:
            seconds = 60.0 if str(event_type or "").upper() == "DARK" else 300.0
        try:
            value = max(float(seconds), 0.0)
        except Exception:
            value = 0.0
        if abs(value - round(value)) < 1e-6:
            return f"{int(round(value))}s"
        token = f"{value:.6f}".rstrip("0").rstrip(".")
        return f"{token}s"

    @classmethod
    def _extract_distance_from_attrs(cls, attrs: Any) -> Optional[float]:
        for key in ("distance_cm", "detector_distance_cm"):
            try:
                if key in attrs:
                    value = cls._to_float(attrs.get(key))
                    if value is not None:
                        return value
            except Exception:
                continue
        return None

    @classmethod
    def resolve_old_format_root(
        cls,
        *,
        config: Optional[Dict[str, Any]] = None,
        archive_folder: Optional[Path] = None,
    ) -> Path:
        cfg = config or {}
        configured = cfg.get("old_format_export_folder") or cfg.get("legacy_export_folder")
        if configured:
            return Path(configured)

        difra_base = cfg.get("difra_base_folder")
        if difra_base:
            return Path(difra_base) / "Old_format"

        if archive_folder is not None:
            af = Path(archive_folder)
            if af.name == "measurements" and af.parent.name == "archive":
                return af.parent.parent / "Old_format"
            return af.parent / "Old_format"

        return Path("/Data/difra/Old_format")

    @classmethod
    def _extract_point_coordinates(
        cls,
        h5f: h5py.File,
        point_name: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        points_group = h5f.get("/entry/points")
        if points_group is None or point_name not in points_group:
            return None, None
        point_group = points_group[point_name]
        coords = point_group.attrs.get("physical_coordinates_mm")
        if coords is None:
            return None, None
        arr = np.asarray(coords).flatten().tolist()
        if len(arr) < 2:
            return None, None
        try:
            return float(arr[0]), float(arr[1])
        except Exception:
            return None, None

    @classmethod
    def _build_measurement_points(cls, h5f: h5py.File) -> List[Dict[str, Any]]:
        points: List[Dict[str, Any]] = []
        points_group = h5f.get("/entry/points")
        if points_group is None:
            return points

        for point_name in sorted(points_group.keys()):
            if not str(point_name).startswith("pt_"):
                continue
            x_mm, y_mm = cls._extract_point_coordinates(h5f, point_name)
            try:
                point_index = int(str(point_name).split("_")[-1])
            except Exception:
                point_index = len(points) + 1
            points.append(
                {
                    "point_index": point_index,
                    "unique_id": str(point_name),
                    "x": x_mm,
                    "y": y_mm,
                }
            )
        return points

    @classmethod
    def _load_state_payload(cls, h5f: h5py.File) -> Dict[str, Any]:
        embedded_state = h5f.attrs.get("meta_json")
        if embedded_state is None:
            return {}
        try:
            parsed = json.loads(cls._as_text(embedded_state, "{}"))
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}

    @classmethod
    def _derive_sample_folder_name(
        cls,
        *,
        state_payload: Dict[str, Any],
        sample_id: str,
        study_name: str,
        session_id: str,
    ) -> str:
        image_ref = cls._as_text(state_payload.get("image"), "").strip()
        if image_ref:
            try:
                parent_name = Path(image_ref.replace("\\", "/")).parent.name
                if parent_name:
                    return parent_name
            except Exception:
                pass

        for key in ("sample_folder", "sample_name", "sample_id"):
            value = cls._as_text(state_payload.get(key), "").strip()
            if value:
                return value

        if sample_id:
            return sample_id
        if study_name:
            return study_name
        return session_id or "sample"

    @classmethod
    def _derive_sample_base_with_distance(
        cls,
        *,
        state_payload: Dict[str, Any],
        sample_id: str,
        distance_token: str,
    ) -> str:
        counter: Counter = Counter()
        existing_meta = state_payload.get("measurements_meta")
        if isinstance(existing_meta, dict):
            for value in existing_meta.values():
                if not isinstance(value, dict):
                    continue
                base_file = cls._as_text(value.get("base_file"), "").strip()
                if base_file:
                    counter[base_file] += 1

        if counter:
            base_candidate = counter.most_common(1)[0][0]
        else:
            base_candidate = cls._as_text(
                state_payload.get("base_file") or state_payload.get("sample_id") or sample_id,
                sample_id,
            )

        base_candidate = cls._safe_token(base_candidate, "sample")
        suffix_re = re.compile(r"_[0-9]+cm$", re.IGNORECASE)
        if base_candidate.lower().endswith(f"_{distance_token.lower()}"):
            return base_candidate
        base_core = suffix_re.sub("", base_candidate)
        return f"{base_core}_{distance_token}"

    @classmethod
    def _export_session_image(
        cls,
        *,
        state_payload: Dict[str, Any],
        h5f: Optional[h5py.File],
        export_dir: Path,
        sample_id: str,
        session_id: str,
    ) -> Optional[Path]:
        """Export JPG image referenced by state payload into legacy export folder."""
        image_ref = cls._as_text(state_payload.get("image"), "").strip()
        image_ref_posix = image_ref.replace("\\", "/")
        image_name = Path(image_ref_posix).name if image_ref_posix else ""

        if not image_name:
            image_name = (
                f"{cls._safe_token(sample_id, 'sample')}_"
                f"{cls._safe_token(session_id, 'session')}.jpg"
            )

        candidate_sources = []
        if image_ref:
            candidate_sources.append(Path(image_ref))
        if image_ref_posix and image_ref_posix != image_ref:
            candidate_sources.append(Path(image_ref_posix))

        for source_path in candidate_sources:
            try:
                if source_path.exists() and source_path.is_file():
                    payload = source_path.read_bytes()
                    direct_target = export_dir / image_name
                    if direct_target.exists() and direct_target.read_bytes() != payload:
                        direct_target = cls._unique_path(export_dir, image_name)
                    direct_target.write_bytes(payload)
                    return direct_target
            except Exception:
                continue

        image_b64 = state_payload.get("image_base64") or state_payload.get("image_b64")
        if image_b64:
            try:
                payload = cls._as_text(image_b64, "").strip()
                if "," in payload and "base64" in payload[:40].lower():
                    payload = payload.split(",", 1)[1]
                image_bytes = base64.b64decode(payload, validate=False)
                if image_bytes:
                    exported_path = export_dir / image_name
                    if exported_path.exists() and exported_path.read_bytes() != image_bytes:
                        exported_path = cls._unique_path(export_dir, image_name)
                    exported_path.write_bytes(image_bytes)
                    return exported_path
            except Exception:
                pass

        if h5f is None:
            return None

        image_bytes = cls._extract_image_bytes_from_container(h5f)
        if not image_bytes:
            return None
        try:
            exported_path = export_dir / image_name
            if exported_path.exists() and exported_path.read_bytes() != image_bytes:
                exported_path = cls._unique_path(export_dir, image_name)
            exported_path.write_bytes(image_bytes)
            return exported_path
        except Exception:
            return None

    @classmethod
    def _extract_image_bytes_from_container(cls, h5f: h5py.File) -> Optional[bytes]:
        """Best-effort recovery of a representative JPG from session image datasets."""
        images_group = h5f.get("/entry/images")
        if images_group is None:
            images_group = h5f.get("/images")
        if images_group is None:
            return None

        image_groups = []
        for key in sorted(images_group.keys()):
            if not str(key).startswith("img_"):
                continue
            item = images_group.get(key)
            if item is None or not hasattr(item, "keys"):
                continue
            if "data" not in item:
                continue
            image_type = cls._as_text(item.attrs.get("image_type"), "").strip().lower()
            image_groups.append((0 if image_type == "sample" else 1, str(key), item))

        if not image_groups:
            return None

        image_groups.sort(key=lambda row: (row[0], row[1]))
        for _rank, _name, image_group in image_groups:
            try:
                image_array = np.asarray(image_group["data"][()])
                jpeg_bytes = cls._encode_array_as_jpeg(image_array)
                if jpeg_bytes:
                    return jpeg_bytes
            except Exception:
                continue
        return None

    @classmethod
    def _encode_array_as_jpeg(cls, image_array: np.ndarray) -> Optional[bytes]:
        """Convert stored session image array to JPG bytes."""
        array = np.asarray(image_array)
        if array.size == 0:
            return None

        # Handle channel-first arrays occasionally used by image stacks.
        if array.ndim == 3 and array.shape[0] in (1, 3, 4) and array.shape[-1] not in (3, 4):
            array = np.moveaxis(array, 0, -1)

        if array.ndim not in (2, 3):
            return None

        arr = np.asarray(array, dtype=np.float32)
        finite = np.isfinite(arr)
        if not finite.any():
            return None
        arr = np.where(finite, arr, 0.0)
        min_v = float(np.min(arr))
        max_v = float(np.max(arr))
        if max_v <= min_v:
            normalized = np.zeros_like(arr, dtype=np.uint8)
        elif min_v >= 0.0 and max_v <= 255.0:
            normalized = np.clip(arr, 0.0, 255.0).astype(np.uint8)
        elif min_v >= 0.0 and max_v <= 1.0:
            normalized = np.clip(arr * 255.0, 0.0, 255.0).astype(np.uint8)
        else:
            normalized = ((arr - min_v) / (max_v - min_v) * 255.0).astype(np.uint8)

        if normalized.ndim == 3:
            if normalized.shape[2] == 1:
                normalized = normalized[:, :, 0]
            elif normalized.shape[2] >= 4:
                normalized = normalized[:, :, :3]

        mode = "L" if normalized.ndim == 2 else "RGB"
        image = Image.fromarray(normalized, mode=mode)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        return buffer.getvalue()

    @classmethod
    def _collect_fallback_timestamps(cls, h5f: h5py.File) -> List[str]:
        values: List[str] = []

        technical_group = h5f.get("/entry/technical")
        if technical_group is not None:
            for event_name in sorted(technical_group.keys()):
                if not str(event_name).startswith("tech_evt_"):
                    continue
                event_group = technical_group[event_name]
                values.append(cls._as_text(event_group.attrs.get("timestamp"), ""))

        measurements_group = h5f.get("/entry/measurements")
        if measurements_group is not None:
            for point_name in sorted(measurements_group.keys()):
                point_group = measurements_group[point_name]
                for meas_name in sorted(point_group.keys()):
                    meas_group = point_group[meas_name]
                    values.append(cls._as_text(meas_group.attrs.get("timestamp_end"), ""))
                    values.append(cls._as_text(meas_group.attrs.get("timestamp_start"), ""))

        return [v for v in values if str(v).strip()]

    @classmethod
    def _collect_technical_events(
        cls,
        *,
        h5f: h5py.File,
        day_token: str,
        default_distance_int: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        technical_group = h5f.get("/entry/technical")
        if technical_group is None:
            return [], int(default_distance_int)

        poni_by_path: Dict[str, str] = {}
        poni_by_alias: Dict[str, str] = {}
        poni_group = technical_group.get("poni")
        if poni_group is not None:
            for poni_name in sorted(poni_group.keys()):
                poni_ds = poni_group[poni_name]
                text = cls._as_text(poni_ds[()], "")
                ds_path = f"{poni_group.name}/{poni_name}"
                poni_by_path[ds_path] = text
                alias = cls._as_text(poni_ds.attrs.get("detector_alias"), "").upper()
                if alias:
                    poni_by_alias[alias] = text

        distance_values: List[float] = []
        events: List[Dict[str, Any]] = []

        for event_name in sorted(technical_group.keys()):
            if not str(event_name).startswith("tech_evt_"):
                continue
            event_group = technical_group[event_name]
            event_type = cls._as_text(event_group.attrs.get("type"), "UNKNOWN").upper()
            event_ts = cls._normalize_timestamp_token(event_group.attrs.get("timestamp"), day_token)
            event_is_primary = bool(event_group.attrs.get("is_primary", False))
            event_distance = cls._extract_distance_from_attrs(event_group.attrs)
            if event_distance is not None:
                distance_values.append(event_distance)

            event_idx_match = re.search(r"(\d+)$", str(event_name))
            if event_idx_match:
                event_index = int(event_idx_match.group(1))
            else:
                event_index = len(events) + 1

            for det_name in sorted(event_group.keys()):
                if not str(det_name).startswith("det_"):
                    continue
                det_group = event_group[det_name]
                if "processed_signal" not in det_group:
                    continue

                alias = cls._as_text(
                    det_group.attrs.get("detector_alias"),
                    str(det_name).replace("det_", "").upper(),
                ).upper()
                detector_id = cls._as_text(det_group.attrs.get("detector_id"), alias)
                integration_ms = cls._to_float(det_group.attrs.get("integration_time_ms"))
                integration_s = None
                if integration_ms is not None:
                    integration_s = integration_ms / 1000.0

                det_distance = cls._extract_distance_from_attrs(det_group.attrs)
                resolved_distance = det_distance if det_distance is not None else event_distance
                if resolved_distance is not None:
                    distance_values.append(resolved_distance)

                raw_blobs: Dict[str, bytes] = {}
                blob_group = det_group.get("blob")
                if blob_group is not None:
                    for blob_name in sorted(blob_group.keys()):
                        if not str(blob_name).startswith("raw_"):
                            continue
                        raw_blobs[str(blob_name)] = cls._read_blob_bytes(blob_group[blob_name])

                poni_text = None
                poni_path = cls._as_text(det_group.attrs.get("poni_path"), "")
                if poni_path:
                    poni_text = poni_by_path.get(poni_path)
                if poni_text is None:
                    poni_text = poni_by_alias.get(alias)

                events.append(
                    {
                        "type": event_type,
                        "event_index": int(event_index),
                        "timestamp_token": event_ts,
                        "alias": alias,
                        "detector_id": detector_id,
                        "integration_s": integration_s,
                        "is_primary": bool(event_is_primary),
                        "processed_signal": np.asarray(det_group["processed_signal"][()]),
                        "raw_blobs": raw_blobs,
                        "poni_text": poni_text,
                    }
                )

        if distance_values:
            rounded = [int(round(v)) for v in distance_values]
            mode_value = Counter(rounded).most_common(1)[0][0]
            canonical_distance_int = max(1, int(mode_value))
        else:
            canonical_distance_int = int(default_distance_int)

        return events, canonical_distance_int

    @classmethod
    def _build_technical_data_files(
        cls,
        *,
        events: List[Dict[str, Any]],
        distance_token: str,
    ) -> Tuple[Dict[str, bytes], Dict[str, Dict[str, Any]]]:
        files: Dict[str, bytes] = {}
        selected: Dict[Tuple[str, str], Dict[str, Any]] = {}

        for event in events:
            event_type = cls._as_text(event.get("type"), "UNKNOWN").upper()
            alias = cls._as_text(event.get("alias"), "DETECTOR").upper()
            alias_token = cls._safe_token(alias, "DETECTOR").upper()
            event_idx_token = f"{int(event.get('event_index') or 1):03d}"
            ts_token = cls._as_text(event.get("timestamp_token"), "")
            integration_token = cls._integration_token(event.get("integration_s"), event_type)
            prefix = cls.TECH_TYPE_FILE_PREFIX.get(event_type, event_type.title() or "Tech")

            base = f"{prefix}_{distance_token}_{event_idx_token}_{ts_token}_{integration_token}_{alias_token}"
            npy_name = f"{base}.npy"
            files[npy_name] = cls._npy_bytes(event.get("processed_signal"))

            raw_blobs = event.get("raw_blobs") or {}
            for blob_name, blob_payload in raw_blobs.items():
                ext = str(blob_name)[4:] if str(blob_name).startswith("raw_") else str(blob_name)
                if ext == "txt":
                    raw_name = f"{base}.txt"
                elif ext == "dsc":
                    raw_name = f"{base}.txt.dsc"
                else:
                    raw_name = f"{base}.{ext or 'bin'}"
                files[raw_name] = bytes(blob_payload)

            poni_name = None
            poni_text = event.get("poni_text")
            if event_type == "AGBH" and poni_text is not None:
                poni_name = f"{base}.poni"
                files[poni_name] = cls._as_text(poni_text, "").encode("utf-8")

            rank = (1 if bool(event.get("is_primary")) else 0, int(event.get("event_index") or 0))
            key = (event_type, alias)
            existing = selected.get(key)
            if existing is None or rank > existing["rank"]:
                selected[key] = {
                    "rank": rank,
                    "npy_name": npy_name,
                    "poni_name": poni_name,
                    "poni_text": cls._as_text(poni_text, "") if poni_text is not None else None,
                }

        return files, selected

    @classmethod
    def _folder_matches_data_files(cls, folder: Path, files: Dict[str, bytes]) -> bool:
        folder = Path(folder)
        if not folder.exists() or not folder.is_dir():
            return False

        try:
            existing_files = {
                p.name
                for p in folder.iterdir()
                if p.is_file() and not p.name.startswith("technical_meta_")
            }
        except Exception:
            return False

        if existing_files != set(files.keys()):
            return False

        for name, payload in files.items():
            candidate = folder / name
            try:
                if not candidate.exists() or candidate.read_bytes() != payload:
                    return False
            except Exception:
                return False

        return True

    @classmethod
    def _next_distance_token(cls, calibration_root: Path, start_distance: int) -> Tuple[str, int]:
        used: set = set()
        pattern = re.compile(r"^(\d+)cm$", re.IGNORECASE)
        for child in calibration_root.iterdir() if calibration_root.exists() else []:
            if not child.is_dir():
                continue
            match = pattern.match(child.name)
            if match:
                used.add(int(match.group(1)))

        candidate = max(1, int(start_distance))
        while candidate in used:
            candidate += 1
        return cls._distance_token(candidate), candidate

    @classmethod
    def _choose_technical_distance_token(
        cls,
        *,
        calibration_root: Path,
        events: List[Dict[str, Any]],
        canonical_distance_int: int,
    ) -> Tuple[str, Dict[str, bytes], Dict[str, Dict[str, Any]], bool]:
        calibration_root.mkdir(parents=True, exist_ok=True)

        existing_tokens: List[Tuple[int, str]] = []
        token_re = re.compile(r"^(\d+)cm$", re.IGNORECASE)
        for child in sorted(calibration_root.iterdir()):
            if not child.is_dir():
                continue
            match = token_re.match(child.name)
            if match:
                existing_tokens.append((int(match.group(1)), child.name))

        # Prefer exact payload match in any already existing distance folder.
        for _distance_value, token in sorted(existing_tokens):
            candidate_files, candidate_selected = cls._build_technical_data_files(
                events=events,
                distance_token=token,
            )
            if cls._folder_matches_data_files(calibration_root / token, candidate_files):
                return token, candidate_files, candidate_selected, True

        canonical_token = cls._distance_token(canonical_distance_int)
        canonical_files, canonical_selected = cls._build_technical_data_files(
            events=events,
            distance_token=canonical_token,
        )
        canonical_folder = calibration_root / canonical_token

        if not canonical_folder.exists():
            return canonical_token, canonical_files, canonical_selected, False

        if cls._folder_matches_data_files(canonical_folder, canonical_files):
            return canonical_token, canonical_files, canonical_selected, True

        next_token, _next_int = cls._next_distance_token(calibration_root, canonical_distance_int + 1)
        next_files, next_selected = cls._build_technical_data_files(
            events=events,
            distance_token=next_token,
        )
        return next_token, next_files, next_selected, False

    @classmethod
    def _export_technical_measurements(
        cls,
        *,
        h5f: h5py.File,
        day_dir: Path,
        day_token: str,
        default_distance_int: int,
        calibration_group_hash: str,
    ) -> Tuple[int, str, Dict[Tuple[str, str], str], Dict[str, Dict[str, Any]], Optional[Path]]:
        calibration_root = day_dir / "calibration background"
        events, canonical_distance_int = cls._collect_technical_events(
            h5f=h5f,
            day_token=day_token,
            default_distance_int=default_distance_int,
        )

        distance_token, data_files, selected, reused_existing = cls._choose_technical_distance_token(
            calibration_root=calibration_root,
            events=events,
            canonical_distance_int=canonical_distance_int,
        )
        tech_dir = calibration_root / distance_token
        tech_dir.mkdir(parents=True, exist_ok=True)

        if data_files:
            for name, payload in sorted(data_files.items()):
                cls._write_bytes_if_changed(tech_dir / name, payload)

        # Build technical meta payload in old format style.
        by_type_alias: Dict[str, Dict[str, str]] = {}
        for (event_type, alias), info in sorted(selected.items()):
            by_type_alias.setdefault(event_type, {})[alias] = info["npy_name"]

        detector_poni: Dict[str, Dict[str, Any]] = {}
        poni_lab: Dict[str, str] = {}
        poni_lab_path: Dict[str, str] = {}
        poni_lab_values: Dict[str, str] = {}

        for (event_type, alias), info in sorted(selected.items()):
            if event_type != "AGBH":
                continue
            poni_name = info.get("poni_name")
            poni_text = info.get("poni_text")
            if not poni_name:
                continue
            poni_path = str((tech_dir / poni_name).resolve())
            detector_poni[alias] = {
                "poni_filename": poni_name,
                "poni_path": poni_path,
                "poni_value": cls._as_text(poni_text, ""),
            }
            poni_lab[alias] = poni_name
            poni_lab_path[alias] = poni_path
            poni_lab_values[alias] = cls._as_text(poni_text, "")

        meta_payload: Dict[str, Any] = {}
        preferred_order = ["DARK", "EMPTY", "AGBH", "BACKGROUND"]
        for typ in preferred_order:
            if typ in by_type_alias:
                meta_payload[typ] = by_type_alias[typ]
        for typ in sorted(by_type_alias.keys()):
            if typ not in meta_payload:
                meta_payload[typ] = by_type_alias[typ]
        if poni_lab:
            meta_payload["PONI_LAB"] = poni_lab
        if poni_lab_path:
            meta_payload["PONI_LAB_PATH"] = poni_lab_path
        if poni_lab_values:
            meta_payload["PONI_LAB_VALUES"] = poni_lab_values
        if calibration_group_hash:
            meta_payload["CALIBRATION_GROUP_HASH"] = calibration_group_hash

        meta_name = f"technical_meta_{day_token}_{distance_token}.json"
        meta_path = tech_dir / meta_name
        meta_bytes = json.dumps(meta_payload, indent=2).encode("utf-8")
        cls._write_bytes_if_changed(meta_path, meta_bytes)

        technical_aux_map: Dict[Tuple[str, str], str] = {}
        for event_type, alias_map in by_type_alias.items():
            for alias, npy_name in alias_map.items():
                technical_aux_map[(event_type, alias)] = str((tech_dir / npy_name).resolve())

        technical_count = len(data_files) + 1
        # Keep a deterministic count even when folder was reused (for reporting/tests).
        if reused_existing:
            technical_count = len(data_files) + 1

        return technical_count, distance_token, technical_aux_map, detector_poni, meta_path

    @classmethod
    def _build_existing_measurement_lookup(
        cls,
        state_payload: Dict[str, Any],
    ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]]]:
        by_txt: Dict[str, Dict[str, Any]] = {}
        by_npy: Dict[str, Dict[str, Any]] = {}
        by_uid_alias: Dict[Tuple[str, str], Dict[str, Any]] = {}
        by_uid_detector: Dict[Tuple[str, str], Dict[str, Any]] = {}

        raw = state_payload.get("measurements_meta")
        if not isinstance(raw, dict):
            return by_txt, by_npy, by_uid_alias, by_uid_detector

        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            name = Path(cls._as_text(key, "")).name
            if name.endswith(".txt"):
                by_txt[name] = value
            if name.endswith(".npy"):
                by_npy[name] = value
            uid = cls._as_text(value.get("unique_id"), "")
            alias = cls._as_text(value.get("detector_alias"), "").upper()
            detector_id = cls._as_text(value.get("detector_id"), "")
            if uid and alias and (uid, alias) not in by_uid_alias:
                by_uid_alias[(uid, alias)] = value
            if uid and detector_id and (uid, detector_id) not in by_uid_detector:
                by_uid_detector[(uid, detector_id)] = value

        return by_txt, by_npy, by_uid_alias, by_uid_detector

    @classmethod
    def _export_measurement_raw(
        cls,
        *,
        h5f: h5py.File,
        sample_dir: Path,
        sample_base_with_distance: str,
        day_token: str,
        calibration_group_hash: str,
        point_uid_by_session_index: Dict[int, str],
        existing_lookup: Tuple[
            Dict[str, Dict[str, Any]],
            Dict[str, Dict[str, Any]],
            Dict[Tuple[str, str], Dict[str, Any]],
            Dict[Tuple[str, str], Dict[str, Any]],
        ],
    ) -> Tuple[int, Dict[str, Dict[str, Any]]]:
        measurements_group = h5f.get("/entry/measurements")
        if measurements_group is None:
            return 0, {}

        by_txt, by_npy, by_uid_alias, by_uid_detector = existing_lookup

        exported = 0
        measurements_meta: Dict[str, Dict[str, Any]] = {}

        for point_name in sorted(measurements_group.keys()):
            point_group = measurements_group[point_name]
            x_mm, y_mm = cls._extract_point_coordinates(h5f, point_name)
            try:
                session_point_idx = int(str(point_name).split("_")[-1])
            except Exception:
                session_point_idx = len(point_uid_by_session_index) + 1
            point_uid = point_uid_by_session_index.get(session_point_idx, str(point_name))

            for meas_name in sorted(point_group.keys()):
                measurement_group = point_group[meas_name]
                ts_token = cls._normalize_timestamp_token(
                    measurement_group.attrs.get("timestamp_end")
                    or measurement_group.attrs.get("timestamp_start"),
                    day_token,
                )

                for det_name in sorted(measurement_group.keys()):
                    if not str(det_name).startswith("det_"):
                        continue
                    det_group = measurement_group[det_name]
                    if "processed_signal" not in det_group:
                        continue

                    detector_alias = cls._as_text(
                        det_group.attrs.get("detector_alias"),
                        str(det_name).replace("det_", "").upper(),
                    ).upper()
                    detector_id = cls._as_text(det_group.attrs.get("detector_id"), detector_alias)
                    alias_token = cls._safe_token(detector_alias, "DETECTOR").upper()

                    base = (
                        f"{sample_base_with_distance}_"
                        f"{cls._format_coord_token(x_mm)}_"
                        f"{cls._format_coord_token(y_mm)}_"
                        f"{ts_token}_"
                        f"{alias_token}"
                    )
                    npy_name = f"{base}.npy"
                    txt_name = f"{base}.txt"

                    npy_payload = cls._npy_bytes(det_group["processed_signal"][()])
                    cls._write_bytes_if_changed(sample_dir / npy_name, npy_payload)
                    exported += 1

                    blob_group = det_group.get("blob")
                    has_txt_blob = False
                    if blob_group is not None:
                        for blob_name in sorted(blob_group.keys()):
                            if not str(blob_name).startswith("raw_"):
                                continue
                            ext = str(blob_name)[4:] or "bin"
                            if ext == "txt":
                                raw_name = txt_name
                                has_txt_blob = True
                            elif ext == "dsc":
                                raw_name = f"{base}.txt.dsc"
                            else:
                                raw_name = f"{base}.{ext}"
                            cls._write_bytes_if_changed(
                                sample_dir / raw_name,
                                cls._read_blob_bytes(blob_group[blob_name]),
                            )
                            exported += 1

                    if not has_txt_blob:
                        # Keep old-style keying by .txt when session lacks raw txt blob.
                        try:
                            txt_buffer = io.StringIO()
                            np.savetxt(txt_buffer, np.asarray(det_group["processed_signal"][()]))
                            txt_payload = txt_buffer.getvalue().encode("utf-8")
                            cls._write_bytes_if_changed(sample_dir / txt_name, txt_payload)
                            exported += 1
                            has_txt_blob = True
                        except Exception:
                            has_txt_blob = False

                    integration_s = None
                    integration_ms = cls._to_float(det_group.attrs.get("integration_time_ms"))
                    if integration_ms is not None:
                        integration_s = integration_ms / 1000.0

                    existing_entry = (
                        by_txt.get(txt_name)
                        or by_npy.get(npy_name)
                        or by_uid_alias.get((point_uid, detector_alias))
                        or by_uid_detector.get((point_uid, detector_id))
                    )
                    if not isinstance(existing_entry, dict):
                        existing_entry = {}

                    merged = dict(existing_entry)
                    merged["x"] = x_mm
                    merged["y"] = y_mm
                    merged["unique_id"] = point_uid
                    merged["base_file"] = sample_base_with_distance
                    merged["integration_time"] = integration_s
                    merged["detector_alias"] = detector_alias
                    merged["detector_id"] = detector_id
                    if calibration_group_hash:
                        merged["CALIBRATION_GROUP_HASH"] = calibration_group_hash

                    for attr_key, state_key in (
                        ("detector_type", "detector_type"),
                        ("detector_size", "detector_size"),
                        ("pixel_size_um", "pixel_size_um"),
                        ("faulty_pixels", "faulty_pixels"),
                    ):
                        if state_key in merged and merged.get(state_key) is not None:
                            continue
                        attr_value = det_group.attrs.get(attr_key)
                        if attr_value is not None:
                            if isinstance(attr_value, np.ndarray):
                                merged[state_key] = attr_value.tolist()
                            else:
                                merged[state_key] = attr_value

                    key_name = txt_name if has_txt_blob else npy_name
                    measurements_meta[key_name] = merged

        return exported, measurements_meta

    @classmethod
    def export_from_session_container(
        cls,
        session_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
        archive_folder: Optional[Path] = None,
        target_root: Optional[Path] = None,
    ) -> OldFormatExportSummary:
        """Export legacy raw/state/technical folders from a session container."""
        source = Path(session_path)
        if not source.exists():
            raise FileNotFoundError(f"Session container not found: {source}")

        cfg = config or {}

        with h5py.File(source, "r") as h5f:
            sample_id = cls._as_text(
                h5f.attrs.get("specimenId", h5f.attrs.get("sample_id")),
                "UNKNOWN",
            )
            study_name = cls._as_text(h5f.attrs.get("study_name"), "UNSPECIFIED")
            session_id = cls._as_text(h5f.attrs.get("session_id"), source.stem)
            operator_id = cls._as_text(
                h5f.attrs.get("operator_id") or h5f.attrs.get("locked_by"),
                "unknown",
            )
            acquisition_date = cls._as_text(h5f.attrs.get("acquisition_date"), "")

            state_payload = cls._load_state_payload(h5f)
            if not isinstance(state_payload, dict):
                state_payload = {}

            fallback_timestamps = cls._collect_fallback_timestamps(h5f)
            day_token = cls._resolve_day_token(
                acquisition_date=acquisition_date,
                fallback_timestamps=fallback_timestamps,
            )

            root = (
                Path(target_root)
                if target_root is not None
                else cls.resolve_old_format_root(
                    config=config,
                    archive_folder=archive_folder,
                )
            )
            root.mkdir(parents=True, exist_ok=True)
            day_dir = root / day_token
            day_dir.mkdir(parents=True, exist_ok=True)

            group_hash = cls._as_text(state_payload.get("CALIBRATION_GROUP_HASH"), "").strip()
            if not group_hash:
                group_hash = uuid.uuid4().hex[:16]
            state_payload["CALIBRATION_GROUP_HASH"] = group_hash

            default_distance_int = cls._distance_int(
                cls._to_float(cfg.get("default_technical_distance_cm")),
                default=17,
            )
            technical_count, distance_token, technical_aux_map, detector_poni_map, _tech_meta_path = cls._export_technical_measurements(
                h5f=h5f,
                day_dir=day_dir,
                day_token=day_token,
                default_distance_int=default_distance_int,
                calibration_group_hash=group_hash,
            )

            sample_folder_name = cls._derive_sample_folder_name(
                state_payload=state_payload,
                sample_id=sample_id,
                study_name=study_name,
                session_id=session_id,
            )
            sample_dir = day_dir / cls._safe_token(sample_folder_name, "sample")
            sample_dir.mkdir(parents=True, exist_ok=True)

            sample_base_with_distance = cls._derive_sample_base_with_distance(
                state_payload=state_payload,
                sample_id=sample_id,
                distance_token=distance_token,
            )

            measurement_points = state_payload.get("measurement_points")
            if not isinstance(measurement_points, list) or not measurement_points:
                measurement_points = cls._build_measurement_points(h5f)
                state_payload["measurement_points"] = measurement_points

            point_uid_by_session_index: Dict[int, str] = {}
            for idx, point in enumerate(measurement_points, start=1):
                if isinstance(point, dict):
                    uid = cls._as_text(point.get("unique_id"), "").strip()
                    if uid:
                        point_uid_by_session_index[idx] = uid

            existing_lookup = cls._build_existing_measurement_lookup(state_payload)
            raw_count, measurements_meta = cls._export_measurement_raw(
                h5f=h5f,
                sample_dir=sample_dir,
                sample_base_with_distance=sample_base_with_distance,
                day_token=day_token,
                calibration_group_hash=group_hash,
                point_uid_by_session_index=point_uid_by_session_index,
                existing_lookup=existing_lookup,
            )

            state_payload["measurements_meta"] = measurements_meta

            if technical_aux_map:
                preferred_types = ["DARK", "EMPTY", "AGBH", "BACKGROUND"]
                technical_aux_rows: List[Dict[str, Any]] = []
                for technical_type in preferred_types:
                    for (row_type, alias), file_path in sorted(technical_aux_map.items()):
                        if row_type != technical_type:
                            continue
                        technical_aux_rows.append(
                            {
                                "file_path": file_path,
                                "type": row_type,
                                "alias": alias,
                            }
                        )
                for (row_type, alias), file_path in sorted(technical_aux_map.items()):
                    if row_type in preferred_types:
                        continue
                    technical_aux_rows.append(
                        {
                            "file_path": file_path,
                            "type": row_type,
                            "alias": alias,
                        }
                    )
                state_payload["technical_aux"] = technical_aux_rows

            if detector_poni_map:
                state_payload["detector_poni"] = detector_poni_map

            state_payload.setdefault("sample_id", sample_id)
            state_payload.setdefault("specimenId", sample_id)
            state_payload.setdefault("study_name", study_name)
            state_payload.setdefault("session_id", session_id)
            state_payload.setdefault("operator_id", operator_id)

            exported_image = cls._export_session_image(
                state_payload=state_payload,
                h5f=h5f,
                export_dir=sample_dir,
                sample_id=sample_id,
                session_id=session_id,
            )
            if exported_image is not None:
                state_payload["image"] = str(exported_image.resolve())
                try:
                    state_payload["image_base64"] = base64.b64encode(
                        exported_image.read_bytes()
                    ).decode("ascii")
                except Exception:
                    pass

            state_filename = f"{sample_base_with_distance}_state.json"
            state_path = sample_dir / state_filename
            state_path.write_text(
                json.dumps(state_payload, indent=2),
                encoding="utf-8",
            )

        return OldFormatExportSummary(
            export_dir=day_dir,
            state_path=state_path,
            raw_file_count=raw_count,
            technical_file_count=technical_count,
        )
