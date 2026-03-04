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
                except Exception:
                    pass
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
        except Exception:
            self._active_technical_container_locked = False
        if hasattr(self, "_refresh_technical_output_folder_lock"):
            try:
                self._refresh_technical_output_folder_lock()
            except Exception:
                pass

    def _active_technical_container_path_obj(self):
        raw = str(getattr(self, "_active_technical_container_path", "") or "").strip()
        if not raw:
            return None
        return Path(raw)

    @staticmethod
    def _paths_same(left: Path, right: Path) -> bool:
        try:
            return Path(left).resolve() == Path(right).resolve()
        except Exception:
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
        except Exception:
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
        except Exception:
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
                except Exception:
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
                except Exception:
                    pass

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
            except Exception:
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
        return destination

    def _attempt_forced_session_send(self, session_path: Path) -> None:
        raise RuntimeError("Cloud session upload API is not available in this build.")

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
                except Exception:
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
            except Exception as exc:
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
            except Exception:
                pass

            poni_data = self._collect_poni_data_by_alias()
            if poni_data:
                try:
                    technical_container.write_poni_datasets(
                        active_path,
                        poni_data,
                        distances_for_write,
                        detector_id_by_alias=alias_to_detector_id,
                    )
                except Exception:
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
                except Exception:
                    pass

            if isinstance(distances_for_write, dict) and distances_for_write:
                root_distance = float(next(iter(distances_for_write.values())))
                with h5py.File(active_path, "a") as h5f:
                    h5f.attrs[schema.ATTR_DISTANCE_CM] = root_distance

            self._log_technical_event(
                f"Technical container synced from table: {active_path.name} (rows={len(runtime_rows)})"
            )
            return True
        except Exception as exc:
            logger.warning("Technical container sync failed: %s", exc, exc_info=True)
            if show_errors:
                QMessageBox.warning(
                    self,
                    "Technical Container Sync",
                    f"Failed to sync active technical container:\n{exc}",
                )
            return False

    def _on_detector_distances_updated(self):
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
        except Exception as exc:
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
                except Exception as callback_error:
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
            return True
        except Exception as exc:
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
                            except Exception:
                                pass

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
                    except Exception as poni_err:
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
