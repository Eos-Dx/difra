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



class H5LoadingTableMixin:
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
            original_runtime_rows = self._extract_rows_from_runtime_group(
                h5f,
                schema,
                h5_path,
            )
            canonical_rows = []
            if original_runtime_rows:
                canonical_rows = self._extract_rows_from_canonical_group(h5f, schema, h5_path)
                if self._should_prefer_canonical_rows(original_runtime_rows, canonical_rows):
                    rows = canonical_rows
                else:
                    rows = self._backfill_runtime_rows_from_canonical(
                        original_runtime_rows,
                        canonical_rows,
                    )
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

                        store_candidates = []
                        active_aliases = set()
                        get_active_aliases = getattr(self, "_get_active_detector_aliases", None)
                        if callable(get_active_aliases):
                            try:
                                active_aliases = {
                                    str(item).strip().upper()
                                    for item in (get_active_aliases() or [])
                                    if str(item).strip()
                                }
                            except Exception:
                                active_aliases = set()
                        if active_aliases:
                            for candidate in [alias_key, *sorted(alias_candidates)]:
                                candidate_key = str(candidate or "").strip().upper()
                                if candidate_key in active_aliases:
                                    store_candidates = [candidate_key]
                                    break
                        else:
                            store_candidates = [alias_key] if alias_key else []

                        for candidate in store_candidates:
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
                from difra.gui.main_window_ext.technical import (
                    h5_management_loading_mixin as loading_module,
                )

                detected_masks, mask_stats = loading_module.detect_faulty_pixel_masks(
                    faulty_pixel_records
                )
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
