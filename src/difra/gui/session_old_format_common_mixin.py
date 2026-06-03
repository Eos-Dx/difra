"""Export session containers into legacy folder layout used by older DIFRA flows."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import h5py
import numpy as np
from container import loader
from container.registry import load_version_module


@dataclass
class OldFormatExportSummary:
    """Summary for one legacy export operation."""

    export_dir: Path
    state_path: Path
    raw_file_count: int
    technical_file_count: int




class SessionOldFormatCommonMixin:
    """Old-format export behavior split from SessionOldFormatExporter."""

    @staticmethod
    def _schema_for_h5(h5f: h5py.File):
        file_name = getattr(h5f, "filename", None)
        if file_name:
            try:
                resolved_version = loader.detect_version(Path(file_name))
                return load_version_module(resolved_version.replace(".", "_")).schema
            except Exception:
                pass
        return None

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
    def _safe_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            try:
                return int(str(value).strip())
            except Exception:
                return None

    @staticmethod
    def _npy_bytes(array: Any) -> bytes:
        buffer = io.BytesIO()
        np.save(buffer, np.asarray(array))
        return buffer.getvalue()

    @classmethod
    def _technical_container_id_from_h5(cls, h5f: h5py.File) -> str:
        for snapshot_path in ("/entry/calibration_snapshot", "/entry/technical"):
            snapshot = h5f.get(snapshot_path)
            if snapshot is None:
                continue
            for attr_name in (
                "source_container_id",
                "technical_container_id",
                "container_id",
            ):
                value = cls._as_text(snapshot.attrs.get(attr_name), "").strip()
                if value:
                    return value
        for attr_name in ("technical_container_id", "source_container_id"):
            value = cls._as_text(h5f.attrs.get(attr_name), "").strip()
            if value:
                return value
        return ""

    @classmethod
    def _default_calibration_group_hash(
        cls,
        h5f: h5py.File,
        *,
        fallback: str,
    ) -> str:
        technical_id = cls._technical_container_id_from_h5(h5f)
        if technical_id:
            return technical_id
        token = str(fallback or "").strip()
        return hashlib.md5(token.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def _resolve_calibration_group_hash(
        cls,
        h5f: h5py.File,
        *,
        state_payload: Dict[str, Any],
        config: Optional[Dict[str, Any]] = None,
        fallback: str,
    ) -> str:
        cfg = config or {}
        for value in (cfg.get("matador_calibration_group_hash_override"),):
            text = cls._as_text(value, "").strip()
            if text:
                return text
        technical_id = cls._technical_container_id_from_h5(h5f)
        if technical_id:
            return cls._default_calibration_group_hash(h5f, fallback=fallback)
        state_hash = cls._as_text(state_payload.get("CALIBRATION_GROUP_HASH"), "").strip()
        if state_hash:
            return state_hash
        return cls._default_calibration_group_hash(h5f, fallback=fallback)

    @staticmethod
    def _raw_blob_extension(blob_name: str) -> str:
        text = str(blob_name or "")
        ext = text[4:] if text.startswith("raw_") else text
        return ext.lower().lstrip(".")

    @classmethod
    def _select_matrix_payload(
        cls,
        *,
        base: str,
        processed_signal: Any,
        raw_blobs: Dict[str, bytes],
        require_raw_txt: bool = False,
        require_raw_dsc: bool = False,
    ) -> Tuple[str, bytes, Optional[str], bytes]:
        by_ext: Dict[str, bytes] = {}
        dsc_payload = b""
        for blob_name, payload in sorted((raw_blobs or {}).items()):
            ext = cls._raw_blob_extension(blob_name)
            if ext == "dsc":
                dsc_payload = bytes(payload)
                continue
            by_ext[ext] = bytes(payload)

        if require_raw_txt and "txt" not in by_ext:
            raise ValueError(f"Missing raw_txt blob for {base}")
        if require_raw_dsc and not dsc_payload:
            raise ValueError(f"Missing raw_dsc blob for {base}")

        for ext in cls.MATRIX_BLOB_PRIORITY:
            if ext not in by_ext:
                continue
            output_ext = "tiff" if ext == "tiff" else ext
            return f"{base}.{output_ext}", by_ext[ext], output_ext, dsc_payload

        return f"{base}.npy", cls._npy_bytes(processed_signal), "npy", dsc_payload

    @staticmethod
    def _measurement_dsc_sidecar_name(matrix_name: str) -> str:
        matrix_path = Path(str(matrix_name or "measurement"))
        base_name = matrix_path.stem if matrix_path.suffix else matrix_path.name
        return f"{base_name}.txt.dsc"

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

    @classmethod
    def _measurement_file_base(
        cls,
        *,
        bundle_base: str,
        point_name: str,
        point_unique_id: str,
        measurement_name: str,
        x_mm: Optional[float],
        y_mm: Optional[float],
        timestamp_token: str,
        detector_alias: str,
    ) -> str:
        point_token = cls._safe_token(
            point_unique_id or point_name,
            cls._safe_token(point_name, "point"),
        )
        measurement_token = cls._safe_token(measurement_name, "measurement")
        alias_token = cls._safe_token(detector_alias, "DETECTOR").upper()
        return (
            f"{bundle_base}_"
            f"{point_token}_"
            f"{measurement_token}_"
            f"{cls._format_coord_token(x_mm)}_"
            f"{cls._format_coord_token(y_mm)}_"
            f"{timestamp_token}_"
            f"{alias_token}"
        )

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
        return f"{value:.6f}s"

    @staticmethod
    def _frames_token(n_frames: Any) -> str:
        try:
            value = int(n_frames)
        except Exception:
            value = 1
        return f"{max(1, value)}frames"

    @classmethod
    def _technical_type_order_token(cls, event_type: str, fallback_index: Any) -> str:
        event_type_key = str(event_type or "").upper()
        value = cls.TECH_TYPE_FILE_ORDER.get(event_type_key)
        if value is None:
            try:
                value = int(fallback_index)
            except Exception:
                value = 1
        return f"{max(1, int(value)):03d}"

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
    def _extract_xy_pair(cls, value: Any) -> Tuple[Optional[float], Optional[float]]:
        if value is None:
            return None, None
        try:
            coords = np.asarray(value).flatten().tolist()
        except Exception:
            return None, None
        if len(coords) < 2:
            return None, None
        try:
            return float(coords[0]), float(coords[1])
        except Exception:
            return None, None

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
        schema = cls._schema_for_h5(h5f)
        points_group = h5f.get(getattr(schema, "GROUP_POINTS", "/entry/points"))
        if points_group is None or point_name not in points_group:
            return None, None
        point_group = points_group[point_name]
        coords = point_group.attrs.get(
            getattr(schema, "ATTR_PHYSICAL_COORDINATES_MM", "physical_coordinates_mm")
        )
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
        schema = cls._schema_for_h5(h5f)
        points_group = h5f.get(getattr(schema, "GROUP_POINTS", "/entry/points"))
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

