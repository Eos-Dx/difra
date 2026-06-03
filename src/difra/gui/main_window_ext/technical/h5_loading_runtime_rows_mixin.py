"""Technical H5 loading/table population responsibilities."""

from pathlib import Path

import numpy as np

from . import h5_management_mixin as _module
from .h5_loading_runtime_row_collection import H5LoadingRuntimeRowCollectionMixin

logger = _module.logger
QMessageBox = _module.QMessageBox
get_schema = _module.get_schema


class H5LoadingRuntimeRowsMixin(H5LoadingRuntimeRowCollectionMixin):
    def _write_runtime_rows_to_active_container(
        self,
        active_path,
        runtime_rows,
        *,
        show_errors: bool = False,
    ):
        from difra.gui.container_api import get_technical_container

        active_path = Path(active_path)
        if not active_path.exists():
            return False

        schema = get_schema(self.config if hasattr(self, "config") else None)
        technical_container = get_technical_container(
            self.config if hasattr(self, "config") else None
        )

        detector_configs = self.config.get("detectors", []) if hasattr(self, "config") else []
        alias_to_detector_id = {
            str(cfg.get("alias")): str(cfg.get("id"))
            for cfg in detector_configs
            if cfg.get("alias") and cfg.get("id")
        }

        runtime_rows_path = f"{schema.GROUP_RUNTIME}/technical_aux_rows"
        persisted_runtime_rows = []
        for idx, entry in enumerate(runtime_rows, start=1):
            runtime_dataset_path = (
                f"{runtime_rows_path}/row_{idx:06d}/{schema.DATASET_PROCESSED_SIGNAL}"
            )
            mapped_ref = f"h5ref://{active_path}#{runtime_dataset_path}"
            persisted_entry = dict(entry)
            persisted_entry["source_ref"] = mapped_ref
            persisted_entry["row_id"] = str(entry.get("row_id") or f"row_{idx:06d}")
            persisted_runtime_rows.append(persisted_entry)

        runtime_signature = self._runtime_rows_signature(persisted_runtime_rows)
        poni_data = self._collect_poni_data_by_alias()
        poni_signature = self._poni_data_signature(poni_data)
        prefer_draft_distances = bool(
            getattr(self, "_use_draft_distances_for_next_sync", False)
        )
        if prefer_draft_distances:
            setattr(self, "_use_draft_distances_for_next_sync", False)
        distances_for_write = self._distance_map_by_alias(
            prefer_draft=prefer_draft_distances
        )
        if not distances_for_write:
            set_state = getattr(self, "_set_container_state", None)
            if callable(set_state):
                set_state(
                    Path(active_path),
                    state=getattr(self, "STATE_PENDING_DISTANCES", "pending_distances"),
                    reason="missing_distances_before_table_sync",
                )
            if show_errors:
                QMessageBox.warning(
                    self,
                    "Technical Container Sync",
                    "Cannot sync active technical container without a real distance_cm.",
                )
            self._log_technical_event(
                "Technical container sync blocked: missing distance_cm"
            )
            return False

        distance_errors = self._poni_distance_validation_errors(
            poni_data,
            distances_for_write,
            validation_config=self._poni_distance_validation_config(),
        )
        if distance_errors:
            set_state = getattr(self, "_set_container_state", None)
            if callable(set_state):
                set_state(
                    Path(active_path),
                    state=getattr(self, "STATE_PENDING_PONI", "pending_poni"),
                    reason="poni_distance_mismatch",
                )
            details = "\n".join(f"- {msg}" for msg in distance_errors[:8])
            if len(distance_errors) > 8:
                details += f"\n- ... and {len(distance_errors) - 8} more"
            if show_errors:
                QMessageBox.warning(
                    self,
                    "PONI Distance Mismatch",
                    "PONI files do not match the current detector distance.\n\n"
                    + details
                    + "\n\nSelect or generate PONI files for this technical container distance.",
                )
            self._log_technical_event(
                "Technical container sync blocked: PONI distance mismatch: "
                + "; ".join(distance_errors[:4])
            )
            return False

        metadata_errors = self._poni_metadata_validation_errors(poni_data)
        if metadata_errors:
            set_state = getattr(self, "_set_container_state", None)
            if callable(set_state):
                set_state(
                    Path(active_path),
                    state=getattr(self, "STATE_PENDING_PONI", "pending_poni"),
                    reason="poni_metadata_mismatch",
                )
            details = "\n".join(f"- {msg}" for msg in metadata_errors[:8])
            if len(metadata_errors) > 8:
                details += f"\n- ... and {len(metadata_errors) - 8} more"
            if show_errors:
                QMessageBox.warning(
                    self,
                    "PONI Metadata Mismatch",
                    "PONI files do not match the required detector metadata.\n\n"
                    + details
                    + "\n\nSelect or generate PONI files for this detector setup.",
                )
            self._log_technical_event(
                "Technical container sync blocked: PONI metadata mismatch: "
                + "; ".join(metadata_errors[:4])
            )
            return False

        copied_poni_count = self._copy_poni_files_to_container_folder(active_path)
        if copied_poni_count:
            poni_data = self._collect_poni_data_by_alias()
            poni_signature = self._poni_data_signature(poni_data)
            self._log_technical_event(
                f"Copied {copied_poni_count} PONI file(s) next to {active_path.name}"
            )

        try:
            import h5py

            with h5py.File(active_path, "a") as h5f:
                runtime_group = h5f.get(schema.GROUP_RUNTIME)
                existing_signature = ""
                existing_poni_signature = ""
                if runtime_group is not None:
                    existing_signature = str(
                        runtime_group.attrs.get(self.RUNTIME_ROWS_SIGNATURE_ATTR, "") or ""
                    ).strip()
                    existing_poni_signature = str(
                        runtime_group.attrs.get(self.PONI_SIGNATURE_ATTR, "") or ""
                    ).strip()
                if (
                    existing_signature == runtime_signature
                    and existing_poni_signature == poni_signature
                ):
                    sync_state = getattr(self, "_sync_container_state", None)
                    if callable(sync_state):
                        sync_state(Path(active_path), reason="table_sync_noop")
                    return True

            with h5py.File(active_path, "a") as h5f:
                if runtime_rows_path in h5f:
                    del h5f[runtime_rows_path]
                runtime_group = h5f.require_group(runtime_rows_path)

                for idx, entry in enumerate(persisted_runtime_rows, start=1):
                    row_group = runtime_group.create_group(f"row_{idx:06d}")
                    row_group.attrs["row_index"] = int(entry.get("index", idx - 1))
                    if entry["alias"]:
                        row_group.attrs[schema.ATTR_DETECTOR_ALIAS] = str(entry["alias"])
                    if entry["technical_type"]:
                        row_group.attrs["type"] = str(entry["technical_type"])
                    row_group.attrs["is_primary"] = bool(entry["is_primary"])
                    if entry["source_path"]:
                        row_group.attrs["source_file"] = str(entry["source_path"])
                    if entry["source_ref"]:
                        row_group.attrs["source_ref"] = str(entry["source_ref"])
                    if entry["row_id"]:
                        row_group.attrs["row_id"] = str(entry["row_id"])

                    metadata = entry.get("metadata", {})
                    for key in ("integration_time_ms", "n_frames", "thickness"):
                        value = metadata.get(key)
                        if value is not None:
                            row_group.attrs[key] = value

                    dataset_name = str(schema.DATASET_PROCESSED_SIGNAL)
                    if dataset_name in row_group:
                        del row_group[dataset_name]
                    row_group.create_dataset(
                        dataset_name,
                        data=np.asarray(entry["data"]),
                        compression="gzip",
                        compression_opts=schema.COMPRESSION_PROCESSED,
                    )
                runtime_parent = h5f.get(schema.GROUP_RUNTIME)
                if runtime_parent is not None:
                    runtime_parent.attrs[self.RUNTIME_ROWS_SIGNATURE_ATTR] = runtime_signature
                    runtime_parent.attrs[self.PONI_SIGNATURE_ATTR] = poni_signature

            # Rebuild canonical technical group from PRIMARY rows only.
            primary_map = {}
            for entry in persisted_runtime_rows:
                typ = entry.get("technical_type")
                alias = entry.get("alias")
                if not typ or not alias or not entry.get("is_primary"):
                    continue
                if typ not in schema.ALL_TECHNICAL_TYPES:
                    continue
                detector_id = alias_to_detector_id.get(alias, alias)
                payload = {
                    "data": np.asarray(entry["data"]),
                    "detector_id": detector_id,
                    "timestamp": schema.now_timestamp(),
                    "source_file": entry.get("source_path") or entry.get("source_ref"),
                }
                metadata = entry.get("metadata", {})
                if metadata.get("integration_time_ms") is not None:
                    payload[schema.ATTR_INTEGRATION_TIME_MS] = metadata.get("integration_time_ms")
                if metadata.get("n_frames") is not None:
                    payload[schema.ATTR_N_FRAMES] = metadata.get("n_frames")
                if metadata.get("thickness") is not None:
                    payload[schema.ATTR_THICKNESS] = metadata.get("thickness")
                primary_map.setdefault(typ, {})[alias] = payload

            with h5py.File(active_path, "a") as h5f:
                if schema.GROUP_TECHNICAL in h5f:
                    del h5f[schema.GROUP_TECHNICAL]
                technical_group = h5f.create_group(schema.GROUP_TECHNICAL)
                technical_group.attrs[schema.ATTR_NX_CLASS] = schema.NX_CLASS_COLLECTION

                config_group = h5f.create_group(schema.GROUP_TECHNICAL_CONFIG)
                config_group.attrs[schema.ATTR_NX_CLASS] = schema.NX_CLASS_INSTRUMENT

                detectors_group = h5f.create_group(schema.GROUP_INSTRUMENT_DETECTORS)
                detectors_group.attrs[schema.ATTR_NX_CLASS] = schema.NX_CLASS_COLLECTION

                poni_group = h5f.create_group(schema.GROUP_TECHNICAL_PONI)
                poni_group.attrs[schema.ATTR_NX_CLASS] = schema.NX_CLASS_COLLECTION

            try:
                technical_container.write_detector_config(
                    active_path,
                    detector_configs,
                    self._get_active_detector_ids(),
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to refresh detector configuration in active technical container: %s",
                    exc,
                    exc_info=True,
                )

            if poni_data:
                try:
                    technical_container.write_poni_datasets(
                        active_path,
                        poni_data,
                        distances_for_write,
                        detector_id_by_alias=alias_to_detector_id,
                    )
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    logger.warning("Failed to refresh PONI datasets in active technical container", exc_info=True)

            event_index = 1
            agbh_event_indices = {}
            for technical_type in schema.ALL_TECHNICAL_TYPES:
                measurements = primary_map.get(technical_type, {})
                if not measurements:
                    continue
                technical_container.add_technical_event(
                    file_path=active_path,
                    event_index=event_index,
                    technical_type=technical_type,
                    measurements=measurements,
                    timestamp=schema.now_timestamp(),
                    distances_cm=distances_for_write,
                )
                if technical_type == schema.TECHNICAL_TYPE_AGBH:
                    for alias in measurements.keys():
                        agbh_event_indices[alias] = event_index
                event_index += 1

            for alias, evt_idx in agbh_event_indices.items():
                try:
                    technical_container.link_poni_to_event(active_path, alias, evt_idx)
                except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.warning(
                        "Failed to link PONI dataset to event alias=%s event=%s: %s",
                        alias,
                        evt_idx,
                        exc,
                        exc_info=True,
                    )

            if isinstance(distances_for_write, dict) and distances_for_write:
                root_distance = float(next(iter(distances_for_write.values())))
                with h5py.File(active_path, "a") as h5f:
                    h5f.attrs[schema.ATTR_DISTANCE_CM] = root_distance

            aux_table = getattr(self, "auxTable", None)
            if aux_table is not None:
                source_ref_role = self._aux_metadata_role() - 1
                source_info_role = self._aux_source_info_role()

            for entry in persisted_runtime_rows:
                if aux_table is None:
                    break
                if int(entry["index"]) >= aux_table.rowCount():
                    continue
                file_item = aux_table.item(int(entry["index"]), self.AUX_COL_FILE)
                if file_item is None:
                    continue
                file_item.setData(source_ref_role, str(entry["source_ref"] or ""))
                source_info = file_item.data(source_info_role)
                if isinstance(source_info, dict):
                    patched_source_info = dict(source_info)
                else:
                    patched_source_info = {}
                patched_source_info["source_kind"] = "container"
                patched_source_info["container_path"] = str(active_path)
                _parsed_container, parsed_dataset = self._parse_h5ref(str(entry["source_ref"] or ""))
                patched_source_info["dataset_path"] = str(parsed_dataset or "")
                if entry.get("source_path"):
                    patched_source_info["source_path"] = str(entry["source_path"])
                patched_source_info["row_id"] = str(entry.get("row_id") or "")
                file_item.setData(source_info_role, patched_source_info)

            sync_state = getattr(self, "_sync_container_state", None)
            if callable(sync_state):
                sync_state(Path(active_path), reason="table_sync_completed")
            self._log_technical_event(
                f"Technical container synced from table: {active_path.name} (rows={len(persisted_runtime_rows)})"
            )
            return True
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Technical container sync failed: %s", exc, exc_info=True)
            if show_errors:
                QMessageBox.warning(
                    self,
                    "Technical Container Sync",
                    f"Failed to sync active technical container:\n{exc}",
                )
            return False
    def _append_captured_result_files_to_active_container(
        self,
        result_files: dict,
        technical_type: str,
        *,
        show_errors: bool = False,
    ):
        active_path = self._active_technical_container_path_obj()
        if active_path is None or not active_path.exists():
            return False

        if not self._ensure_active_technical_container_available(
            for_edit=True,
            prompt_on_locked=False,
        ):
            return False

        runtime_rows = self._collect_runtime_rows_from_container(active_path)
        normalize_alias_candidates = getattr(
            self,
            "_normalize_technical_alias_candidates",
            None,
        )
        normalize_technical_type = getattr(self, "_normalize_technical_type", None)
        infer_type_from_filename = getattr(self, "_infer_type_from_filename", None)
        capture_metadata_from_path = getattr(self, "_extract_capture_metadata_from_path", None)
        pending_metadata = getattr(self, "_pending_aux_capture_metadata", None)

        def _normalized_type(value):
            raw = str(value or "").strip()
            if callable(normalize_technical_type):
                return normalize_technical_type(raw)
            return raw.upper() or None

        def _same_alias(left, right):
            if callable(normalize_alias_candidates):
                left_tokens = normalize_alias_candidates(left)
                right_tokens = normalize_alias_candidates(right)
                if left_tokens and right_tokens:
                    return bool(left_tokens & right_tokens)
            return str(left or "").strip().upper() == str(right or "").strip().upper()

        valid_technical_types = {
            _normalized_type(option) for option in getattr(self, "TYPE_OPTIONS", []) if option
        }

        def _resolve_technical_type(source_path: str):
            normalized = _normalized_type(technical_type)
            if normalized in valid_technical_types:
                return normalized
            if callable(infer_type_from_filename):
                inferred = _normalized_type(infer_type_from_filename(source_path))
                if inferred in valid_technical_types:
                    return inferred
            return normalized

        appended_rows = []
        for alias, file_path in sorted((result_files or {}).items()):
            source_path = str(file_path or "").strip()
            if not source_path:
                continue
            try:
                data = self._load_aux_entry_array(
                    {
                        "source_path": source_path,
                    }
                )
            except (FileNotFoundError, KeyError, OSError, TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to load captured technical result '%s' for container append: %s",
                    source_path,
                    exc,
                    exc_info=True,
                )
                if show_errors:
                    QMessageBox.warning(
                        self,
                        "Technical Capture",
                        f"Failed to load captured measurement:\n{source_path}\n\n{exc}",
                    )
                continue

            metadata = {}
            if callable(capture_metadata_from_path):
                metadata.update(capture_metadata_from_path(source_path))
            if isinstance(pending_metadata, dict):
                for key, value in pending_metadata.items():
                    if value is not None:
                        metadata[key] = value

            appended_rows.append(
                {
                    "index": len(runtime_rows) + len(appended_rows),
                    "alias": str(alias or "").strip() or None,
                    "technical_type": _resolve_technical_type(source_path),
                    "is_primary": True,
                    "data": data,
                    "source_ref": source_path,
                    "source_path": source_path,
                    "row_id": Path(source_path).stem,
                    "metadata": metadata,
                }
            )

        if not appended_rows:
            return False

        for new_entry in appended_rows:
            new_entry_type = _normalized_type(new_entry.get("technical_type"))
            for existing in runtime_rows:
                if _normalized_type(existing.get("technical_type")) != new_entry_type:
                    continue
                if not _same_alias(existing.get("alias"), new_entry.get("alias")):
                    continue
                existing["is_primary"] = False

        runtime_rows.extend(appended_rows)

        written = self._write_runtime_rows_to_active_container(
            active_path,
            runtime_rows,
            show_errors=show_errors,
        )
        if not written:
            return False

        self._populate_aux_table_from_h5(str(active_path), set_active=False)
        self._log_technical_event(
            f"Technical container updated from new capture: {active_path.name} (added={len(appended_rows)})"
        )
        return True
    def _sync_active_technical_container_from_table(self, show_errors: bool = False):
        active_path = self._active_technical_container_path_obj()
        if active_path is None or not active_path.exists():
            return False

        if not self._ensure_active_technical_container_available(
            for_edit=True,
            prompt_on_locked=False,
        ):
            return False

        runtime_rows = self._collect_runtime_rows_from_table(show_errors=show_errors)
        return self._write_runtime_rows_to_active_container(
            active_path,
            runtime_rows,
            show_errors=show_errors,
        )
    def _on_detector_distances_updated(self):
        if bool(getattr(self, "_suppress_distance_auto_container_creation", False)):
            return None
        active_path = self._create_new_active_technical_container(clear_table=False)
        if active_path is None:
            return None
        self._sync_active_technical_container_from_table(show_errors=False)
        sync_state = getattr(self, "_sync_container_state", None)
        if callable(sync_state):
            sync_state(Path(active_path), reason="distances_updated")
        return Path(active_path)
