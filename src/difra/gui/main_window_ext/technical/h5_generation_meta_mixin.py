"""Technical metadata generation responsibilities for H5 generation."""

from . import h5_generation_mixin as _module
from .poni_center_validation import validate_poni_centers

json = _module.json
logger = _module.logger
os = _module.os
re = _module.re
uuid = _module.uuid
QMessageBox = _module.QMessageBox
QComboBox = _module.QComboBox
QDialog = _module.QDialog
QFileDialog = _module.QFileDialog
QInputDialog = _module.QInputDialog
QCheckBox = _module.QCheckBox
Qt = _module.Qt

get_schema = _module.get_schema


class H5GenerationMetaMixin:
    @staticmethod
    def _is_combobox_like(widget) -> bool:
        """Support real/stubbed combo boxes without strict class identity checks."""
        return widget is not None and callable(getattr(widget, "currentText", None))

    def _parse_poni_distance_m(self, poni_text: str):
        """Parse Distance from PONI text in meters. Returns None if not found/invalid."""
        if not poni_text:
            return None
        try:
            m = re.search(r"^Distance:\s*([0-9.eE+-]+)", poni_text, flags=re.MULTILINE)
            return float(m.group(1)) if m else None
        except Exception:
            return None

    @staticmethod
    def _to_float_or_none(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def _resolve_fake_demo_center_px(self, alias: str, detector_size):
        """Resolve fake demo PONI center from configured center validation rules."""
        alias_key = str(alias or "").strip().upper()
        # Fixed demo targets requested by workflow for fake PRIMARY/SECONDARY.
        if alias_key == "PRIMARY":
            return 128.0, 8.0
        if alias_key == "SECONDARY":
            return 128.0, 280.0

        try:
            width = float(detector_size[0])
            height = float(detector_size[1])
        except Exception:
            width, height = 256.0, 256.0

        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict):
            validation_cfg = {}

        defaults = validation_cfg.get("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}

        rules = validation_cfg.get("detectors", {})
        if not isinstance(rules, dict):
            rules = {}

        alias_key = str(alias or "").strip().upper()
        rule = dict(defaults) if defaults else {}
        for key, value in rules.items():
            if str(key or "").strip().upper() == alias_key and isinstance(value, dict):
                rule.update(value)
                break

        row_target = self._to_float_or_none(rule.get("row_target_px"))
        if row_target is None:
            row_target = height / 2.0

        col_target = self._to_float_or_none(rule.get("col_target_px"))
        col_min = self._to_float_or_none(rule.get("col_min_px"))
        col_max = self._to_float_or_none(rule.get("col_max_px"))
        col_gt = self._to_float_or_none(rule.get("col_gt_px"))
        col_lt = self._to_float_or_none(rule.get("col_lt_px"))

        if col_target is not None:
            col = float(col_target)
        elif col_gt is not None and col_lt is not None and float(col_lt) > float(col_gt):
            col = (float(col_gt) + float(col_lt)) / 2.0
        elif col_gt is not None:
            col = float(col_gt) + 1.0
        elif col_min is not None and col_max is not None and float(col_max) >= float(col_min):
            col = (float(col_min) + float(col_max)) / 2.0
        elif col_min is not None:
            col = float(col_min)
        elif col_lt is not None:
            col = float(col_lt) - 1.0
        elif col_max is not None:
            col = float(col_max)
        else:
            col = width / 2.0

        if col_gt is not None and not (col > float(col_gt)):
            col = float(col_gt) + 1.0
        if col_min is not None and col < float(col_min):
            col = float(col_min)
        if col_lt is not None and not (col < float(col_lt)):
            col = float(col_lt) - 1.0
        if col_max is not None and col > float(col_max):
            col = float(col_max)

        return float(row_target), float(col)
    
    def _generate_fake_poni_data(self, aliases, user_distance_cm):
        """Generate fake PONI data for dev mode with distances within ±3% of user value.
        
        Args:
            aliases: List of detector aliases
            user_distance_cm: User-specified distance in cm
        
        Returns:
            Dict mapping alias to tuple of (poni_content, poni_filename)
        """
        import random
        import time
        
        poni_data = {}
        
        for alias in aliases:
            # Get detector config
            detector_config = None
            for d in self.config.get("detectors", []):
                if d.get("alias") == alias:
                    detector_config = d
                    break
            
            if not detector_config:
                detector_config = {"alias": alias}
            
            # Generate distance within ±3% margin (inside the 5% validation tolerance)
            random.seed(hash(alias))  # Consistent values for same detector
            margin = random.uniform(-0.03, 0.03)
            fake_distance_m = (user_distance_cm / 100.0) * (1 + margin)
            
            # Get detector size or use defaults
            size = detector_config.get("size", {"width": 256, "height": 256})
            width = size.get("width", 256)
            height = size.get("height", 256)
            
            # Generate pixel sizes (typically 55um or 100um)
            pixel_size = detector_config.get("pixel_size_um", [55, 55])
            pixel1 = pixel_size[0] * 1e-6 if len(pixel_size) > 0 else 5.5e-05
            pixel2 = pixel_size[1] * 1e-6 if len(pixel_size) > 1 else 5.5e-05
            row_px, col_px = self._resolve_fake_demo_center_px(alias, (width, height))
            poni1 = float(row_px) * float(pixel1)
            poni2 = float(col_px) * float(pixel2)
            
            wavelength = 1.5406e-10  # Typical Cu Kα wavelength
            
            current_time = time.strftime("%a %b %d %H:%M:%S %Y")
            
            poni_content = f"""# Nota: C-Order, 1 refers to the Y axis, 2 to the X axis
# Calibration done on {current_time} (DEV MODE - FAKE DATA)
poni_version: 2.1
Detector: Detector
Detector_config: {{"pixel1": {pixel1}, "pixel2": {pixel2}, "max_shape": [{height}, {width}], "orientation": 3}}
Distance: {fake_distance_m}
Poni1: {poni1}
Poni2: {poni2}
Rot1: 0
Rot2: 0
Rot3: 0
Wavelength: {wavelength}
# Calibrant: AgBh (DEV MODE)
# Detector: {alias} (DEV MODE - FAKE DATA)
# User specified: {user_distance_cm:.2f} cm, Generated: {fake_distance_m*100:.2f} cm (margin: {margin*100:.1f}%)
"""
            
            poni_filename = f"{alias.lower()}_fake_h5gen.poni"
            poni_data[alias] = (poni_content, poni_filename)
            
            logger.info(
                f"Generated fake PONI for {alias}: distance={fake_distance_m*100:.2f} cm "
                f"(user: {user_distance_cm:.2f} cm, margin: {margin*100:.1f}%)"
            )
        
        return poni_data

    def _prompt_distance_cm(self, default_cm: float = None):
        """Prompt user for sample-detector distance in cm. Returns None if canceled."""
        default_val = 17.0 if default_cm is None else float(default_cm)
        dist_cm, ok = QInputDialog.getDouble(
            self,
            "Sample-Detector Distance",
            "Enter sample-detector distance (cm):",
            default_val,
            0.01,
            100000.0,
            3,
        )
        return float(dist_cm) if ok else None

    def _detector_sizes_by_alias_for_validation(self):
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
                sizes[alias] = (int(width), int(height))
            except Exception:
                sizes[alias] = (256, 256)
        return sizes

    def _validate_poni_center_rules_for_data(self, poni_data):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return [], []

        if bool(cfg.get("DEV", False)) and not bool(
            validation_cfg.get("apply_in_dev_mode", False)
        ):
            return [], []

        poni_text_by_alias = {}
        for alias, value in (poni_data or {}).items():
            if isinstance(value, (list, tuple)) and value:
                poni_text_by_alias[str(alias)] = str(value[0] or "")
            else:
                poni_text_by_alias[str(alias)] = str(value or "")

        return validate_poni_centers(
            poni_text_by_alias=poni_text_by_alias,
            detector_sizes_by_alias=self._detector_sizes_by_alias_for_validation(),
            validation_config=validation_cfg,
        )
    
    def generate_technical_meta(self):
        """Generate technical metadata JSON file from selected measurements."""
        from pathlib import Path
        container_schema = get_schema(self.config if hasattr(self, "config") else None)
        message_box = getattr(_module, "QMessageBox", QMessageBox)
        dialog_class = getattr(_module, "QDialog", QDialog)

        self._log_technical_event("Generating technical metadata...")

        # Validate selection
        sel = (
            self.auxTable.selectionModel().selectedRows()
            if self.auxTable.selectionModel()
            else []
        )
        rows = [idx.row() for idx in sel]
        if not rows:
            self._log_technical_event("Error: No rows selected for metadata generation")
            message_box.warning(
                self, "No Selection", "Select one or more rows in the Aux table."
            )
            return

        # Validate name and folder
        name = (self.auxNameLE.text() or "").strip()
        if not name:
            message_box.warning(
                self, "Missing Name", "Enter a name in the Aux Measurement field."
            )
            return
        safe_name = name.replace(" ", "_")
        # Use the explicit folder path for meta generation.
        # (Do not auto-fallback to CWD; if the folder is invalid/unwritable we should stop.)
        folder = self._current_technical_output_folder()
        if not folder or not os.path.isdir(folder):
            message_box.warning(self, "Invalid Folder", "Select a valid save folder.")
            return
        if not os.access(folder, os.W_OK):
            message_box.warning(
                self,
                "Folder Not Writable",
                "Selected save folder is not writable. Choose a different folder.",
            )
            return

        out_path = os.path.join(folder, f"technical_meta_{safe_name}.json")
        if os.path.exists(out_path):
            res = message_box.question(
                self,
                "Overwrite?",
                f"File exists:\n{out_path}\n\nDo you want to overwrite it?",
                message_box.Yes | message_box.No,
                message_box.No,
            )
            if res != message_box.Yes:
                return

        meta = {}
        technical_event_metadata = {}
        seen_pairs = set()  # (type, alias)
        gui_integration_ms = None
        gui_n_frames = None
        try:
            gui_integration_ms = float(self.integrationTimeSpin.value()) * 1000.0
        except Exception:
            gui_integration_ms = None
        try:
            gui_n_frames = int(self.captureFramesSpin.value())
        except Exception:
            gui_n_frames = None

        # Get active detector aliases for validation
        try:
            active_aliases = self._get_active_detector_aliases()
        except Exception:
            active_aliases = []

        for row in rows:
            file_item = self.auxTable.item(row, 1)
            if not file_item:
                continue
            file_path = file_item.data(Qt.UserRole)
            if not file_path or not os.path.exists(file_path):
                message_box.warning(
                    self, "Missing File", f"Row {row+1}: file path does not exist."
                )
                return

            # Type
            type_cb = self.auxTable.cellWidget(row, 2)
            if (
                not self._is_combobox_like(type_cb)
                or type_cb.currentText() == self.NO_SELECTION_LABEL
            ):
                message_box.warning(
                    self, "Missing Type", f"Row {row+1}: select measurement type."
                )
                return
            typ = type_cb.currentText()

            # Alias (must be selected)
            cb = self.auxTable.cellWidget(row, 3)
            if (
                not self._is_combobox_like(cb)
                or cb.currentText() == self.NO_SELECTION_LABEL
            ):
                message_box.warning(
                    self, "Missing Alias", f"Row {row+1}: select an alias."
                )
                return
            al = cb.currentText()

            base = os.path.basename(file_path)
            dst = meta.setdefault(typ, {})
            pair = (typ, al)
            if pair in seen_pairs or al in dst:
                message_box.warning(
                    self,
                    "Duplicate Assignment",
                    f"Measurement for type '{typ}' and alias '{al}' is already assigned.",
                )
                return
            dst[al] = base
            try:
                row_metadata = self._get_aux_row_metadata(
                    row,
                    str(file_path),
                    include_filename_fallback=False,
                )
            except Exception:
                row_metadata = {}
            if not isinstance(row_metadata, dict):
                row_metadata = {}
            if row_metadata.get("integration_time_ms") is None and gui_integration_ms is not None:
                row_metadata["integration_time_ms"] = gui_integration_ms
            if row_metadata.get("n_frames") is None and gui_n_frames is not None:
                row_metadata["n_frames"] = gui_n_frames
            if isinstance(row_metadata, dict) and row_metadata:
                technical_event_metadata.setdefault(typ, {})[al] = row_metadata
            seen_pairs.add(pair)

        # Enforce completeness: all REQUIRED measurement types must be present, and for each alias
        required_types = set(
            getattr(self, "REQUIRED_TYPE_OPTIONS", None)
            or container_schema.REQUIRED_TECHNICAL_TYPES
        )

        # 1) Ensure at least one row selected for each required type
        types_in_meta = {t for t in meta.keys() if t in required_types}
        missing_types = sorted(required_types - types_in_meta)
        if missing_types:
            message_box.warning(
                self,
                "Missing Measurement Types",
                "The following measurement types are missing from your selection:\n\n"
                + ", ".join(missing_types)
                + "\n\nPlease include at least one measurement for each required type before generating the meta file.",
            )
            return

        # 2) Ensure per-alias coverage for each required type
        # Prefer active aliases from config; if unavailable, fall back to aliases seen in selection
        aliases_in_selection = set()
        for type_map in meta.values():
            if isinstance(type_map, dict):
                aliases_in_selection.update(type_map.keys())
        aliases_to_check = active_aliases or sorted(aliases_in_selection)

        missing_pairs = []
        for t in sorted(required_types):
            type_map = meta.get(t, {})
            for a in aliases_to_check:
                if a not in type_map:
                    missing_pairs.append(f"{t} → {a}")

        if missing_pairs:
            message_box.warning(
                self,
                "Incomplete Technical Set",
                "All measurement types must be provided for each detector alias.\n\nMissing combinations:\n"
                + "\n".join(missing_pairs),
            )
            return

        if technical_event_metadata:
            meta["TECHNICAL_EVENT_METADATA"] = technical_event_metadata

        # Get unique aliases from selected measurements for PONI file selection
        unique_aliases = set()
        for row in rows:
            cb = self.auxTable.cellWidget(row, 3)
            if self._is_combobox_like(cb) and cb.currentText() != self.NO_SELECTION_LABEL:
                unique_aliases.add(cb.currentText())

        # Show PONI file selection dialog if we have aliases
        poni_lab = {}
        if unique_aliases:
            # Import here to avoid circular dependency
            from difra.gui.main_window_ext.technical_measurements import PoniFileSelectionDialog
            
            # Get current PONI files if available
            current_poni_files = getattr(self, "poni_files", {})

            poni_dialog = PoniFileSelectionDialog(
                aliases=sorted(unique_aliases),
                current_poni_files=current_poni_files,
                parent=self,
            )

            if poni_dialog.exec_() == dialog_class.Accepted:
                selected_poni_files = poni_dialog.get_poni_files()
                poni_lab_path = {}
                poni_lab_values = {}

                # Process each selected PONI file
                for alias, file_path in selected_poni_files.items():
                    # Store filename for PONI_LAB
                    poni_lab[alias] = os.path.basename(file_path)

                    # Store full path for PONI_LAB_PATH
                    poni_lab_path[alias] = file_path

                    # Read and store PONI file content for PONI_LAB_VALUES
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            poni_content = f.read()
                            poni_lab_values[alias] = poni_content
                    except Exception as e:
                        message_box.warning(
                            self,
                            "PONI File Read Error",
                            f"Failed to read PONI file for {alias}:\n{file_path}\n\nError: {e}\n\nContinuing without this PONI file content.",
                        )
                        # Still include the filename and path, but mark content as unavailable
                        poni_lab_values[alias] = (
                            f"# ERROR: Could not read PONI file content\n# File: {file_path}\n# Error: {str(e)}"
                        )

                # Store additional PONI data for later use
                self._temp_poni_lab_path = poni_lab_path
                self._temp_poni_lab_values = poni_lab_values
            else:
                # User cancelled PONI selection, ask if they want to continue without PONI files
                res = message_box.question(
                    self,
                    "No PONI Files Selected",
                    "Do you want to generate the technical meta file without PONI calibration files?",
                    message_box.Yes | message_box.No,
                    message_box.No,
                )
                if res != message_box.Yes:
                    return

        # Add PONI sections to meta if any PONI files were selected
        if poni_lab:
            meta["PONI_LAB"] = poni_lab

        # Add PONI_LAB_PATH section if available
        if hasattr(self, "_temp_poni_lab_path") and self._temp_poni_lab_path:
            meta["PONI_LAB_PATH"] = self._temp_poni_lab_path

        # Add PONI_LAB_VALUES section if available
        if hasattr(self, "_temp_poni_lab_values") and self._temp_poni_lab_values:
            meta["PONI_LAB_VALUES"] = self._temp_poni_lab_values

        # Add or reuse a calibration group hash so multiple files can be grouped together
        try:
            group_hash = getattr(self, "calibration_group_hash", None)
            if not group_hash:
                group_hash = uuid.uuid4().hex[:16]
                setattr(self, "calibration_group_hash", group_hash)
            meta["CALIBRATION_GROUP_HASH"] = group_hash
        except Exception:
            # Non-fatal; proceed without the group hash if something unexpected happens
            pass

        # Write JSON
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            message_box.critical(
                self, "Write Error", f"Failed to write meta file:\n{e}"
            )
            return
        
        # Clean up temporary PONI data variables
        if hasattr(self, "_temp_poni_lab_path"):
            delattr(self, "_temp_poni_lab_path")
        if hasattr(self, "_temp_poni_lab_values"):
            delattr(self, "_temp_poni_lab_values")

        # Summary
        try:
            summary_lines = []
            for k, v in meta.items():
                if isinstance(v, dict):
                    summary_lines.append(f"{k}: {len(v)} file(s)")
                else:
                    summary_lines.append(f"{k}: {v}")
            summary = "\n".join(summary_lines) or "(empty)"
        except Exception:
            summary = "(summary unavailable)"
        
        self._log_technical_event(
            f"Technical metadata generated: {os.path.basename(out_path)}"
        )
        
        message_box.information(
            self, "Meta Generated", f"Saved to:\n{out_path}\n\nSummary:\n{summary}"
        )
        
        # Now generate HDF5 container
        self.generate_technical_h5()
