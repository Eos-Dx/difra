"""Technical H5 loading/table population responsibilities."""

from pathlib import Path
import hashlib
import json

import numpy as np

from . import h5_management_mixin as _module
from .poni_center_validation import resolve_poni_rule_alias, validate_poni_metadata
from .poni_distance_validation import parse_poni_distance_cm, validate_poni_distances
from . import technical_startup_reconcile
from difra.gui.technical.analysis_compat import detect_faulty_pixel_masks

os = _module.os
shutil = _module.shutil
time = _module.time
logger = _module.logger
QInputDialog = _module.QInputDialog
QMessageBox = _module.QMessageBox
QFileDialog = _module.QFileDialog
get_container_manager = _module.get_container_manager
get_schema = _module.get_schema
get_technical_validator = _module.get_technical_validator

from difra.gui.main_window_ext.technical import h5_management_loading_actions



class H5LoadingExtractMixin:
    def _extract_rows_from_runtime_group(self, h5f, schema, h5_path: str):
        candidates = [
            f"{schema.GROUP_RUNTIME}/technical_aux_rows",
            "/runtime/technical_aux_rows",
            "/entry/runtime/technical_aux_rows",
        ]
        runtime_group = None
        for candidate in candidates:
            if candidate in h5f:
                runtime_group = h5f[candidate]
                break

        if runtime_group is None:
            return []

        rows = []
        for row_name in sorted(runtime_group.keys()):
            row_group = runtime_group[row_name]
            alias = row_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
            if isinstance(alias, bytes):
                alias = alias.decode("utf-8", errors="replace")
            technical_type = row_group.attrs.get("type", "")
            if isinstance(technical_type, bytes):
                technical_type = technical_type.decode("utf-8", errors="replace")

            source_file = row_group.attrs.get("source_file", "")
            if isinstance(source_file, bytes):
                source_file = source_file.decode("utf-8", errors="replace")
            source_ref = row_group.attrs.get("source_ref", "")
            if isinstance(source_ref, bytes):
                source_ref = source_ref.decode("utf-8", errors="replace")

            dataset_path = ""
            source_container = ""
            source_kind = "file"
            parsed_container, parsed_dataset = self._parse_h5ref(str(source_ref))
            if parsed_container and parsed_dataset:
                source_kind = "container"
                source_container = str(h5_path) if parsed_dataset in h5f else str(parsed_container)
                dataset_path = str(parsed_dataset)
            elif schema.DATASET_PROCESSED_SIGNAL in row_group:
                source_kind = "container"
                source_container = str(h5_path)
                dataset_path = f"{row_group.name}/{schema.DATASET_PROCESSED_SIGNAL}"
            else:
                if source_ref and os.path.exists(str(source_ref)):
                    source_file = str(source_ref)
                else:
                    source_kind = "file"
            if (
                source_kind == "file"
                and not source_file
                and source_ref
                and os.path.exists(str(source_ref))
            ):
                source_file = str(source_ref)

            if source_kind == "file" and not source_file:
                continue

            rows.append(
                {
                    "alias": alias or "UNKNOWN",
                    "technical_type": (technical_type or "").upper() or None,
                    "is_primary": bool(row_group.attrs.get("is_primary", False)),
                    "source_kind": source_kind,
                    "source_container": source_container,
                    "source_dataset": dataset_path,
                    "source_path": str(source_file or ""),
                    "source_row_id": str(row_group.attrs.get("row_id", row_name) or row_name),
                    "capture_metadata": {
                        "integration_time_ms": row_group.attrs.get("integration_time_ms"),
                        "n_frames": row_group.attrs.get("n_frames"),
                        "thickness": row_group.attrs.get("thickness"),
                    },
                }
            )
        return rows
    def _extract_rows_from_canonical_group(self, h5f, schema, h5_path: str):
        rows = []
        tech_group = h5f.get(schema.GROUP_TECHNICAL)
        if tech_group is None:
            tech_group = h5f.get(f"{schema.GROUP_CALIBRATION_SNAPSHOT}/events")
        if tech_group is None:
            return rows

        detector_configs = self.config.get("detectors", []) if hasattr(self, "config") else []
        detector_id_to_alias = {
            str(cfg.get("id")): str(cfg.get("alias"))
            for cfg in detector_configs
            if cfg.get("id") and cfg.get("alias")
        }

        for event_name in sorted(tech_group.keys()):
            if not str(event_name).startswith("tech_evt_"):
                continue
            event_group = tech_group[event_name]
            technical_type = event_group.attrs.get("type", event_group.attrs.get(schema.ATTR_TECHNICAL_TYPE, ""))
            if isinstance(technical_type, bytes):
                technical_type = technical_type.decode("utf-8", errors="replace")
            is_primary = bool(event_group.attrs.get("is_primary", True))

            for detector_name in sorted(event_group.keys()):
                detector_group = event_group[detector_name]
                if schema.DATASET_PROCESSED_SIGNAL not in detector_group:
                    continue

                alias = detector_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                if isinstance(alias, bytes):
                    alias = alias.decode("utf-8", errors="replace")
                if not alias:
                    detector_id = detector_group.attrs.get(schema.ATTR_DETECTOR_ID, "")
                    if isinstance(detector_id, bytes):
                        detector_id = detector_id.decode("utf-8", errors="replace")
                    alias = detector_id_to_alias.get(str(detector_id), str(detector_name).replace("det_", "").upper())

                source_file = detector_group.attrs.get("source_file", "")
                if isinstance(source_file, bytes):
                    source_file = source_file.decode("utf-8", errors="replace")

                dataset_path = f"{event_group.name}/{detector_name}/{schema.DATASET_PROCESSED_SIGNAL}"
                rows.append(
                    {
                        "alias": alias or "UNKNOWN",
                        "technical_type": (str(technical_type or "").upper() or None),
                        "is_primary": is_primary,
                        "source_kind": "container",
                        "source_container": str(h5_path),
                        "source_dataset": dataset_path,
                        "source_path": str(source_file or ""),
                        "source_row_id": f"{event_name}:{detector_name}",
                        "capture_metadata": {
                            "integration_time_ms": detector_group.attrs.get("integration_time_ms"),
                            "n_frames": detector_group.attrs.get("n_frames"),
                            "thickness": detector_group.attrs.get("thickness"),
                        },
                    }
                )
        return rows
    @staticmethod
    def _should_prefer_canonical_rows(runtime_rows, canonical_rows) -> bool:
        """Use canonical container-backed rows when runtime rows point off-machine."""
        if not runtime_rows or not canonical_rows:
            return False
        if len(canonical_rows) < len(runtime_rows):
            return False

        saw_file_row = False
        missing_file_row = False
        for row in runtime_rows:
            if str(row.get("source_kind") or "").strip().lower() != "file":
                return False
            saw_file_row = True
            source_path = str(row.get("source_path") or "").strip()
            if source_path and not os.path.exists(source_path):
                missing_file_row = True

        return saw_file_row and missing_file_row
    @staticmethod
    def _runtime_row_needs_container_backfill(row) -> bool:
        if str(row.get("source_kind") or "").strip().lower() != "file":
            return False
        source_path = str(row.get("source_path") or "").strip()
        return not source_path or not os.path.exists(source_path)
    @staticmethod
    def _runtime_row_match_key(row) -> tuple[str, str]:
        technical_type = str(row.get("technical_type") or "").strip().upper()
        alias = str(row.get("alias") or "").strip().upper()
        return technical_type, alias
    @classmethod
    def _backfill_runtime_rows_from_canonical(cls, runtime_rows, canonical_rows):
        if not runtime_rows or not canonical_rows:
            return list(runtime_rows or [])

        canonical_by_key = {}
        for row in canonical_rows:
            if str(row.get("source_kind") or "").strip().lower() != "container":
                continue
            key = cls._runtime_row_match_key(row)
            if not all(key):
                continue
            canonical_by_key.setdefault(key, []).append(dict(row))

        if not canonical_by_key:
            return list(runtime_rows)

        backfilled_rows = []
        canonical_index_by_key = {}
        for row in runtime_rows:
            patched_row = dict(row)
            if cls._runtime_row_needs_container_backfill(row):
                key = cls._runtime_row_match_key(row)
                candidates = canonical_by_key.get(key, [])
                if candidates:
                    candidate_index = canonical_index_by_key.get(key, 0)
                    if candidate_index < len(candidates):
                        candidate = candidates[candidate_index]
                        canonical_index_by_key[key] = candidate_index + 1
                    else:
                        candidate = candidates[-1]
                    patched_row["source_kind"] = "container"
                    patched_row["source_container"] = str(
                        candidate.get("source_container") or ""
                    )
                    patched_row["source_dataset"] = str(
                        candidate.get("source_dataset") or ""
                    )
            backfilled_rows.append(patched_row)
        return backfilled_rows
