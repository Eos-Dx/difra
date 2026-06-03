"""Technical H5 locking responsibilities: H5LockingLockWorkflowMixin."""

from difra.gui.main_window_ext.technical.h5_locking_common import *


class H5LockingLockWorkflowMixin:
    def _collect_lock_detector_aliases(self, container_path: Path):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        aliases = []

        if hasattr(self, "_get_active_detector_aliases"):
            try:
                aliases.extend([a for a in self._get_active_detector_aliases() if a])
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "Suppressed exception in h5_management_locking_mixin.py",
                    exc_info=True,
                )

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

    def _has_ready_poni_file_selection(self, aliases) -> bool:
        if not isinstance(getattr(self, "poni_files", None), dict):
            return False

        for alias in sorted({str(alias).strip() for alias in (aliases or []) if str(alias).strip()}):
            info = self.poni_files.get(alias)
            if not isinstance(info, dict):
                return False
            candidate_path = str(info.get("path") or "").strip()
            if not candidate_path or not os.path.exists(candidate_path):
                return False
        return True

    def _launch_poni_update_for_container(self, container_path: Path) -> bool:
        """Run Update PONI flow for a specific container path."""
        update_fn = getattr(self, "update_active_technical_container_poni", None)
        if not callable(update_fn):
            return False

        previous_path = str(getattr(self, "_active_technical_container_path", "") or "")
        previous_locked = bool(getattr(self, "_active_technical_container_locked", False))
        switched = False
        try:
            same_path = False
            if previous_path:
                try:
                    same_path = Path(previous_path).resolve() == Path(container_path).resolve()
                except Exception:
                    same_path = str(previous_path) == str(container_path)
            if not same_path:
                self._active_technical_container_path = str(container_path)
                self._active_technical_container_locked = False
                switched = True
            return bool(update_fn())
        finally:
            if switched:
                self._active_technical_container_path = previous_path
                self._active_technical_container_locked = previous_locked

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
                    synced = self._sync_active_technical_container_from_table(show_errors=False)
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

        if self._has_ready_poni_file_selection(aliases):
            if hasattr(self, "_sync_active_technical_container_from_table"):
                synced = self._sync_active_technical_container_from_table(show_errors=False)
                if not synced:
                    QMessageBox.warning(
                        self,
                        "PONI Sync Failed",
                        "Prepared PONI files were found, but syncing them into the container failed.\n\n"
                        "Fix technical rows and try locking again.",
                    )
                    self._log_technical_event(
                        f"Lock blocked: failed to sync preselected PONI into {container_id}"
                    )
                    return False

            if self._container_has_poni_datasets(container_path):
                self._log_technical_event(
                    f"PONI datasets auto-applied before lock for {container_id}"
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
            synced = self._sync_active_technical_container_from_table(show_errors=False)
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

        center_errors, center_warnings = self._validate_poni_centers_for_container(
            Path(container_path)
        )
        center_error_count = len(center_errors)
        if center_errors:
            errors.extend(center_errors)
            is_valid = False
        if center_warnings:
            warnings.extend(center_warnings)

        distance_errors = self._embedded_poni_distance_validation_errors(
            Path(container_path)
        )
        if distance_errors:
            errors.extend(distance_errors)
            is_valid = False

        metadata_errors = self._embedded_poni_metadata_validation_errors(
            Path(container_path)
        )
        if metadata_errors:
            errors.extend(metadata_errors)
            is_valid = False

        agbh_peak_warnings = self._embedded_agbh_peak_qc_warnings(Path(container_path))
        if agbh_peak_warnings:
            warnings.extend(agbh_peak_warnings)
            self._log_technical_event(
                f"AgBH peak QC warnings before lock for {container_id}: {len(agbh_peak_warnings)} warning(s)"
            )

        if not is_valid:
            details = []
            for i, err in enumerate(errors[:8], 1):
                details.append(f"{i}. {err}")
            if len(errors) > 8:
                details.append(f"... and {len(errors) - 8} more")

            remediation_lines = []
            has_distance_error = any(
                ("distance" in str(err).lower()) and ("poni" in str(err).lower())
                for err in errors
            )
            if has_distance_error:
                remediation_lines.append(
                    "Distance check failed: update PONI file distance values or detector distance settings."
                )
            has_metadata_error = any(
                ("pixel size" in str(err).lower())
                or ("energy" in str(err).lower())
                or ("detector shape" in str(err).lower())
                for err in errors
            )
            if has_metadata_error:
                remediation_lines.append(
                    "PONI metadata check failed: use Xena PONI files with 8.04 keV, "
                    "55 um pixels, and 256 x 256 detector shape."
                )
            if center_error_count > 0:
                remediation_lines.append(
                    "Center position check failed: update the PONI center values or update "
                    f"poni_center_validation in {self._poni_validation_config_label()}."
                )

            message = (
                "Technical container cannot be locked because validation failed.\n\n"
                f"Container ID: {container_id}\n"
                f"File: {container_path.name}\n\n"
                + "\n".join(details)
            )
            if remediation_lines:
                message += "\n\nRecommended action:\n" + "\n".join(
                    f"- {line}" for line in remediation_lines
                )

            QMessageBox.critical(
                self,
                "Validation Failed",
                message,
            )
            self._set_container_state(
                Path(container_path),
                state=self.STATE_VALIDATION_FAILED,
                reason="lock_validation_failed",
            )
            self._log_technical_event(
                f"Lock blocked by validation errors for {container_id}: {len(errors)} error(s)"
            )
            return False

        if warnings:
            self._log_technical_event(
                f"Validation warnings before lock for {container_id}: {len(warnings)} warning(s)"
            )
            if agbh_peak_warnings and bool(self._agbh_peak_qc_config().get("show_dialog", True)):
                details = "\n".join(f"- {msg}" for msg in agbh_peak_warnings[:6])
                if len(agbh_peak_warnings) > 6:
                    details += f"\n- ... and {len(agbh_peak_warnings) - 6} more"
                QMessageBox.warning(
                    self,
                    "AgBH Peak QC Warning",
                    "AgBH peak positions do not fully match theoretical lines.\n\n"
                    + details
                    + "\n\nThis is a warning only; lock can continue.",
                )

        return True

    def _confirm_poni_center_preview_before_lock(self, container_path: Path, container_id: str) -> bool:
        """Require accepted user review for PONI center preview before lock."""
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return True

        review_state = self._read_poni_review_state(Path(container_path))
        if (
            review_state.get("status") == "accepted"
            and bool(review_state.get("in_zone", False))
        ):
            self._set_container_state(
                Path(container_path),
                state=self.STATE_READY_TO_LOCK,
                reason="poni_review_confirmed",
            )
            return True

        self._log_technical_event(
            f"PONI center review must be re-confirmed before lock for {container_id}"
        )
        self._set_container_state(
            Path(container_path),
            state=self.STATE_PENDING_PONI_REVIEW,
            reason="poni_review_reconfirmation_required",
        )
        return bool(
            self._run_poni_center_review_workflow(
                Path(container_path),
                container_id=container_id,
                prompt_reload_on_reject=True,
            )
        )

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

        center_errors, center_warnings = self._validate_poni_centers_for_container(
            Path(container_path)
        )
        if center_errors:
            errors.extend(center_errors)
            is_valid = False
        if center_warnings:
            warnings.extend(center_warnings)

        distance_errors = self._embedded_poni_distance_validation_errors(
            Path(container_path)
        )
        if distance_errors:
            errors.extend(distance_errors)
            is_valid = False

        metadata_errors = self._embedded_poni_metadata_validation_errors(
            Path(container_path)
        )
        if metadata_errors:
            errors.extend(metadata_errors)
            is_valid = False

        agbh_peak_warnings = self._embedded_agbh_peak_qc_warnings(Path(container_path))
        if agbh_peak_warnings:
            warnings.extend(agbh_peak_warnings)
            self._log_technical_event(
                f"AgBH peak QC warnings before lock for {container_id}: {len(agbh_peak_warnings)} warning(s)"
            )
        
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
                if self._confirm_poni_center_preview_before_lock(
                    Path(container_path), container_id
                ):
                    # Lock the container only after operator preview confirmation.
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
    
