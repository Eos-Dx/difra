"""Export session containers into legacy folder layout used by older DIFRA flows."""

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import h5py
import numpy as np


@dataclass
class OldFormatExportSummary:
    """Summary for one legacy export operation."""

    export_dir: Path
    state_path: Path
    raw_file_count: int
    technical_file_count: int




class SessionOldFormatAttenuationMixin:
    """Old-format export behavior split from SessionOldFormatExporter."""

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
    def _resolve_attenuation_role(
        cls,
        *,
        analysis_type: str,
        analysis_role: str,
        has_i0_already: bool,
    ) -> Optional[str]:
        att_type = analysis_type.strip().lower()
        att_role = analysis_role.strip().lower()

        if att_role in {"i0", "without", "without_sample"} or att_type in {
            "attenuation_i0",
            "attenuation_without",
            "attenuation_without_sample",
        }:
            return "without_sample"
        if att_role in {"i", "with", "with_sample"} or att_type in {
            "attenuation_i",
            "attenuation_with",
            "attenuation_with_sample",
        }:
            return "with_sample"

        if att_type and not att_type.startswith("attenuation"):
            return None
        return "with_sample" if has_i0_already else "without_sample"

    @classmethod
    def _extract_point_indices_from_refs(
        cls,
        *,
        h5f: h5py.File,
        refs: Any,
    ) -> List[int]:
        indices: List[int] = []
        for ref in np.asarray(refs).flatten().tolist():
            try:
                point_obj = h5f[ref]
            except Exception:
                continue
            match = re.search(r"pt_(\d+)$", getattr(point_obj, "name", ""))
            if not match:
                continue
            indices.append(int(match.group(1)))
        return indices

    @classmethod
    def _copy_attenuation_source_files(
        cls,
        *,
        sample_dir: Path,
        state_payload: Dict[str, Any],
    ) -> Tuple[int, Dict[str, Dict[str, Dict[str, str]]]]:
        copied_count = 0
        copied: Dict[str, Dict[str, Dict[str, str]]] = {}
        source_cache: Dict[str, str] = {}
        raw = state_payload.get("attenuation_files")
        if not isinstance(raw, dict):
            return copied_count, copied

        for point_uid, point_roles in raw.items():
            if not isinstance(point_roles, dict):
                continue
            point_uid_text = cls._as_text(point_uid, "").strip()
            if not point_uid_text:
                continue

            for role_key in ("without_sample", "with_sample"):
                alias_map = point_roles.get(role_key)
                if not isinstance(alias_map, dict):
                    continue

                for alias, source_value in sorted(alias_map.items()):
                    alias_text = cls._as_text(alias, "").strip()
                    source_text = cls._as_text(source_value, "").strip()
                    if not alias_text or not source_text:
                        continue

                    source_path = Path(source_text)
                    cache_key = source_text
                    try:
                        cache_key = str(source_path.resolve())
                    except Exception:
                        pass

                    exported_path = source_cache.get(cache_key)
                    if exported_path is None:
                        try:
                            if not source_path.exists() or not source_path.is_file():
                                continue
                            payload = source_path.read_bytes()
                            target_name = source_path.name or f"{cls._safe_token(alias_text, 'detector')}.npy"
                            target_path = sample_dir / target_name
                            if target_path.exists() and target_path.read_bytes() != payload:
                                target_path = cls._unique_path(sample_dir, target_name)
                            target_path.write_bytes(payload)
                            exported_path = str(target_path.resolve())
                            source_cache[cache_key] = exported_path
                            copied_count += 1
                        except Exception:
                            continue

                    copied.setdefault(point_uid_text, {}).setdefault(role_key, {})[alias_text] = exported_path

        return copied_count, copied

    @classmethod
    def _export_attenuation_from_container(
        cls,
        *,
        h5f: h5py.File,
        sample_dir: Path,
        sample_base_with_distance: str,
        day_token: str,
        point_uid_by_session_index: Dict[int, str],
    ) -> Tuple[int, Dict[str, Dict[str, Dict[str, str]]]]:
        schema = cls._schema_for_h5(h5f)
        ana_group = h5f.get(
            getattr(schema, "GROUP_ANALYTICAL_MEASUREMENTS", "/analytical_measurements")
        )
        if ana_group is None:
            return 0, {}

        exported = 0
        attenuation_files: Dict[str, Dict[str, Dict[str, str]]] = {}
        path_cache: Dict[Tuple[str, str], str] = {}
        seen_i0 = False

        type_attr_name = getattr(schema, "ATTR_ANALYSIS_TYPE", "analysis_type")
        role_attr_name = getattr(schema, "ATTR_ANALYSIS_ROLE", "analysis_role")
        point_ids_attr_name = getattr(schema, "ATTR_POINT_IDS", "point_ids")
        point_refs_attr_name = getattr(schema, "ATTR_POINT_REFS", "point_refs")
        timestamp_end_attr_name = getattr(schema, "ATTR_TIMESTAMP_END", "timestamp_end")
        timestamp_start_attr_name = getattr(schema, "ATTR_TIMESTAMP_START", "timestamp_start")
        dataset_processed_signal = getattr(schema, "DATASET_PROCESSED_SIGNAL", "processed_signal")

        for ana_name in sorted(ana_group.keys()):
            ana_item = ana_group[ana_name]
            analysis_type = cls._as_text(ana_item.attrs.get(type_attr_name), "")
            analysis_role = cls._as_text(ana_item.attrs.get(role_attr_name), "")
            role_key = cls._resolve_attenuation_role(
                analysis_type=analysis_type,
                analysis_role=analysis_role,
                has_i0_already=seen_i0,
            )
            if role_key is None:
                continue
            if role_key == "without_sample":
                seen_i0 = True

            point_indices: List[int] = []
            point_ids_raw = ana_item.attrs.get(point_ids_attr_name)
            if point_ids_raw is not None:
                for point_id in np.asarray(point_ids_raw).flatten().tolist():
                    point_idx = cls._safe_int(point_id)
                    if point_idx is not None:
                        point_indices.append(point_idx)
            if not point_indices:
                point_refs = ana_item.attrs.get(point_refs_attr_name)
                if point_refs is not None:
                    point_indices = cls._extract_point_indices_from_refs(h5f=h5f, refs=point_refs)
            if role_key == "without_sample" and not point_indices:
                point_indices = sorted(point_uid_by_session_index.keys())

            ts_token = cls._normalize_timestamp_token(
                ana_item.attrs.get(timestamp_end_attr_name)
                or ana_item.attrs.get(timestamp_start_attr_name),
                day_token,
            )

            for det_name in sorted(ana_item.keys()):
                if not str(det_name).startswith("det_"):
                    continue
                det_group = ana_item[det_name]
                if dataset_processed_signal not in det_group:
                    continue

                detector_alias = cls._as_text(
                    det_group.attrs.get(getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")),
                    str(det_name).replace("det_", "").upper(),
                ).upper()
                alias_token = cls._safe_token(detector_alias, "DETECTOR").upper()
                cache_key = (str(ana_name), detector_alias)
                exported_path = path_cache.get(cache_key)

                if exported_path is None:
                    if role_key == "with_sample":
                        x_mm = y_mm = None
                        if point_indices:
                            x_mm, y_mm = cls._extract_point_coordinates(
                                h5f, f"pt_{int(point_indices[0]):03d}"
                            )
                        if x_mm is None or y_mm is None:
                            point_position = det_group.attrs.get("point_position_mm")
                            if point_position is None:
                                point_position = ana_item.attrs.get("point_position_mm")
                            x_mm, y_mm = cls._extract_xy_pair(
                                point_position
                            )
                        base = (
                            f"{sample_base_with_distance}_"
                            f"{cls._format_coord_token(x_mm)}_"
                            f"{cls._format_coord_token(y_mm)}_"
                            f"{ts_token}__{alias_token}_ATTENUATION"
                        )
                    else:
                        base = f"{sample_base_with_distance}_{ts_token}__{alias_token}_ATTENUATION0"

                    blob_group = det_group.get("blob")
                    raw_blobs: Dict[str, bytes] = {}
                    if blob_group is not None:
                        for blob_name in sorted(blob_group.keys()):
                            if not str(blob_name).startswith("raw_"):
                                continue
                            raw_blobs[str(blob_name)] = cls._read_blob_bytes(blob_group[blob_name])

                    matrix_name, matrix_payload, _matrix_ext, dsc_payload = cls._select_matrix_payload(
                        base=base,
                        processed_signal=det_group[dataset_processed_signal][()],
                        raw_blobs=raw_blobs,
                    )
                    matrix_path = sample_dir / matrix_name
                    cls._write_bytes_if_changed(matrix_path, matrix_payload)
                    exported_path = str(matrix_path.resolve())
                    path_cache[cache_key] = exported_path
                    exported += 1
                    if dsc_payload:
                        cls._write_bytes_if_changed(
                            sample_dir / cls._measurement_dsc_sidecar_name(matrix_name),
                            dsc_payload,
                        )
                        exported += 1

                for point_index in point_indices:
                    point_uid = point_uid_by_session_index.get(int(point_index))
                    if not point_uid:
                        continue
                    attenuation_files.setdefault(point_uid, {}).setdefault(role_key, {})[
                        detector_alias
                    ] = exported_path

        return exported, attenuation_files

    @classmethod
    def _export_attenuation_files(
        cls,
        *,
        h5f: h5py.File,
        sample_dir: Path,
        sample_base_with_distance: str,
        day_token: str,
        state_payload: Dict[str, Any],
        point_uid_by_session_index: Dict[int, str],
    ) -> Tuple[int, Dict[str, Dict[str, Dict[str, str]]]]:
        copied_count, copied_state = cls._copy_attenuation_source_files(
            sample_dir=sample_dir,
            state_payload=state_payload,
        )
        container_count, container_files = cls._export_attenuation_from_container(
            h5f=h5f,
            sample_dir=sample_dir,
            sample_base_with_distance=sample_base_with_distance,
            day_token=day_token,
            point_uid_by_session_index=point_uid_by_session_index,
        )

        merged: Dict[str, Dict[str, Dict[str, str]]] = {}
        for source in (copied_state, container_files):
            for point_uid, point_roles in source.items():
                for role_key, alias_map in point_roles.items():
                    target = merged.setdefault(point_uid, {}).setdefault(role_key, {})
                    for alias, file_path in alias_map.items():
                        target.setdefault(alias, file_path)

        return copied_count + container_count, merged

