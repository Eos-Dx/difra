"""Technical H5 loading/table population responsibilities."""

from pathlib import Path
import hashlib
import json

import numpy as np

from . import h5_management_mixin as _module
from .poni_center_validation import resolve_poni_rule_alias
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


class H5ManagementLoadingMixin:
    RUNTIME_ROWS_SIGNATURE_ATTR = "technical_aux_rows_signature"
    PONI_SIGNATURE_ATTR = "technical_poni_signature"

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

    def _canonical_faulty_pixel_alias(self, *values) -> str:
        normalize = getattr(self, "_normalize_technical_alias_candidates", None)
        candidates = set()
        for value in values:
            token = str(value or "").strip()
            if not token:
                continue
            if callable(normalize):
                candidates.update(normalize(token))
            else:
                upper = token.upper()
                candidates.add(upper)
                if upper.startswith("DET_"):
                    candidates.add(upper[4:])
                else:
                    candidates.add(f"DET_{upper}")

        primary_tokens = {"PRIMARY", "SAXS", "DET_PRIMARY", "DET_SAXS"}
        secondary_tokens = {"SECONDARY", "WAXS", "DET_SECONDARY", "DET_WAXS"}
        if candidates & primary_tokens:
            return "PRIMARY"
        if candidates & secondary_tokens:
            return "SECONDARY"
        return ""

    def _apply_loaded_masks(self, loaded_masks: dict):
        if not loaded_masks:
            return
        if not isinstance(getattr(self, "masks", None), dict):
            self.masks = {}
        self.masks.update(loaded_masks)
        for widget in (getattr(self, "measurement_widgets", {}) or {}).values():
            if hasattr(widget, "masks"):
                widget.masks = self.masks

    @staticmethod
    def _poni_data_signature(poni_data) -> str:
        normalized = {}
        for alias, payload in sorted((poni_data or {}).items()):
            try:
                poni_text, poni_name = payload
            except Exception:
                poni_text, poni_name = "", ""
            normalized[str(alias)] = {
                "name": str(poni_name or ""),
                "content_sha256": hashlib.sha256(
                    str(poni_text or "").encode("utf-8")
                ).hexdigest(),
            }
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

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
        infer_state = getattr(self, "_infer_container_state", None)
        if callable(infer_state):
            try:
                self._active_technical_container_state = str(
                    infer_state(Path(file_path)) or ""
                ).strip()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                logger.debug(
                    "Suppressed exception while inferring container state on activation",
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

    def _list_archived_technical_containers(self):
        return technical_startup_reconcile.list_archived_technical_containers(self)

    def _find_duplicate_archived_technical_container(self, container_path: Path):
        return technical_startup_reconcile.find_duplicate_archived_technical_container(
            self,
            container_path,
        )

    def _delete_storage_technical_container(self, container_path: Path) -> bool:
        return technical_startup_reconcile.delete_storage_technical_container(
            self,
            container_path,
        )

    def _format_startup_technical_container_option(self, container_path: Path) -> str:
        return technical_startup_reconcile.format_startup_technical_container_option(
            self,
            container_path,
        )

    def _prompt_startup_technical_container_selection(self, candidates):
        return technical_startup_reconcile.prompt_startup_technical_container_selection(
            self,
            candidates,
        )

    def reconcile_startup_technical_containers(self):
        return technical_startup_reconcile.reconcile_startup_technical_containers(self)

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
            set_state = getattr(self, "_set_container_state", None)
            if callable(set_state):
                set_state(
                    Path(existing_path),
                    state=getattr(self, "STATE_LOCKED", "locked"),
                    reason="locked_before_archive",
                )

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
        set_state = getattr(self, "_set_container_state", None)
        if callable(set_state):
            set_state(
                Path(destination),
                state=getattr(self, "STATE_ARCHIVED", "archived"),
                reason="archived_for_replacement",
            )
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
        try:
            from difra.gui.session_lifecycle_service import SessionLifecycleService

            SessionLifecycleService.copy_archive_item_to_mirror(
                archive_dir,
                config=self.config if hasattr(self, "config") else None,
                archive_kind="technical",
            )
        except Exception as exc:
            logger.warning(
                "Failed to mirror archived technical container folder %s: %s",
                archive_dir,
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

    @staticmethod
    def _json_safe_runtime_value(value):
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, dict):
            return {
                str(key): H5ManagementLoadingMixin._json_safe_runtime_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                H5ManagementLoadingMixin._json_safe_runtime_value(item)
                for item in value
            ]
        return value

    @staticmethod
    def _normalize_runtime_row_for_signature(entry):
        metadata = entry.get("metadata", {}) if isinstance(entry.get("metadata"), dict) else {}
        normalized_metadata = {
            "integration_time_ms": H5ManagementLoadingMixin._json_safe_runtime_value(
                metadata.get("integration_time_ms")
            ),
            "n_frames": H5ManagementLoadingMixin._json_safe_runtime_value(
                metadata.get("n_frames")
            ),
            "thickness": H5ManagementLoadingMixin._json_safe_runtime_value(
                metadata.get("thickness")
            ),
        }
        return {
            "alias": H5ManagementLoadingMixin._json_safe_runtime_value(
                str(entry.get("alias") or "")
            ),
            "technical_type": H5ManagementLoadingMixin._json_safe_runtime_value(
                str(entry.get("technical_type") or "")
            ),
            "is_primary": H5ManagementLoadingMixin._json_safe_runtime_value(
                bool(entry.get("is_primary"))
            ),
            "source_ref": H5ManagementLoadingMixin._json_safe_runtime_value(
                str(entry.get("source_ref") or "")
            ),
            "source_path": H5ManagementLoadingMixin._json_safe_runtime_value(
                str(entry.get("source_path") or "")
            ),
            "row_id": H5ManagementLoadingMixin._json_safe_runtime_value(
                str(entry.get("row_id") or "")
            ),
            "metadata": normalized_metadata,
        }

    @classmethod
    def _runtime_rows_signature(cls, runtime_rows):
        payload = [
            cls._normalize_runtime_row_for_signature(entry)
            for entry in (runtime_rows or [])
        ]
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _collect_runtime_rows_from_table(self, show_errors: bool = False):
        runtime_rows = []
        for row in range(self.auxTable.rowCount() if hasattr(self, "auxTable") else 0):
            file_item = self.auxTable.item(row, self.AUX_COL_FILE)
            if file_item is None:
                continue

            source_ref = str(file_item.data(self._aux_metadata_role() - 1) or "").strip()
            source_info = file_item.data(self._aux_source_info_role())
            if not isinstance(source_info, dict):
                source_info = {}
            source_path = str(source_info.get("source_path") or "").strip()

            type_cb = self.auxTable.cellWidget(row, self.AUX_COL_TYPE)
            technical_type = None
            if type_cb is not None and hasattr(type_cb, "currentText"):
                value = type_cb.currentText().strip()
                if value and value != self.NO_SELECTION_LABEL:
                    technical_type = self._normalize_technical_type(value)

            alias_cb = self.auxTable.cellWidget(row, self.AUX_COL_ALIAS)
            alias = None
            if alias_cb is not None and hasattr(alias_cb, "currentText"):
                value = alias_cb.currentText().strip()
                if value and value != self.NO_SELECTION_LABEL:
                    alias = value

            primary_widget = self.auxTable.cellWidget(row, self.AUX_COL_PRIMARY)
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
        return runtime_rows

    def _collect_runtime_rows_from_container(self, container_path):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        h5_path = str(container_path)

        with h5py.File(h5_path, "r") as h5f:
            runtime_rows = self._extract_rows_from_runtime_group(h5f, schema, h5_path)
            canonical_rows = []
            if runtime_rows:
                canonical_rows = self._extract_rows_from_canonical_group(h5f, schema, h5_path)
                runtime_rows = self._backfill_runtime_rows_from_canonical(
                    runtime_rows,
                    canonical_rows,
                )
                if self._should_prefer_canonical_rows(runtime_rows, canonical_rows):
                    rows = canonical_rows
                else:
                    rows = runtime_rows
            else:
                rows = self._extract_rows_from_canonical_group(h5f, schema, h5_path)

        normalized_rows = []
        for idx, row in enumerate(rows):
            source_kind = str(row.get("source_kind") or "").strip().lower()
            source_path = str(row.get("source_path") or "").strip()
            source_container = str(row.get("source_container") or h5_path).strip()
            source_dataset = str(row.get("source_dataset") or "").strip()
            source_ref = ""
            if source_kind == "container" and source_container and source_dataset:
                source_ref = f"h5ref://{source_container}#{source_dataset}"
            elif source_path:
                source_ref = source_path

            try:
                data = self._load_aux_entry_array(
                    {
                        "source_ref": source_ref,
                        "source_path": source_path,
                    }
                )
            except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
                logger.warning(
                    "Skipping unreadable runtime row while collecting container-backed rows: %s",
                    row,
                    exc_info=True,
                )
                continue

            metadata = row.get("capture_metadata", {})
            normalized_rows.append(
                {
                    "index": idx,
                    "alias": row.get("alias"),
                    "technical_type": row.get("technical_type"),
                    "is_primary": bool(row.get("is_primary")),
                    "data": data,
                    "source_ref": source_ref,
                    "source_path": source_path,
                    "row_id": str(row.get("source_row_id") or f"row_{idx + 1:06d}"),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                }
            )
        return normalized_rows

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

            source_ref_role = self._aux_metadata_role() - 1
            source_info_role = self._aux_source_info_role()

            for entry in persisted_runtime_rows:
                if not hasattr(self, "auxTable") or self.auxTable is None:
                    break
                if int(entry["index"]) >= self.auxTable.rowCount():
                    continue
                file_item = self.auxTable.item(int(entry["index"]), self.AUX_COL_FILE)
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

        normalized_type = _normalized_type(technical_type)
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
                    "technical_type": normalized_type,
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
            for existing in runtime_rows:
                if _normalized_type(existing.get("technical_type")) != normalized_type:
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
            return
        active_path = self._create_new_active_technical_container(clear_table=False)
        if active_path is None:
            return
        self._sync_active_technical_container_from_table(show_errors=False)
        sync_state = getattr(self, "_sync_container_state", None)
        if callable(sync_state):
            sync_state(Path(active_path), reason="distances_updated")

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
                source_container = str(parsed_container)
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

    def _normalize_center_preview_alias(self, alias: str) -> str:
        detector_cfgs = self.config.get("detectors", []) if hasattr(self, "config") else []
        return resolve_poni_rule_alias(alias, detector_cfgs)

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

        poni_by_alias = {}
        try:
            embedded = self._collect_container_poni_text_by_alias(Path(h5_path))
        except Exception:
            embedded = {}
        for alias, text in (embedded or {}).items():
            key = self._normalize_center_preview_alias(alias)
            if key and text:
                poni_by_alias[key] = str(text)

        if not poni_by_alias:
            ponis = getattr(self, "ponis", {}) or {}
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
            self._loading_technical_container = True
            try:
                self._populate_aux_table_from_h5(file_path)
                self._set_active_technical_container(file_path)
                infer_state = getattr(self, "_infer_container_state", None)
                if callable(infer_state):
                    try:
                        self._active_technical_container_state = str(
                            infer_state(Path(file_path)) or ""
                        ).strip()
                    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                        logger.debug(
                            "Suppressed exception while inferring container state on load",
                            exc_info=True,
                        )
            finally:
                self._loading_technical_container = False

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
        detected_masks = {}
        loaded_poni = {}
        loaded_poni_files = {}
        faulty_pixel_records = []

        with h5py.File(h5_path, "r") as h5f:
            def _read_text_value(value) -> str:
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return str(value or "")

            def _read_poni_text_from_path(ref_path: str) -> str:
                ref = str(ref_path or "").strip()
                if not ref or ref not in h5f:
                    return ""
                try:
                    return _read_text_value(h5f[ref][()]).strip()
                except Exception:
                    logger.debug(
                        "Failed to read embedded PONI dataset '%s' from %s",
                        ref,
                        h5_path,
                        exc_info=True,
                    )
                    return ""

            def _read_detector_linked_poni_text(detector_group) -> str:
                candidate_paths = []
                attr_poni_ref = getattr(schema, "ATTR_PONI_REF", "poni_ref")
                for attr_name in (attr_poni_ref, "poni_path"):
                    ref_path = _read_text_value(detector_group.attrs.get(attr_name, "")).strip()
                    if ref_path and ref_path not in candidate_paths:
                        candidate_paths.append(ref_path)

                role_name = str(detector_group.name.rsplit("/", 1)[-1] or "").strip()
                technical_poni_group = getattr(
                    schema,
                    "GROUP_TECHNICAL_PONI",
                    "/entry/technical/poni",
                )
                if role_name:
                    suffixes = [role_name]
                    if role_name.startswith("det_"):
                        suffixes.insert(0, role_name[4:])
                    for suffix in suffixes:
                        canonical_path = f"{technical_poni_group}/poni_{suffix}"
                        if canonical_path not in candidate_paths:
                            candidate_paths.append(canonical_path)

                for ref_path in candidate_paths:
                    poni_text = _read_poni_text_from_path(ref_path)
                    if poni_text:
                        return poni_text
                return ""

            # Prefer runtime rows for editable in-progress containers, but fall
            # back to canonical container-backed datasets when runtime metadata
            # only references missing external files from another machine.
            runtime_rows = self._extract_rows_from_runtime_group(h5f, schema, h5_path)
            canonical_rows = []
            if runtime_rows:
                canonical_rows = self._extract_rows_from_canonical_group(h5f, schema, h5_path)
                runtime_rows = self._backfill_runtime_rows_from_canonical(
                    runtime_rows,
                    canonical_rows,
                )
                if self._should_prefer_canonical_rows(runtime_rows, canonical_rows):
                    rows = canonical_rows
                else:
                    rows = runtime_rows
            else:
                rows = self._extract_rows_from_canonical_group(h5f, schema, h5_path)

            detector_configs = self.config.get("detectors", []) if hasattr(self, "config") else []

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
                        detector_attr_id = ds.attrs.get(
                            getattr(schema, "ATTR_DETECTOR_ID", "detector_id"),
                            "",
                        )
                        alias_key, detector_id, alias_candidates = (
                            self._resolve_configured_technical_alias(
                                alias,
                                detector_attr_id,
                                str(ds_name),
                            )
                        )
                        if not alias_key and not alias_candidates:
                            continue

                        poni_filename = ds.attrs.get("poni_filename", "")
                        if isinstance(poni_filename, bytes):
                            poni_filename = poni_filename.decode("utf-8", errors="replace")

                        for candidate in [alias_key, *sorted(alias_candidates)]:
                            store_key = str(candidate or "").strip().upper()
                            if not store_key:
                                continue
                            loaded_poni[store_key] = poni_text
                            loaded_poni_files[store_key] = {
                                "path": "",
                                "name": str(poni_filename or f"{store_key}.poni"),
                            }

                        distance_attr = ds.attrs.get(schema.ATTR_DISTANCE_CM)
                        if detector_id and distance_attr is not None:
                            extracted_distances[detector_id] = float(distance_attr)
                    except (KeyError, OSError, TypeError, ValueError) as poni_err:
                        logger.warning("Failed to parse PONI dataset '%s': %s", ds_name, poni_err)

            tech_group = h5f.get(schema.GROUP_TECHNICAL)
            if tech_group is None:
                tech_group = h5f.get(f"{schema.GROUP_CALIBRATION_SNAPSHOT}/events")
            if tech_group is not None:
                dataset_name = getattr(schema, "DATASET_PROCESSED_SIGNAL", "processed_signal")
                for event_name in tech_group.keys():
                    if not str(event_name).startswith("tech_evt_"):
                        continue
                    event_group = tech_group[event_name]
                    for detector_name in event_group.keys():
                        detector_group = event_group[detector_name]
                        alias = detector_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                        detector_attr_id = detector_group.attrs.get(
                            getattr(schema, "ATTR_DETECTOR_ID", "detector_id"),
                            "",
                        )
                        resolved_alias, detector_id, alias_candidates = (
                            self._resolve_configured_technical_alias(
                                alias,
                                detector_attr_id,
                                detector_name,
                            )
                        )
                        if detector_id:
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

                        if dataset_name not in detector_group:
                            continue
                        try:
                            image = np.asarray(detector_group[dataset_name][()], dtype=float)
                        except Exception:
                            logger.debug(
                                "Failed to read processed signal for %s/%s from %s",
                                event_name,
                                detector_name,
                                h5_path,
                                exc_info=True,
                            )
                            continue
                        if image.ndim != 2 or image.size <= 0:
                            continue

                        canonical_alias = self._canonical_faulty_pixel_alias(
                            resolved_alias,
                            *sorted(alias_candidates),
                            detector_name,
                        )
                        if not canonical_alias:
                            continue

                        poni_text = _read_detector_linked_poni_text(detector_group)
                        if not poni_text:
                            for candidate in [resolved_alias, *sorted(alias_candidates)]:
                                store_key = str(candidate or "").strip().upper()
                                if store_key and store_key in loaded_poni:
                                    poni_text = str(loaded_poni.get(store_key) or "").strip()
                                    if poni_text:
                                        break

                        faulty_pixel_records.append(
                            {
                                "alias": canonical_alias,
                                "image": image,
                                "poni_text": poni_text,
                                "meas_name": f"{canonical_alias}_{event_name}_{detector_name}",
                            }
                        )

        if faulty_pixel_records:
            try:
                detected_masks, mask_stats = detect_faulty_pixel_masks(faulty_pixel_records)
                if detected_masks:
                    self._apply_loaded_masks(detected_masks)
                    logger.info(
                        "Loaded automatic faulty-pixel masks from %s: PRIMARY=%s SECONDARY=%s",
                        Path(h5_path).name,
                        int(np.count_nonzero(detected_masks.get("PRIMARY"))) if "PRIMARY" in detected_masks else 0,
                        int(np.count_nonzero(detected_masks.get("SECONDARY"))) if "SECONDARY" in detected_masks else 0,
                    )
                    if hasattr(self, "_log_technical_event"):
                        self._log_technical_event(
                            "Auto-detected faulty pixels from container: "
                            f"PRIMARY={int(np.count_nonzero(detected_masks.get('PRIMARY'))) if 'PRIMARY' in detected_masks else 0}, "
                            f"SECONDARY={int(np.count_nonzero(detected_masks.get('SECONDARY'))) if 'SECONDARY' in detected_masks else 0}"
                        )
                else:
                    logger.debug(
                        "No automatic faulty-pixel masks detected from %s: %s",
                        h5_path,
                        mask_stats,
                    )
            except Exception:
                logger.warning(
                    "Failed to auto-detect faulty pixels from %s",
                    h5_path,
                    exc_info=True,
                )

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
            for widget in (getattr(self, "measurement_widgets", {}) or {}).values():
                if hasattr(widget, "ponis"):
                    widget.ponis = self.ponis
                if hasattr(widget, "masks") and isinstance(getattr(self, "masks", None), dict):
                    widget.masks = self.masks

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
