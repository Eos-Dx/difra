"""Technical H5 loading/table population responsibilities."""

from pathlib import Path

import numpy as np

from . import h5_management_mixin as _module

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


class H5ManagementLoadingMixin:
    @staticmethod
    def _safe_archive_token(value: str, fallback: str = "unknown") -> str:
        token = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in str(value or "")
        ).strip("_")
        return token or fallback

    def validate_technical_h5(self):
        """Deprecated: validation button workflow was removed."""
        QMessageBox.information(
            self,
            "Removed Workflow",
            "Explicit Validate action is removed.\n"
            "Containers are validated automatically when loaded or locked.",
        )
        self._log_technical_event("Validate action removed from technical workflow")

    @staticmethod
    def _parse_h5ref(value: str):
        raw = str(value or "")
        if not raw.startswith("h5ref://"):
            return None, None
        payload = raw[len("h5ref://") :]
        container_path, sep, dataset_path = payload.partition("#")
        if not sep or not container_path or not dataset_path:
            return None, None
        return container_path, dataset_path

    def _distance_map_by_alias(self):
        detector_configs = self.config.get("detectors", []) if hasattr(self, "config") else []
        by_alias = {}
        by_id = getattr(self, "_detector_distances", {}) or {}
        for detector in detector_configs:
            detector_id = detector.get("id")
            alias = detector.get("alias")
            if not detector_id or not alias:
                continue
            if detector_id in by_id:
                try:
                    by_alias[str(alias)] = float(by_id[detector_id])
                except (TypeError, ValueError) as exc:
                    logger.warning(
                        "Failed to parse detector distance for alias=%s id=%s: %s",
                        alias,
                        detector_id,
                        exc,
                    )
        return by_alias

    def _collect_poni_data_by_alias(self):
        poni_data = {}
        ponis = getattr(self, "ponis", {}) or {}
        poni_files = getattr(self, "poni_files", {}) or {}
        for alias, poni_text in ponis.items():
            if not poni_text:
                continue
            info = poni_files.get(alias, {}) if isinstance(poni_files.get(alias, {}), dict) else {}
            poni_name = info.get("name") or f"{alias}.poni"
            poni_data[str(alias)] = (str(poni_text), str(poni_name))
        return poni_data

    def _set_active_technical_container(self, file_path: str):
        self._active_technical_container_path = str(file_path)
        container_manager = get_container_manager(self.config if hasattr(self, "config") else None)
        try:
            self._active_technical_container_locked = bool(
                container_manager.is_container_locked(Path(file_path))
            )
        except (AttributeError, OSError, TypeError, ValueError):
            self._active_technical_container_locked = False
        if hasattr(self, "_refresh_technical_output_folder_lock"):
            try:
                self._refresh_technical_output_folder_lock()
            except (AttributeError, RuntimeError, TypeError) as exc:
                logger.warning(
                    "Failed to refresh technical output lock indicator: %s",
                    exc,
                    exc_info=True,
                )

    def _active_technical_container_path_obj(self):
        raw = str(getattr(self, "_active_technical_container_path", "") or "").strip()
        if not raw:
            return None
        return Path(raw)

    def _append_runtime_log_to_active_technical_container(
        self,
        message: str,
        *,
        channel: str = "",
        source: str = "gui",
    ) -> bool:
        """Append runtime log line to active technical container TXT dataset."""
        raw_message = str(message or "").strip()
        if not raw_message:
            return False

        active_path = self._active_technical_container_path_obj()
        if active_path is None or not active_path.exists():
            return False

        normalized_channel = str(channel or "").strip().upper()
        if not normalized_channel and raw_message.startswith("["):
            right = raw_message.find("]")
            if right > 1:
                normalized_channel = raw_message[1:right].strip().upper()
        if not normalized_channel:
            normalized_channel = "GENERAL"

        schema = get_schema(self.config if hasattr(self, "config") else None)
        runtime_root = str(getattr(schema, "GROUP_RUNTIME", "/runtime") or "/runtime")
        if not runtime_root.startswith("/"):
            runtime_root = f"/{runtime_root}"
        logs_txt_path = f"{runtime_root.rstrip('/')}/difra_logs_txt"

        max_entries = 5000
        if hasattr(self, "config") and isinstance(self.config, dict):
            try:
                max_entries = int(self.config.get("technical_runtime_log_max_entries", 5000))
            except (TypeError, ValueError):
                max_entries = 5000
        if max_entries < 1:
            max_entries = 1

        restore_mode = None
        try:
            stat_result = active_path.stat()
            restore_mode = stat_result.st_mode
            if not os.access(active_path, os.W_OK):
                os.chmod(active_path, restore_mode | 0o200)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            restore_mode = None

        try:
            import h5py

            timestamp = schema.now_timestamp() if hasattr(schema, "now_timestamp") else time.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            new_line = (
                f"{timestamp} | {normalized_channel} | {str(source or 'gui')} | {raw_message}"
            )
            with h5py.File(active_path, "a") as h5f:
                existing_text = ""
                if logs_txt_path in h5f:
                    raw = h5f[logs_txt_path][()]
                    if isinstance(raw, bytes):
                        existing_text = raw.decode("utf-8", errors="replace")
                    else:
                        existing_text = str(raw or "")
                    try:
                        del h5f[logs_txt_path]
                    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                        logger.debug(
                            "Suppressed exception while replacing technical TXT log dataset",
                            exc_info=True,
                        )

                lines = [ln for ln in str(existing_text).splitlines() if str(ln).strip()]
                lines.append(new_line)
                lines = lines[-int(max_entries):]
                payload = "\n".join(lines)

                ds = h5f.create_dataset(logs_txt_path, data=np.bytes_(payload))
                ds.attrs["line_count"] = int(len(lines))
                ds.attrs["last_timestamp"] = str(timestamp)
                ds.attrs["format"] = "txt"
                ds.attrs["encoding"] = "utf-8"
            return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Suppressed exception while writing technical runtime log",
                exc_info=True,
            )
            return False
        finally:
            if restore_mode is not None:
                try:
                    os.chmod(active_path, restore_mode)
                except (OSError, RuntimeError, TypeError, ValueError):
                    logger.debug(
                        "Suppressed exception while restoring technical container mode",
                        exc_info=True,
                    )

    @staticmethod
    def _paths_same(left: Path, right: Path) -> bool:
        try:
            return Path(left).resolve() == Path(right).resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            return str(Path(left)) == str(Path(right))

    @staticmethod
    def _distance_matches(
        actual_cm,
        expected_cm: float,
        tolerance_cm: float = 0.5,
    ) -> bool:
        try:
            if actual_cm is None:
                return False
            return abs(float(actual_cm) - float(expected_cm)) <= float(tolerance_cm)
        except (TypeError, ValueError):
            return False

    def _read_technical_container_distance_cm(self, container_path: Path):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        try:
            with h5py.File(container_path, "r") as h5f:
                distance_attr = h5f.attrs.get(schema.ATTR_DISTANCE_CM)
                if distance_attr is not None:
                    return float(distance_attr)

                tech_group = h5f.get(schema.GROUP_TECHNICAL)
                if tech_group is None:
                    tech_group = h5f.get(f"{schema.GROUP_CALIBRATION_SNAPSHOT}/events")
                if tech_group is not None:
                    for event_name in sorted(tech_group.keys()):
                        event_group = tech_group[event_name]
                        for detector_name in sorted(event_group.keys()):
                            detector_group = event_group[detector_name]
                            distance_attr = detector_group.attrs.get(schema.ATTR_DISTANCE_CM)
                            if distance_attr is not None:
                                return float(distance_attr)

                poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
                if poni_group is not None:
                    for ds_name in sorted(poni_group.keys()):
                        distance_attr = poni_group[ds_name].attrs.get(schema.ATTR_DISTANCE_CM)
                        if distance_attr is not None:
                            return float(distance_attr)
        except (KeyError, OSError, TypeError, ValueError):
            return None

        return None

    def _list_storage_technical_containers(self, storage_folder: Path):
        storage_folder = Path(storage_folder)
        if not storage_folder.exists():
            return []

        candidates = []
        seen = set()
        for pattern in ("technical_*.nxs.h5", "technical_*.h5"):
            for tech_path in storage_folder.glob(pattern):
                if not tech_path.is_file():
                    continue
                try:
                    resolved = str(tech_path.resolve())
                except (OSError, RuntimeError, TypeError, ValueError):
                    resolved = str(tech_path)
                if resolved in seen:
                    continue
                seen.add(resolved)
                candidates.append(tech_path)

        candidates.sort(
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        return candidates

    def _lock_and_archive_technical_container(self, existing_path: Path) -> Path:
        container_manager = get_container_manager(
            self.config if hasattr(self, "config") else None
        )
        existing_path = Path(existing_path)

        if not container_manager.is_container_locked(existing_path):
            operator_id = None
            if hasattr(self, "config") and isinstance(self.config, dict):
                operator_id = self.config.get("operator_id")
            container_manager.lock_container(existing_path, user_id=operator_id)

        archived = self._archive_existing_technical_container_for_replacement(
            existing_path=existing_path,
        )

        current_active = self._active_technical_container_path_obj()
        if current_active is not None and self._paths_same(current_active, existing_path):
            self._active_technical_container_path = ""
            self._active_technical_container_locked = False
            if hasattr(self, "_refresh_technical_output_folder_lock"):
                try:
                    self._refresh_technical_output_folder_lock()
                except (AttributeError, RuntimeError, TypeError) as exc:
                    logger.warning(
                        "Failed to refresh technical output lock after archive: %s",
                        exc,
                        exc_info=True,
                    )

        return archived

    def _archive_existing_technical_container_for_replacement(
        self,
        existing_path: Path,
    ) -> Path:
        from .helpers import _get_technical_archive_folder

        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        container_manager = get_container_manager(
            self.config if hasattr(self, "config") else None
        )
        archive_base = Path(
            _get_technical_archive_folder(self.config if hasattr(self, "config") else None)
        )
        archive_base.mkdir(parents=True, exist_ok=True)

        operator_token = "unknown"
        get_lock_info = getattr(container_manager, "get_lock_info", None)
        if callable(get_lock_info):
            try:
                lock_info = get_lock_info(Path(existing_path)) or {}
                operator_token = self._safe_archive_token(
                    lock_info.get("locked_by") or cfg.get("operator_id", ""),
                    fallback="unknown",
                )
            except (AttributeError, OSError, TypeError, ValueError):
                operator_token = "unknown"

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        archive_dir = archive_base / (
            f"{self._safe_archive_token(Path(existing_path).stem, 'technical')}_"
            f"{operator_token}_{timestamp}"
        )
        suffix = 1
        while archive_dir.exists():
            suffix += 1
            archive_dir = archive_base / (
                f"{self._safe_archive_token(Path(existing_path).stem, 'technical')}_"
                f"{operator_token}_{timestamp}_{suffix}"
            )
        archive_dir.mkdir(parents=True, exist_ok=False)

        destination = archive_dir / Path(existing_path).name
        shutil.move(str(existing_path), str(destination))
        archived_count = 0
        archive_technical_data_files = getattr(
            container_manager,
            "archive_technical_data_files",
            None,
        )
        if callable(archive_technical_data_files):
            file_patterns = None
            if isinstance(cfg, dict):
                file_patterns = cfg.get(
                    "technical_archive_patterns",
                    ["*.txt", "*.dsc", "*.npy", "*.poni", "*_state.json"],
                )
            try:
                archived_count = int(
                    archive_technical_data_files(
                        container_path=Path(existing_path),
                        archive_folder=archive_dir,
                        file_patterns=file_patterns,
                    )
                    or 0
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to archive technical companion files for %s: %s",
                    existing_path,
                    exc,
                    exc_info=True,
                )
        if archived_count > 0:
            self._log_technical_event(
                f"Archived {archived_count} technical companion file(s) to {archive_dir.name}"
            )
            update_paths = getattr(self, "_update_aux_table_paths_after_archive", None)
            if callable(update_paths):
                try:
                    update_paths(archive_dir)
                except (AttributeError, RuntimeError, TypeError):
                    logger.debug(
                        "Suppressed exception in h5_management_loading_mixin.py",
                        exc_info=True,
                    )
        try:
            self._remap_aux_table_container_references(
                old_container_path=Path(existing_path),
                new_container_path=Path(destination),
            )
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.warning(
                "Failed to remap aux table references after technical archive: %s",
                exc,
                exc_info=True,
            )
        return destination

    def _remap_aux_table_container_references(
        self,
        *,
        old_container_path: Path,
        new_container_path: Path,
    ) -> int:
        """Rewrite aux table h5ref/source_info container paths after archive move."""
        if not hasattr(self, "auxTable") or self.auxTable is None:
            return 0

        old_path = Path(old_container_path)
        new_path = Path(new_container_path)
        updated = 0

        source_ref_role = self._aux_metadata_role() - 1
        source_info_role = self._aux_source_info_role()

        for row in range(self.auxTable.rowCount()):
            file_item = self.auxTable.item(row, 1)
            if file_item is None:
                continue

            row_updated = False

            source_ref = str(file_item.data(source_ref_role) or "").strip()
            container_path, dataset_path = self._parse_h5ref(source_ref)
            if container_path and dataset_path:
                try:
                    if self._paths_same(Path(container_path), old_path):
                        file_item.setData(
                            source_ref_role,
                            f"h5ref://{new_path}#{dataset_path}",
                        )
                        row_updated = True
                except (OSError, RuntimeError, TypeError, ValueError):
                    logger.debug(
                        "Suppressed exception in h5_management_loading_mixin.py",
                        exc_info=True,
                    )

            source_info = file_item.data(source_info_role)
            if isinstance(source_info, dict):
                source_container = str(source_info.get("container_path") or "").strip()
                if source_container:
                    try:
                        if self._paths_same(Path(source_container), old_path):
                            patched = dict(source_info)
                            patched["container_path"] = str(new_path)
                            file_item.setData(source_info_role, patched)
                            row_updated = True
                    except (OSError, RuntimeError, TypeError, ValueError):
                        logger.debug(
                            "Suppressed exception in h5_management_loading_mixin.py",
                            exc_info=True,
                        )

            if row_updated:
                updated += 1

        if updated > 0:
            self._log_technical_event(
                f"Remapped {updated} aux row(s) to archived container path: {new_path.name}"
            )
        return updated

    def _attempt_forced_session_send(self, session_path: Path) -> None:
        # Stub transport is handled by SessionLifecycleActions; keep this hook for
        # future real API integration.
        return None

    def _finalize_active_session_for_new_technical_container(self) -> bool:
        return h5_management_loading_actions.finalize_active_session_for_new_technical_container(
            self
        )

    def _prompt_existing_technical_container_resolution(
        self,
        existing_path: Path,
    ):
        return h5_management_loading_actions.prompt_existing_technical_container_resolution(
            self,
            existing_path,
        )

    def _create_new_active_technical_container(self, *, clear_table: bool = False):
        return h5_management_loading_actions.create_new_active_technical_container(
            self,
            clear_table=clear_table,
        )

    def _ensure_active_technical_container_available(
        self,
        *,
        for_edit: bool = False,
        prompt_on_locked: bool = False,
    ) -> bool:
        return h5_management_loading_actions.ensure_active_technical_container_available(
            self,
            for_edit=for_edit,
            prompt_on_locked=prompt_on_locked,
        )

    def _load_aux_entry_array(self, entry):
        source_ref = str(entry.get("source_ref") or "").strip()
        source_path = str(entry.get("source_path") or "").strip()

        container_path, dataset_path = self._parse_h5ref(source_ref)
        if container_path and dataset_path:
            import h5py

            with h5py.File(container_path, "r") as h5f:
                if dataset_path not in h5f:
                    raise KeyError(f"Dataset not found: {container_path}#{dataset_path}")
                data = h5f[dataset_path][()]
                return np.asarray(data)

        if source_path and os.path.exists(source_path):
            return np.asarray(np.load(source_path))

        if source_ref and os.path.exists(source_ref):
            return np.asarray(np.load(source_ref))

        raise FileNotFoundError(
            f"No readable measurement source for row: {source_path or source_ref}"
        )

    def _sync_active_technical_container_from_table(self, show_errors: bool = False):
        from difra.gui.container_api import get_technical_container

        active_path = self._active_technical_container_path_obj()
        if active_path is None or not active_path.exists():
            return False

        if not self._ensure_active_technical_container_available(for_edit=True, prompt_on_locked=False):
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

        runtime_rows = []
        for row in range(self.auxTable.rowCount() if hasattr(self, "auxTable") else 0):
            file_item = self.auxTable.item(row, 1)
            if file_item is None:
                continue

            source_ref = str(file_item.data(self._aux_metadata_role() - 1) or "").strip()
            source_info = file_item.data(self._aux_source_info_role())
            if not isinstance(source_info, dict):
                source_info = {}
            source_path = str(source_info.get("source_path") or "").strip()

            type_cb = self.auxTable.cellWidget(row, 2)
            technical_type = None
            if type_cb is not None and hasattr(type_cb, "currentText"):
                value = type_cb.currentText().strip()
                if value and value != self.NO_SELECTION_LABEL:
                    technical_type = self._normalize_technical_type(value)

            alias_cb = self.auxTable.cellWidget(row, 3)
            alias = None
            if alias_cb is not None and hasattr(alias_cb, "currentText"):
                value = alias_cb.currentText().strip()
                if value and value != self.NO_SELECTION_LABEL:
                    alias = value

            primary_widget = self.auxTable.cellWidget(row, 0)
            is_primary = False
            if primary_widget is not None:
                try:
                    from PyQt5.QtWidgets import QCheckBox

                    checkbox = primary_widget.findChild(QCheckBox)
                except (ImportError, AttributeError, RuntimeError, TypeError):
                    checkbox = None
                if checkbox is not None:
                    is_primary = bool(checkbox.isChecked())

            metadata = self._get_aux_row_metadata(row, source_path or source_ref)

            try:
                data = self._load_aux_entry_array(
                    {
                        "source_ref": source_ref,
                        "source_path": source_path,
                    }
                )
            except (FileNotFoundError, KeyError, OSError, TypeError, ValueError) as exc:
                if show_errors:
                    QMessageBox.warning(
                        self,
                        "Technical Sync",
                        f"Skipping row {row + 1}: {exc}",
                    )
                continue

            runtime_rows.append(
                {
                    "index": row,
                    "alias": alias,
                    "technical_type": technical_type,
                    "is_primary": is_primary,
                    "data": data,
                    "source_ref": source_ref,
                    "source_path": source_path,
                    "row_id": str(source_info.get("row_id") or ""),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                }
            )

        try:
            import h5py

            with h5py.File(active_path, "a") as h5f:
                runtime_rows_path = f"{schema.GROUP_RUNTIME}/technical_aux_rows"
                if runtime_rows_path in h5f:
                    del h5f[runtime_rows_path]
                runtime_group = h5f.require_group(runtime_rows_path)

                for idx, entry in enumerate(runtime_rows, start=1):
                    row_group = runtime_group.create_group(f"row_{idx:06d}")
                    row_group.attrs["row_index"] = int(entry["index"])
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

                    row_group.create_dataset(
                        schema.DATASET_PROCESSED_SIGNAL,
                        data=np.asarray(entry["data"]),
                        compression="gzip",
                        compression_opts=4,
                    )

            # Rebuild canonical technical group from PRIMARY rows only.
            primary_map = {}
            for entry in runtime_rows:
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

            distances_by_alias = self._distance_map_by_alias()
            if distances_by_alias:
                distances_for_write = distances_by_alias
            elif alias_to_detector_id:
                distances_for_write = {alias: 0.0 for alias in alias_to_detector_id.keys()}
            else:
                distances_for_write = 0.0

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

            poni_data = self._collect_poni_data_by_alias()
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
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
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

            self._log_technical_event(
                f"Technical container synced from table: {active_path.name} (rows={len(runtime_rows)})"
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

    def _on_detector_distances_updated(self):
        if bool(getattr(self, "_suppress_distance_auto_container_creation", False)):
            return
        active_path = self._create_new_active_technical_container(clear_table=False)
        if active_path is None:
            return
        self._sync_active_technical_container_from_table(show_errors=False)

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
            if schema.DATASET_PROCESSED_SIGNAL not in row_group:
                continue

            alias = row_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
            if isinstance(alias, bytes):
                alias = alias.decode("utf-8", errors="replace")
            technical_type = row_group.attrs.get("type", "")
            if isinstance(technical_type, bytes):
                technical_type = technical_type.decode("utf-8", errors="replace")

            source_file = row_group.attrs.get("source_file", "")
            if isinstance(source_file, bytes):
                source_file = source_file.decode("utf-8", errors="replace")

            dataset_path = f"{row_group.name}/{schema.DATASET_PROCESSED_SIGNAL}"
            rows.append(
                {
                    "alias": alias or "UNKNOWN",
                    "technical_type": (technical_type or "").upper() or None,
                    "is_primary": bool(row_group.attrs.get("is_primary", False)),
                    "source_kind": "container",
                    "source_container": str(h5_path),
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
    def _normalize_center_preview_alias(alias: str) -> str:
        key = str(alias or "").strip().upper()
        if key == "SAXS":
            return "PRIMARY"
        if key == "WAXS":
            return "SECONDARY"
        return key

    def _detector_sizes_for_center_preview(self):
        sizes = {}
        for detector_cfg in self.config.get("detectors", []) if hasattr(self, "config") else []:
            alias = str(detector_cfg.get("alias") or "").strip()
            if not alias:
                continue
            size_cfg = detector_cfg.get("size", {})
            if isinstance(size_cfg, dict):
                width = size_cfg.get("width", 256)
                height = size_cfg.get("height", 256)
            else:
                width = 256
                height = 256
            try:
                size_tuple = (int(width), int(height))
            except Exception:
                size_tuple = (256, 256)
            sizes[alias] = size_tuple
            sizes[self._normalize_center_preview_alias(alias)] = size_tuple
        return sizes

    def _collect_agbh_images_for_center_preview(self, h5_path: str):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        detector_configs = self.config.get("detectors", []) if hasattr(self, "config") else []
        detector_id_to_alias = {
            str(cfg.get("id")): str(cfg.get("alias"))
            for cfg in detector_configs
            if cfg.get("id") and cfg.get("alias")
        }

        agbh_images = {}

        with h5py.File(h5_path, "r") as h5f:
            tech_group = h5f.get(schema.GROUP_TECHNICAL)
            if tech_group is None:
                tech_group = h5f.get(f"{schema.GROUP_CALIBRATION_SNAPSHOT}/events")
            if tech_group is None:
                return {}

            for event_name in sorted(tech_group.keys()):
                if not str(event_name).startswith("tech_evt_"):
                    continue
                event_group = tech_group[event_name]
                technical_type = event_group.attrs.get(
                    "type",
                    event_group.attrs.get(schema.ATTR_TECHNICAL_TYPE, ""),
                )
                if isinstance(technical_type, bytes):
                    technical_type = technical_type.decode("utf-8", errors="replace")
                if str(technical_type or "").strip().upper() != str(
                    schema.TECHNICAL_TYPE_AGBH
                ).upper():
                    continue

                for detector_name in sorted(event_group.keys()):
                    detector_group = event_group[detector_name]
                    if schema.DATASET_PROCESSED_SIGNAL not in detector_group:
                        continue

                    alias = detector_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                    if isinstance(alias, bytes):
                        alias = alias.decode("utf-8", errors="replace")
                    alias = str(alias or "").strip()
                    if not alias:
                        detector_id = detector_group.attrs.get(schema.ATTR_DETECTOR_ID, "")
                        if isinstance(detector_id, bytes):
                            detector_id = detector_id.decode("utf-8", errors="replace")
                        alias = detector_id_to_alias.get(
                            str(detector_id),
                            str(detector_name).replace("det_", "").upper(),
                        )
                    alias_key = self._normalize_center_preview_alias(alias)
                    if alias_key in agbh_images:
                        continue
                    try:
                        data = detector_group[schema.DATASET_PROCESSED_SIGNAL][()]
                        data = np.asarray(data, dtype=float)
                        if data.ndim == 2:
                            agbh_images[alias_key] = data
                    except Exception:
                        logger.warning(
                            "Failed to extract AGBH image for alias '%s' from %s",
                            alias,
                            h5_path,
                            exc_info=True,
                        )
        return agbh_images

    def _show_poni_center_preview_for_container(
        self,
        h5_path: str,
        *,
        decision_mode: bool = False,
    ):
        validation_cfg = self.config.get("poni_center_validation", {}) if hasattr(self, "config") else {}
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return None if decision_mode else False

        detector_rules = validation_cfg.get("detectors", {})
        if not isinstance(detector_rules, dict) or not detector_rules:
            return None if decision_mode else False

        aliases = [self._normalize_center_preview_alias(a) for a in detector_rules.keys()]
        aliases = [a for a in aliases if a]
        if not aliases:
            return None if decision_mode else False

        ponis = getattr(self, "ponis", {}) or {}
        poni_by_alias = {}
        for alias, text in ponis.items():
            key = self._normalize_center_preview_alias(alias)
            if key and text:
                poni_by_alias[key] = str(text)

        if not poni_by_alias:
            return None if decision_mode else False

        detector_sizes = self._detector_sizes_for_center_preview()
        agbh_images = self._collect_agbh_images_for_center_preview(str(h5_path))

        show_preview = None
        if hasattr(self, "_get_technical_module"):
            show_preview = self._get_technical_module("show_poni_centers_preview_window")
        if not callable(show_preview):
            return None if decision_mode else False

        try:
            dialog = show_preview(
                aliases=aliases,
                poni_by_alias=poni_by_alias,
                detector_sizes_by_alias=detector_sizes,
                validation_cfg=validation_cfg,
                agbh_images_by_alias=agbh_images,
                decision_mode=bool(decision_mode),
                parent=self,
            )
            if bool(decision_mode):
                if isinstance(dialog, dict):
                    result_dialog = dialog.get("dialog")
                    if result_dialog is not None:
                        self._poni_center_preview_dialog = result_dialog
                    return bool(dialog.get("accepted", False))
                if isinstance(dialog, bool):
                    return bool(dialog)
                return None

            if dialog is not None:
                self._poni_center_preview_dialog = dialog
                return True
            return False
        except Exception:
            logger.warning(
                "Failed to show PONI center preview for container %s",
                h5_path,
                exc_info=True,
            )
            return False

    def load_technical_h5(self):
        """Load technical H5 container selected by user."""
        from .helpers import _get_default_folder

        folder = self._current_technical_output_folder()
        if not folder:
            folder = _get_default_folder(self.config if hasattr(self, "config") else None)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Technical HDF5 Container",
            folder,
            "NeXus HDF5 Files (*.nxs.h5 *.h5 *.hdf5);;All Files (*)",
        )
        if not file_path:
            self._log_technical_event("Technical container load cancelled by user")
            return

        self.load_technical_h5_from_path(file_path, show_dialogs=True)

    def load_technical_h5_from_path(self, file_path: str, show_dialogs: bool = True):
        """Load technical container from explicit path and populate table."""
        technical_validator = get_technical_validator(
            self.config if hasattr(self, "config") else None
        )
        validate_technical_container = technical_validator.validate_technical_container
        container_manager = get_container_manager(self.config if hasattr(self, "config") else None)

        file_path = str(file_path)
        if not os.path.exists(file_path):
            if show_dialogs:
                QMessageBox.warning(
                    self,
                    "Container Missing",
                    f"Technical container not found:\n{file_path}",
                )
            return False

        is_locked = container_manager.is_container_locked(file_path)
        lock_status = "🔒 LOCKED" if is_locked else "🔓 UNLOCKED"

        try:
            is_valid, errors, warnings = validate_technical_container(file_path, strict=False)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            if show_dialogs:
                QMessageBox.critical(
                    self,
                    "Validation Error",
                    f"Failed to validate technical container:\n{exc}",
                )
            self._log_technical_event(f"Technical container validation failed: {exc}")
            return False

        if not is_valid and show_dialogs:
            msg = [
                f"Container validation reported {len(errors)} error(s).",
                f"Status: {lock_status}",
                "",
                "Load anyway?",
            ]
            reply = QMessageBox.question(
                self,
                "Validation Issues",
                "\n".join(msg),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._log_technical_event("Load cancelled due to validation errors")
                return False

        try:
            self._populate_aux_table_from_h5(file_path)
            self._set_active_technical_container(file_path)

            if hasattr(self, "on_technical_container_loaded"):
                try:
                    self.on_technical_container_loaded(file_path, is_locked=is_locked)
                except (AttributeError, RuntimeError, TypeError, ValueError) as callback_error:
                    logger.warning(
                        f"Technical-load callback failed: {callback_error}",
                        exc_info=True,
                    )

            if show_dialogs:
                summary = [
                    f"Container loaded: {os.path.basename(file_path)}",
                    f"Status: {lock_status}",
                ]
                if warnings:
                    summary.append(f"Warnings: {len(warnings)}")
                QMessageBox.information(self, "Container Loaded", "\n".join(summary))

            self._log_technical_event(
                f"Loaded technical container: {Path(file_path).name} ({lock_status})"
            )
            self._show_poni_center_preview_for_container(file_path)
            return True
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            if show_dialogs:
                QMessageBox.critical(
                    self,
                    "Load Error",
                    f"Failed to load technical container:\n{exc}",
                )
            logger.error("Error loading technical container: %s", exc, exc_info=True)
            self._log_technical_event(f"Technical container load failed: {exc}")
            return False

    def _populate_aux_table_from_h5(self, h5_path: str, set_active: bool = True):
        """Populate technical table from container runtime rows or canonical events."""
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)

        extracted_distances = {}
        loaded_poni = {}
        loaded_poni_files = {}

        with h5py.File(h5_path, "r") as h5f:
            # Prefer runtime rows for editable in-progress containers.
            rows = self._extract_rows_from_runtime_group(h5f, schema, h5_path)
            if not rows:
                rows = self._extract_rows_from_canonical_group(h5f, schema, h5_path)

            detector_configs = self.config.get("detectors", []) if hasattr(self, "config") else []
            alias_to_detector_id = {
                cfg.get("alias"): cfg.get("id")
                for cfg in detector_configs
                if cfg.get("alias") and cfg.get("id")
            }

            tech_group = h5f.get(schema.GROUP_TECHNICAL)
            if tech_group is None:
                tech_group = h5f.get(f"{schema.GROUP_CALIBRATION_SNAPSHOT}/events")
            if tech_group is not None:
                for event_name in tech_group.keys():
                    if not str(event_name).startswith("tech_evt_"):
                        continue
                    event_group = tech_group[event_name]
                    for detector_name in event_group.keys():
                        detector_group = event_group[detector_name]
                        alias = detector_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                        if isinstance(alias, bytes):
                            alias = alias.decode("utf-8", errors="replace")
                        alias = str(alias or "").strip()
                        detector_id = alias_to_detector_id.get(alias)
                        if not detector_id:
                            continue
                        distance_attr = detector_group.attrs.get(schema.ATTR_DISTANCE_CM)
                        if distance_attr is not None:
                            try:
                                extracted_distances[detector_id] = float(distance_attr)
                            except (TypeError, ValueError) as exc:
                                logger.warning(
                                    "Failed to parse technical distance from event alias=%s id=%s: %s",
                                    alias,
                                    detector_id,
                                    exc,
                                )

            poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
            if poni_group is not None:
                for ds_name in sorted(poni_group.keys()):
                    try:
                        ds = poni_group[ds_name]
                        poni_blob = ds[()]
                        if isinstance(poni_blob, bytes):
                            poni_text = poni_blob.decode("utf-8", errors="replace")
                        else:
                            poni_text = str(poni_blob)

                        alias = ds.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                        if isinstance(alias, bytes):
                            alias = alias.decode("utf-8", errors="replace")
                        alias = str(alias or "").strip()
                        if not alias and str(ds_name).startswith("poni_"):
                            alias = str(ds_name)[5:].upper()
                        if not alias:
                            continue

                        poni_filename = ds.attrs.get("poni_filename", "")
                        if isinstance(poni_filename, bytes):
                            poni_filename = poni_filename.decode("utf-8", errors="replace")

                        loaded_poni[alias] = poni_text
                        loaded_poni_files[alias] = {
                            "path": "",
                            "name": str(poni_filename or f"{alias}.poni"),
                        }

                        distance_attr = ds.attrs.get(schema.ATTR_DISTANCE_CM)
                        detector_id = None
                        for cfg in detector_configs:
                            if cfg.get("alias") == alias:
                                detector_id = cfg.get("id")
                                break
                        if detector_id and distance_attr is not None:
                            extracted_distances[detector_id] = float(distance_attr)
                    except (KeyError, OSError, TypeError, ValueError) as poni_err:
                        logger.warning("Failed to parse PONI dataset '%s': %s", ds_name, poni_err)

        self._restoring_aux_table = True
        try:
            self.auxTable.setRowCount(0)
            for row in rows:
                self._add_aux_item_to_list(
                    row.get("alias") or "UNKNOWN",
                    row.get("source_path") or row.get("source_row_id") or "",
                    source_kind=row.get("source_kind") or "container",
                    source_container=row.get("source_container") or str(h5_path),
                    source_dataset=row.get("source_dataset") or "",
                    technical_type=row.get("technical_type"),
                    is_primary=bool(row.get("is_primary")),
                    source_row_id=row.get("source_row_id") or "",
                    explicit_metadata=row.get("capture_metadata")
                    if isinstance(row.get("capture_metadata"), dict)
                    else None,
                )
        finally:
            self._restoring_aux_table = False

        if loaded_poni:
            if not isinstance(getattr(self, "ponis", None), dict):
                self.ponis = {}
            if not isinstance(getattr(self, "poni_files", None), dict):
                self.poni_files = {}
            self.ponis.update(loaded_poni)
            self.poni_files.update(loaded_poni_files)

        if extracted_distances:
            self._detector_distances = extracted_distances
            if hasattr(self, "_update_window_title_with_distances"):
                self._update_window_title_with_distances()
            if hasattr(self, "_update_distance_dependent_controls"):
                self._update_distance_dependent_controls()

        if set_active:
            self._set_active_technical_container(h5_path)
        self._log_technical_event(
            f"Populated technical table from container: {Path(h5_path).name} (rows={self.auxTable.rowCount()})"
        )
