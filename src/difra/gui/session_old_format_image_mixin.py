"""Export session containers into legacy folder layout used by older DIFRA flows."""

import base64
from dataclasses import dataclass
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

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




class SessionOldFormatImageMixin:
    """Old-format export behavior split from SessionOldFormatExporter."""

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
        schema = cls._schema_for_h5(h5f)
        images_group = h5f.get(getattr(schema, "GROUP_IMAGES", "/entry/images"))
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
            image_type = cls._as_text(
                item.attrs.get(getattr(schema, "ATTR_IMAGE_TYPE", "image_type")),
                "",
            ).strip().lower()
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
        schema = cls._schema_for_h5(h5f)

        technical_group = h5f.get(getattr(schema, "GROUP_TECHNICAL", "/entry/technical"))
        if technical_group is not None:
            for event_name in sorted(technical_group.keys()):
                if not str(event_name).startswith("tech_evt_"):
                    continue
                event_group = technical_group[event_name]
                values.append(
                    cls._as_text(
                        event_group.attrs.get(getattr(schema, "ATTR_TIMESTAMP", "timestamp")),
                        "",
                    )
                )

        measurements_group = h5f.get(getattr(schema, "GROUP_MEASUREMENTS", "/entry/measurements"))
        if measurements_group is not None:
            for point_name in sorted(measurements_group.keys()):
                point_group = measurements_group[point_name]
                for meas_name in sorted(point_group.keys()):
                    meas_group = point_group[meas_name]
                    values.append(
                        cls._as_text(
                            meas_group.attrs.get(getattr(schema, "ATTR_TIMESTAMP_END", "timestamp_end")),
                            "",
                        )
                    )
                    values.append(
                        cls._as_text(
                            meas_group.attrs.get(getattr(schema, "ATTR_TIMESTAMP_START", "timestamp_start")),
                            "",
                        )
                    )

        return [v for v in values if str(v).strip()]

