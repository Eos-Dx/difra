"""Technical H5 validation/locking responsibilities."""

from . import h5_management_mixin as _module

os = _module.os
logger = _module.logger
QInputDialog = _module.QInputDialog
QMessageBox = _module.QMessageBox
QFileDialog = _module.QFileDialog
Path = _module.Path
shutil = _module.shutil
time = _module.time
get_container_manager = _module.get_container_manager
get_technical_validator = _module.get_technical_validator
get_schema = _module.get_schema

from difra.gui.main_window_ext.technical import h5_management_lock_actions


class H5ManagementLockingMixin:
    @staticmethod
    def _sync_lock_action_overrides():
        """Mirror monkeypatchable module globals into extracted helper actions."""
        h5_management_lock_actions.QMessageBox = QMessageBox
        h5_management_lock_actions.QInputDialog = QInputDialog
        h5_management_lock_actions.get_container_manager = get_container_manager

    @staticmethod
    def _build_fake_poni_content(
        alias: str,
        distance_cm: float = 17.0,
        detector_size=(256, 256),
        pixel_size_um=(55.0, 55.0),
    ) -> str:
        """Create deterministic fake PONI content for demo mode."""
        try:
            width = int(detector_size[0])
            height = int(detector_size[1])
        except Exception:
            width, height = 256, 256

        try:
            pixel1_um = float(pixel_size_um[0])
        except Exception:
            pixel1_um = 55.0
        try:
            pixel2_um = float(pixel_size_um[1])
        except Exception:
            pixel2_um = pixel1_um

        distance_m = max(float(distance_cm), 0.0) / 100.0
        pixel1 = pixel1_um * 1e-6
        pixel2 = pixel2_um * 1e-6

        # Stable pseudo-variation per detector alias.
        alias_seed = abs(hash(str(alias))) % 1000
        poni1 = 0.006 + (alias_seed % 150) / 100000.0
        poni2 = 0.002 + (alias_seed % 120) / 100000.0

        return (
            "# Auto-generated fake PONI (DEMO mode)\n"
            "poni_version: 2.1\n"
            "Detector: Detector\n"
            f'Detector_config: {{"pixel1": {pixel1}, "pixel2": {pixel2}, '
            f'"max_shape": [{height}, {width}], "orientation": 3}}\n'
            f"Distance: {distance_m:.6f}\n"
            f"Poni1: {poni1:.6f}\n"
            f"Poni2: {poni2:.6f}\n"
            "Rot1: 0\n"
            "Rot2: 0\n"
            "Rot3: 0\n"
            "Wavelength: 1.5406e-10\n"
            f"# Detector alias: {alias}\n"
        )

    def _auto_provision_demo_poni_files(self, aliases) -> bool:
        """Create/load fake PONI files for aliases in DEV mode."""
        if not bool(self.config.get("DEV", False)):
            return False

        if not isinstance(getattr(self, "ponis", None), dict):
            self.ponis = {}
        if not isinstance(getattr(self, "poni_files", None), dict):
            self.poni_files = {}

        demo_dir = Path(__file__).resolve().parents[3] / "resources" / "demo_poni_files"
        demo_dir.mkdir(parents=True, exist_ok=True)

        detector_cfg_by_alias = {}
        for detector_cfg in self.config.get("detectors", []):
            alias = str(detector_cfg.get("alias") or "").strip()
            if alias:
                detector_cfg_by_alias[alias] = detector_cfg

        distances_by_alias = {}
        if hasattr(self, "_distance_map_by_alias"):
            try:
                distances_by_alias = dict(self._distance_map_by_alias() or {})
            except Exception:
                distances_by_alias = {}

        added = 0
        for alias in sorted(set(aliases)):
            alias = str(alias).strip()
            if not alias:
                continue

            existing_content = str((self.ponis or {}).get(alias) or "").strip()
            existing_meta = (self.poni_files or {}).get(alias, {})
            existing_path = ""
            if isinstance(existing_meta, dict):
                existing_path = str(existing_meta.get("path") or "").strip()

            if existing_content and existing_path and os.path.exists(existing_path):
                continue

            demo_path = demo_dir / f"{alias.lower()}_demo.poni"
            if demo_path.exists():
                try:
                    content = demo_path.read_text(encoding="utf-8")
                    self.ponis[alias] = content
                    self.poni_files[alias] = {
                        "path": str(demo_path),
                        "name": demo_path.name,
                    }
                    added += 1
                    continue
                except Exception:
                    pass

            detector_cfg = detector_cfg_by_alias.get(alias, {})
            size_cfg = detector_cfg.get("size", {}) if isinstance(detector_cfg, dict) else {}
            if isinstance(size_cfg, dict):
                detector_size = (
                    int(size_cfg.get("width", 256)),
                    int(size_cfg.get("height", 256)),
                )
            else:
                detector_size = (256, 256)

            pixel_cfg = detector_cfg.get("pixel_size_um", [55.0, 55.0]) if isinstance(detector_cfg, dict) else [55.0, 55.0]
            if isinstance(pixel_cfg, (int, float)):
                pixel_size_um = (float(pixel_cfg), float(pixel_cfg))
            elif isinstance(pixel_cfg, (list, tuple)):
                if len(pixel_cfg) >= 2:
                    pixel_size_um = (float(pixel_cfg[0]), float(pixel_cfg[1]))
                elif len(pixel_cfg) == 1:
                    pixel_size_um = (float(pixel_cfg[0]), float(pixel_cfg[0]))
                else:
                    pixel_size_um = (55.0, 55.0)
            else:
                pixel_size_um = (55.0, 55.0)

            distance_cm = float(distances_by_alias.get(alias, 17.0))
            content = self._build_fake_poni_content(
                alias=alias,
                distance_cm=distance_cm,
                detector_size=detector_size,
                pixel_size_um=pixel_size_um,
            )
            try:
                demo_path.write_text(content, encoding="utf-8")
                self.ponis[alias] = content
                self.poni_files[alias] = {
                    "path": str(demo_path),
                    "name": demo_path.name,
                }
                added += 1
            except Exception as exc:
                logger.warning(
                    "Failed to auto-generate demo PONI file for alias=%s error=%s",
                    alias,
                    str(exc),
                )

        if added > 0:
            self._log_technical_event(
                f"Auto-provisioned {added} fake PONI file(s) for demo mode"
            )
        return added > 0

    def _container_has_poni_datasets(self, container_path: Path) -> bool:
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        try:
            with h5py.File(container_path, "r") as h5f:
                poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
                if poni_group is None:
                    return False
                return any(str(name).startswith("poni_") for name in poni_group.keys())
        except Exception:
            return False

    def _collect_lock_detector_aliases(self, container_path: Path):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        aliases = []

        if hasattr(self, "_get_active_detector_aliases"):
            try:
                aliases.extend([a for a in self._get_active_detector_aliases() if a])
            except Exception:
                pass

        if aliases:
            return sorted({str(a) for a in aliases if str(a).strip()})

        try:
            with h5py.File(container_path, "r") as h5f:
                tech_group = h5f.get(schema.GROUP_TECHNICAL)
                if tech_group is None:
                    return []
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
                        if alias:
                            aliases.append(alias)
        except Exception:
            return []

        return sorted({str(a) for a in aliases if str(a).strip()})

    def _prompt_poni_selection_for_lock(self, aliases) -> bool:
        from difra.gui.main_window_ext.technical_measurements import (
            PoniFileSelectionDialog,
        )

        if not isinstance(getattr(self, "ponis", None), dict):
            self.ponis = {}
        if not isinstance(getattr(self, "poni_files", None), dict):
            self.poni_files = {}

        dialog = PoniFileSelectionDialog(
            aliases=sorted(set(aliases)),
            current_poni_files=getattr(self, "poni_files", {}) or {},
            parent=self,
        )
        accepted_value = getattr(type(dialog), "Accepted", 1)
        if dialog.exec_() != accepted_value:
            self._log_technical_event("Lock cancelled: PONI selection dialog cancelled")
            return False

        selected_poni_files = dialog.get_poni_files() or {}
        missing = []
        read_errors = []

        for alias in sorted(set(aliases)):
            candidate_path = str(selected_poni_files.get(alias) or "").strip()
            if not candidate_path:
                current_info = self.poni_files.get(alias)
                if isinstance(current_info, dict):
                    candidate_path = str(current_info.get("path") or "").strip()

            if not candidate_path or not os.path.exists(candidate_path):
                missing.append(alias)
                continue

            try:
                with open(candidate_path, "r", encoding="utf-8") as file_handle:
                    self.ponis[alias] = file_handle.read()
                self.poni_files[alias] = {
                    "path": candidate_path,
                    "name": Path(candidate_path).name,
                }
            except Exception as exc:
                read_errors.append(f"{alias}: {exc}")

        if missing:
            QMessageBox.warning(
                self,
                "Missing PONI Files",
                "PONI files are required before locking.\n\nMissing for: "
                + ", ".join(missing),
            )
            self._log_technical_event(
                f"Lock blocked: missing PONI for aliases {missing}"
            )
            return False

        if read_errors:
            QMessageBox.warning(
                self,
                "PONI Read Error",
                "Failed to read one or more PONI files:\n\n"
                + "\n".join(read_errors),
            )
            self._log_technical_event(
                f"Lock blocked: failed to read PONI files ({len(read_errors)})"
            )
            return False

        return True

    def _ensure_poni_before_lock(self, container_path: Path, container_id: str) -> bool:
        if self._container_has_poni_datasets(container_path):
            return True

        aliases = self._collect_lock_detector_aliases(container_path)
        if not aliases:
            QMessageBox.warning(
                self,
                "Missing PONI",
                "Container has no PONI datasets and detector aliases could not be determined.\n\n"
                "Load/select a valid technical container with detector aliases and try again.",
            )
            self._log_technical_event(
                f"Lock blocked: missing PONI and detector aliases for {container_id}"
            )
            return False

        if bool(self.config.get("DEV", False)):
            if self._auto_provision_demo_poni_files(aliases):
                if hasattr(self, "_sync_active_technical_container_from_table"):
                    synced = self._sync_active_technical_container_from_table(show_errors=True)
                    if not synced:
                        QMessageBox.warning(
                            self,
                            "PONI Sync Failed",
                            "Demo PONI files were generated but container sync failed.\n\n"
                            "Fix technical rows and try locking again.",
                        )
                        self._log_technical_event(
                            f"Lock blocked: failed to sync demo PONI into {container_id}"
                        )
                        return False
                if self._container_has_poni_datasets(container_path):
                    QMessageBox.information(
                        self,
                        "Demo PONI Added",
                        "DEMO mode detected. Fake PONI files were added automatically.\n\n"
                        "Validation will now continue before lock.",
                    )
                    return True

        reply = QMessageBox.question(
            self,
            "PONI Required",
            "Active technical container has no embedded PONI datasets.\n\n"
            "Select PONI files now? They will be added, then validation will run before lock.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._log_technical_event("Lock cancelled: user declined PONI selection")
            return False

        if not self._prompt_poni_selection_for_lock(aliases):
            return False

        if hasattr(self, "_sync_active_technical_container_from_table"):
            synced = self._sync_active_technical_container_from_table(show_errors=True)
            if not synced:
                QMessageBox.warning(
                    self,
                    "PONI Sync Failed",
                    "PONI files were selected but container sync failed.\n\n"
                    "Fix technical rows and try locking again.",
                )
                self._log_technical_event(
                    f"Lock blocked: failed to sync selected PONI into {container_id}"
                )
                return False

        if not self._container_has_poni_datasets(container_path):
            QMessageBox.warning(
                self,
                "PONI Missing",
                "PONI datasets are still missing after selection.\n\n"
                "Please verify detector aliases and selected PONI files.",
            )
            self._log_technical_event(
                f"Lock blocked: PONI still missing after selection for {container_id}"
            )
            return False

        self._log_technical_event(
            f"PONI datasets added before lock for {container_id}"
        )
        return True

    def _validate_container_before_lock(self, container_path: Path, container_id: str) -> bool:
        """Validate technical container before allowing lock."""
        import h5py

        technical_validator = get_technical_validator(
            self.config if hasattr(self, "config") else None
        )
        validate_technical_container = technical_validator.validate_technical_container

        try:
            is_valid, errors, warnings = validate_technical_container(
                str(container_path), strict=False
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Validation Error",
                f"Failed to validate technical container before lock:\n{exc}",
            )
            self._log_technical_event(f"Lock validation error: {exc}")
            return False

        expected_version = self.config.get(
            "expected_technical_schema_version",
            self.config.get("container_version", "0.2"),
        )
        actual_version = "unknown"
        try:
            with h5py.File(container_path, "r") as h5f:
                raw_version = h5f.attrs.get("schema_version", "unknown")
                if isinstance(raw_version, bytes):
                    raw_version = raw_version.decode("utf-8", errors="replace")
                actual_version = str(raw_version)
        except Exception as exc:
            errors.append(f"Failed to read schema version: {exc}")
            is_valid = False

        if actual_version != str(expected_version):
            errors.append(
                f"Schema version mismatch: container has {actual_version}, expected {expected_version}"
            )
            is_valid = False

        if not is_valid:
            details = []
            for i, err in enumerate(errors[:8], 1):
                details.append(f"{i}. {err}")
            if len(errors) > 8:
                details.append(f"... and {len(errors) - 8} more")

            QMessageBox.critical(
                self,
                "Validation Failed",
                "Technical container cannot be locked because validation failed.\n\n"
                f"Container ID: {container_id}\n"
                f"File: {container_path.name}\n\n"
                + "\n".join(details),
            )
            self._log_technical_event(
                f"Lock blocked by validation errors for {container_id}: {len(errors)} error(s)"
            )
            return False

        if warnings:
            self._log_technical_event(
                f"Validation warnings before lock for {container_id}: {len(warnings)} warning(s)"
            )

        return True

    def _validate_and_prompt_lock(self, container_path: str, container_id: str):
        """Validate container and prompt user to lock it.
        
        Args:
            container_path: Path to generated container
            container_id: Container ID
        """
        import h5py
        technical_validator = get_technical_validator(
            self.config if hasattr(self, "config") else None
        )
        validate_technical_container = technical_validator.validate_technical_container
        
        # Validate container
        try:
            is_valid, errors, warnings = validate_technical_container(container_path, strict=False)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Validation Error",
                f"Failed to validate container:\n{e}"
            )
            self._log_technical_event(f"Validation error: {e}")
            return
        
        # Check schema version
        expected_version = self.config.get(
            "expected_technical_schema_version",
            self.config.get("container_version", "0.2"),
        )
        try:
            with h5py.File(container_path, 'r') as f:
                actual_version = f.attrs.get("schema_version", "unknown")
                if isinstance(actual_version, bytes):
                    actual_version = actual_version.decode('utf-8')
                
                if actual_version != expected_version:
                    errors.append(
                        f"Schema version mismatch: container has {actual_version}, expected {expected_version}"
                    )
                    is_valid = False
        except Exception as e:
            errors.append(f"Failed to check schema version: {e}")
            is_valid = False
        
        # Build validation summary
        status_icon = "✅" if is_valid else ("⚠️" if errors else "✅")
        summary_lines = [
            f"{status_icon} Container Validation Results",
            "",
            f"Container ID: {container_id}",
            f"Location: {os.path.basename(container_path)}",
            f"Schema Version: {actual_version}",
            "",
        ]
        
        if errors:
            summary_lines.append(f"❌ {len(errors)} Error(s):")
            for i, error in enumerate(errors[:5], 1):
                summary_lines.append(f"  {i}. {error}")
            if len(errors) > 5:
                summary_lines.append(f"  ... and {len(errors) - 5} more")
            summary_lines.append("")
        
        if warnings:
            summary_lines.append(f"⚠️  {len(warnings)} Warning(s):")
            for i, warning in enumerate(warnings[:3], 1):
                summary_lines.append(f"  {i}. {warning}")
            if len(warnings) > 3:
                summary_lines.append(f"  ... and {len(warnings) - 3} more")
            summary_lines.append("")
        
        if not errors and not warnings:
            summary_lines.append("✅ No issues found")
            summary_lines.append("")
        
        # Show validation results
        if is_valid:
            summary_lines.append("Container is valid and ready to lock.")
            summary_lines.append("\nLock this container for session measurements?")
            
            reply = QMessageBox.question(
                self,
                "Validation Passed",
                "\n".join(summary_lines),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            
            if reply == QMessageBox.Yes:
                # Lock the container
                self._lock_container(container_path, container_id)
            else:
                QMessageBox.information(
                    self,
                    "Container Saved",
                    f"Container saved without locking.\n\nLocation: {container_path}",
                )
        else:
            summary_lines.append("Container has validation errors.")
            summary_lines.append("\nYou can still use this container, but it may not work correctly.")
            summary_lines.append("\nSave anyway?")
            
            reply = QMessageBox.warning(
                self,
                "Validation Failed",
                "\n".join(summary_lines),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            
            if reply == QMessageBox.Yes:
                self._log_technical_event(f"User saved container {container_id} despite validation errors")
                QMessageBox.information(
                    self,
                    "Container Saved",
                    f"Container saved with errors.\n\nLocation: {container_path}",
                )
    
    def _archive_existing_containers(self, storage_folder: str) -> int:
        """Archive any existing .h5 containers in storage folder before creating new one."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.archive_existing_containers(
            self, storage_folder
        )

    def _update_aux_table_paths_after_archive(self, archive_folder: Path) -> int:
        """Remap aux table file paths to archived locations for visualization."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.update_aux_table_paths_after_archive(
            self, archive_folder
        )

    def create_new_technical_container(self):
        """Legacy API kept for compatibility; uses container-first creation flow."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.create_new_technical_container(self)

    def lock_active_technical_container(self):
        """Lock currently active technical container."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.lock_active_technical_container(self)
    
    def _lock_container(self, container_path: str, container_id: str):
        """Lock the technical container and archive raw data."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.lock_container(
            self, container_path, container_id
        )
    
