"""Session lifecycle flow helpers for SessionMixin."""

from . import session_mixin as _session_module

Path = _session_module.Path
QMessageBox = _session_module.QMessageBox
QFileDialog = _session_module.QFileDialog
QDialog = _session_module.QDialog
get_container_manager = _session_module.get_container_manager
get_schema = _session_module.get_schema
get_writer = _session_module.get_writer
logger = _session_module.logger

from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.gui.session_lifecycle_actions import SessionLifecycleActions
from difra.gui.main_window_ext import session_flow_actions


class SessionFlowMixin:
    def _try_reform_active_session_for_new_image(self, image_path: str) -> bool:
        """Reuse active unlocked session by resetting workspace before first measurements."""
        if not hasattr(self, "session_manager") or self.session_manager is None:
            return False
        if not self.session_manager.is_session_active():
            return False
        if self.session_manager.is_locked():
            return False

        has_measurements = False
        has_measurements_fn = getattr(self.session_manager, "has_point_measurements", None)
        if callable(has_measurements_fn):
            try:
                has_measurements = bool(has_measurements_fn())
            except Exception as exc:
                logger.debug(
                    "Failed to check point-measurement presence before image reform: %s",
                    exc,
                    exc_info=True,
                )
                has_measurements = False
        if has_measurements:
            return False

        image_array = None
        if hasattr(self, "_load_image_array_from_path"):
            try:
                image_array = self._load_image_array_from_path(image_path)
            except Exception as exc:
                logger.warning(
                    "Failed to load image for session reform reset: %s",
                    exc,
                    exc_info=True,
                )
                image_array = None

        try:
            self.session_manager.reset_for_image_reform(
                image_data=image_array,
                reset_attenuation=True,
            )
        except Exception as exc:
            logger.warning(
                "Session image reform reset failed; falling back to replacement flow: %s",
                exc,
                exc_info=True,
            )
            return False

        if hasattr(self, "state") and isinstance(self.state, dict):
            self.state["shapes"] = []
            self.state["zone_points"] = []
            self.state["measurement_points"] = []
            self.state["skipped_points"] = []
        if hasattr(self, "state_measurements") and isinstance(self.state_measurements, dict):
            self.state_measurements["measurement_points"] = []
            self.state_measurements["skipped_points"] = []
            self.state_measurements["attenuation_files"] = {}
            self.state_measurements["measurements_meta"] = {}

        if hasattr(self, "_session_sync_cache_session_path"):
            self._session_sync_cache_session_path = None
            self._session_sync_shapes_sig = None
            self._session_sync_mapping_sig = None
            self._session_sync_points_sig = None
            self._session_sync_last_image_sig = None
            self._session_sync_overall_sig = None

        if hasattr(self, "_append_session_log"):
            self._append_session_log(
                "Image reformed in active session: points and attenuation reset"
            )
        QMessageBox.information(
            self,
            "Session Image Reformed",
            "Active session was reused.\n\n"
            "Workspace has been reset for the new image:\n"
            "• zones removed\n"
            "• points removed\n"
            "• attenuation reset\n\n"
            "Define zones and points again before measurements.",
        )
        if hasattr(self, "update_session_status"):
            self.update_session_status()
        return True

    def _resolve_measurements_folder_for_recovery(self, session_path: Path) -> Path:
        """Resolve folder where raw/session measurement files are stored."""
        if hasattr(self, "folderLineEdit"):
            try:
                folder_text = self.folderLineEdit.text().strip()
                if folder_text:
                    return Path(folder_text)
            except Exception as exc:
                logger.debug(
                    "Failed to read measurements folder from folderLineEdit: %s",
                    exc,
                    exc_info=True,
                )

        if hasattr(self, "config") and isinstance(self.config, dict):
            folder = self.config.get("measurements_folder")
            if folder:
                return Path(folder)

        return session_path.parent

    def _sync_attenuation_controls_after_restore(self, session_info: dict) -> None:
        """Enable attenuation checkbox when restored session already contains attenuation data."""
        if not hasattr(self, "attenuationCheckBox"):
            return

        has_prior_attenuation = bool(
            session_info.get("i0_recorded")
            or session_info.get("i_recorded")
            or session_info.get("attenuation_complete")
        )
        if not has_prior_attenuation:
            return

        try:
            self.attenuationCheckBox.setChecked(True)
        except Exception as exc:
            logger.debug(
                "Failed to set attenuationCheckBox during session restore: %s",
                exc,
                exc_info=True,
            )
            return

        if hasattr(self, "_append_session_log"):
            if session_info.get("i0_recorded"):
                self._append_session_log(
                    "Restored attenuation state: I0 already exists in session"
                )
            else:
                self._append_session_log(
                    "Restored attenuation state from existing session"
                )

    def _handle_incomplete_measurements_after_restore(self, session_path: Path):
        """Recover in-progress measurements from on-disk files or mark for re-measurement."""
        session_manager = getattr(self, "session_manager", None)
        if session_manager is None:
            return
        if not hasattr(session_manager, "list_incomplete_measurements"):
            return
        if not session_manager.is_session_active():
            return

        try:
            incomplete = session_manager.list_incomplete_measurements()
        except Exception as exc:
            logger.warning(f"Failed to scan incomplete measurements: {exc}", exc_info=True)
            return

        if not incomplete:
            return

        set_state = getattr(session_manager, "_set_session_state", None)
        recovery_state = getattr(session_manager, "SESSION_STATE_RECOVERY_REQUIRED", "recovery_required")
        if callable(set_state):
            set_state(recovery_state, reason="incomplete_measurements_detected")

        measurements_folder = self._resolve_measurements_folder_for_recovery(session_path)
        integration_time_ms = 0.0
        if hasattr(self, "integrationSpinBox"):
            try:
                integration_time_ms = float(self.integrationSpinBox.value()) * 1000.0
            except Exception as exc:
                logger.debug(
                    "Failed to read integrationSpinBox value for recovery; using 0ms: %s",
                    exc,
                    exc_info=True,
                )
                integration_time_ms = 0.0

        for item in incomplete:
            point_index = item.get("point_index")
            measurement_path = item.get("measurement_path")
            if not measurement_path:
                continue

            scan = session_manager.scan_recovery_files_for_measurement(
                measurement_path=measurement_path,
                measurement_folder=measurements_folder,
            )
            expected_aliases = scan.get("expected_aliases", [])
            files_by_alias = scan.get("files_by_alias", {})
            missing_aliases = scan.get("missing_aliases", [])
            unreadable_aliases = scan.get("unreadable_aliases", [])
            timestamp_start = scan.get("timestamp_start", "")

            if scan.get("is_complete"):
                details = "\n".join(
                    [f"  • {alias}: {Path(path).name}" for alias, path in sorted(files_by_alias.items())]
                )
                choice = QMessageBox.question(
                    self,
                    "Recover Incomplete Point",
                    (
                        f"Point {point_index} was started ({timestamp_start}) but not finalized.\n\n"
                        f"Found complete detector files in:\n{measurements_folder}\n\n"
                        f"{details}\n\n"
                        "Load these files into the session container now?\n"
                        "Press No to mark this point for re-measurement."
                    ),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if choice == QMessageBox.Yes:
                    try:
                        session_manager.finalize_incomplete_measurement_from_files(
                            measurement_path=measurement_path,
                            files_by_alias=files_by_alias,
                            integration_time_ms=integration_time_ms,
                        )
                        logger.info(
                            "Recovered point measurement from files: point=%s path=%s",
                            point_index,
                            measurement_path,
                        )
                        if hasattr(self, "_append_session_log"):
                            self._append_session_log(
                                f"Recovered point {point_index} from existing detector files"
                            )
                    except Exception as exc:
                        logger.warning(
                            "Failed to recover point %s from files, marking for re-measurement: %s",
                            point_index,
                            exc,
                            exc_info=True,
                        )
                        session_manager.abort_incomplete_measurement(
                            measurement_path=measurement_path,
                            reason=f"recovery_load_failed:{type(exc).__name__}",
                        )
                else:
                    session_manager.abort_incomplete_measurement(
                        measurement_path=measurement_path,
                        reason="user_selected_remeasure",
                    )
                    if hasattr(self, "_append_session_log"):
                        self._append_session_log(
                            f"Point {point_index} marked for re-measurement"
                        )
                continue

            expected_text = ", ".join(expected_aliases) if expected_aliases else "not configured"
            missing_text = ", ".join(missing_aliases) if missing_aliases else "none"
            unreadable_text = ", ".join(unreadable_aliases) if unreadable_aliases else "none"
            QMessageBox.warning(
                self,
                "Incomplete Point Requires Re-measurement",
                (
                    f"Point {point_index} was started ({timestamp_start}) but could not be recovered from files.\n\n"
                    f"Measurements folder: {measurements_folder}\n"
                    f"Expected detectors: {expected_text}\n"
                    f"Missing files: {missing_text}\n"
                    f"Unreadable files: {unreadable_text}\n\n"
                    "This point will be marked for re-measurement."
                ),
            )
            session_manager.abort_incomplete_measurement(
                measurement_path=measurement_path,
                reason="recovery_missing_or_unreadable_files",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Point {point_index} marked for re-measurement (missing files)"
                )

    def _handle_session_replacement(self) -> bool:
        """Handle replacement of existing session with error checking.
        
        Returns:
            True if session was closed/archived, False if user cancelled
        """
        from PyQt5.QtWidgets import QInputDialog
        import h5py
        import time
        container_manager = get_container_manager(self.config if hasattr(self, "config") else None)
        
        if not self.session_manager.is_session_active():
            return True
        
        info = self.session_manager.get_session_info()
        session_path = Path(info['session_path'])
        sample_id = info['sample_id']
        session_id = info['session_id']
        
        # Check if container is locked/finalized
        is_locked = container_manager.is_container_locked(session_path)
        
        # Check if measurements exist
        has_measurements = False
        try:
            with h5py.File(session_path, 'r') as f:
                schema = get_schema(self.config if hasattr(self, "config") else None)
                if schema.GROUP_MEASUREMENTS in f:
                    meas_group = f[schema.GROUP_MEASUREMENTS]
                    # Check if any point groups exist
                    has_measurements = any(key.startswith('pt_') for key in meas_group.keys())
        except Exception as exc:
            logger.warning(
                "Failed to inspect session measurements before replacement: %s",
                exc,
                exc_info=True,
            )
        
        # Build status message
        status_lines = [
            f"Sample ID: {sample_id}",
            f"Session ID: {session_id}",
            f"Status: {'Finalized (locked)' if is_locked else 'Unfinalized (unlocked)'}",
            f"Measurements: {'Yes' if has_measurements else 'None recorded'}",
        ]
        
        # Check attenuation status
        if info.get('i0_recorded'):
            status_lines.append(f"Attenuation: I₀ recorded")
        if info.get('attenuation_complete'):
            status_lines.append(f"Attenuation: Complete")
        
        status_str = "\n".join(status_lines)
        
        # Show different dialogs based on status
        if is_locked:
            QMessageBox.information(
                self,
                "Session Already Finalized",
                "Current session is finalized and locked, but it is still the active session.\n\n"
                f"{status_str}\n\n"
                "Send/archive it from the Session queue first, then create a new session.",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Replacement blocked: finalized session {sample_id} must be sent/archived first"
                )
            return False
        
        # Unlocked container - potential error scenario
        msg = (
            f"⚠️  Found unfinalized session:\n\n{status_str}\n\n"
            f"You are about to load a new sample image.\n"
            f"The current session will be archived.\n\n"
        )
        
        # Warn about incomplete data
        if not has_measurements:
            msg += "⚠️  WARNING: No measurements recorded in this session!\n"
        
        if not info.get('attenuation_complete'):
            msg += "⚠️  WARNING: Attenuation not complete!\n"
        
        msg += "\nWas this session created by error?"
        
        # Create custom dialog with three buttons
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Replace Unfinalized Session?")
        msg_box.setText(msg)
        msg_box.setIcon(QMessageBox.Warning)
        
        # Add buttons
        mark_error_btn = msg_box.addButton("Yes - Mark as Error", QMessageBox.YesRole)
        continue_btn = msg_box.addButton("No - Archive Normally", QMessageBox.NoRole)
        cancel_btn = msg_box.addButton("Cancel", QMessageBox.RejectRole)
        msg_box.setDefaultButton(cancel_btn)
        
        msg_box.exec_()
        clicked_button = msg_box.clickedButton()
        
        if clicked_button == cancel_btn:
            logger.info("User cancelled session replacement")
            if hasattr(self, "_append_session_log"):
                self._append_session_log("Session replacement cancelled")
            return False
        
        # Determine error status
        created_by_error = (clicked_button == mark_error_btn)
        error_reason = ""
        
        if created_by_error:
            # Prompt for error reason
            reason, ok = QInputDialog.getText(
                self,
                "Error Reason",
                f"Why was session '{sample_id}' created by error?\n\n"
                f"(Optional - provide brief description)",
            )
            if ok and reason.strip():
                error_reason = reason.strip()
            else:
                error_reason = "User marked as error without specifying reason"
            
            logger.info(
                f"Session {session_id} marked as created_by_error: {error_reason}"
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Session {sample_id} marked as error before archive"
                )
        
        # Close session and archive with metadata
        try:
            # Add error attributes before closing if needed
            if created_by_error:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                try:
                    with h5py.File(session_path, 'a') as f:
                        f.attrs['created_by_error'] = True
                        f.attrs['error_reason'] = error_reason
                        f.attrs['archived_timestamp'] = timestamp
                    logger.info(f"Added error attributes to session container: {session_path.name}")
                except Exception as e:
                    logger.warning(f"Failed to add error attributes: {e}")

            try:
                container_manager = get_container_manager(
                    self.config if hasattr(self, "config") else None
                )
                lock_user = getattr(self.session_manager, "operator_id", None)
                SessionLifecycleActions.finalize_session_container(
                    session_path=session_path,
                    container_manager=container_manager,
                    lock_user=lock_user,
                )
            except Exception as lock_exc:
                logger.warning(
                    f"Failed to lock session before archive replacement: {lock_exc}"
                )
            
            # Close session (this will keep the file in place)
            self.session_manager.close_session()
            
            # Archive the container to session_archive folder
            self._archive_session_container(session_path, session_id, created_by_error, error_reason)
            
            return True
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Archive Failed",
                f"Failed to archive session:\n{e}",
            )
            logger.error(f"Failed to archive session: {e}", exc_info=True)
            if hasattr(self, "_append_session_log"):
                self._append_session_log(f"Failed to archive session: {type(e).__name__}")
            return False
    
    def _archive_session_container(self, session_path: Path, session_id: str, 
                                   created_by_error: bool = False, error_reason: str = ""):
        """Archive session container to session_archive folder.
        
        Args:
            session_path: Path to session container
            session_id: Session container ID
            created_by_error: Whether marked as error
            error_reason: Optional error reason
        """
        try:
            destination = SessionLifecycleService.archive_session_container(
                session_path=session_path,
                session_id=session_id,
                config=self.config if hasattr(self, "config") else None,
            )
            logger.info(
                f"Archived session container: {session_path.name} -> {destination.parent.name}/"
                + (f" [ERROR: {error_reason}]" if created_by_error else "")
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Archived session container: {session_path.name}"
                )
        except Exception as e:
            logger.error(f"Failed to move session to archive: {e}")
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Session archive failed: {type(e).__name__}"
                )
            raise
    
    def _handle_new_sample_image(self, image_path: str):
        """Handle loading/capturing a new sample image - auto-creates session."""
        return session_flow_actions.handle_new_sample_image(self, image_path)
    
    def _add_zones_to_session(self):
        """Add zones from state to session container.
        
        Called when measurements start to store zone definitions from state.
        """
        if not hasattr(self, 'session_manager') or not self.session_manager.is_session_active():
            return
        
        # Get shapes from state instead of image_view
        if not hasattr(self, 'state') or 'shapes' not in self.state:
            logger.warning("No shapes found in state")
            return
        
        shapes_data = self.state.get('shapes', [])
        if not shapes_data:
            logger.warning("Shapes list is empty in state")
            return
        
        try:
            zone_index = 1
            for shape in shapes_data:
                # Get shape data from state structure
                role = shape.get('role', 'include')
                shape_type = shape.get('type', 'Circle').lower()
                geometry = shape.get('geometry', {})
                
                # Convert geometry dict to list format [x, y, width, height]
                geometry_px = [
                    geometry.get('x', 0),
                    geometry.get('y', 0),
                    geometry.get('width', 0),
                    geometry.get('height', 0),
                ]
                
                # Map role to zone_role
                zone_role_map = {
                    'include': 'sample_holder',
                    'sample holder': 'sample_holder',
                    'exclude': 'exclude',
                }
                zone_role = zone_role_map.get(role.lower(), 'sample_holder')
                
                # Get holder diameter if it's a sample_holder
                holder_diameter_mm = None
                if zone_role == 'sample_holder' and hasattr(self, 'pixel_to_mm_ratio'):
                    # Calculate diameter from geometry
                    if shape_type == 'circle' and len(geometry_px) == 4:
                        diameter_px = max(geometry_px[2], geometry_px[3])
                        holder_diameter_mm = diameter_px / self.pixel_to_mm_ratio
                
                self.session_manager.add_zone(
                    zone_index=zone_index,
                    geometry_px=geometry_px,
                    shape=shape_type,
                    zone_role=zone_role,
                    holder_diameter_mm=holder_diameter_mm,
                )
                
                logger.info(
                    f"Added zone {zone_index} to session: role={zone_role}, shape={shape_type}, geometry={geometry_px}"
                )
                zone_index += 1
            
            if zone_index > 1:
                logger.info(f"Added {zone_index - 1} zones to session container")
            else:
                logger.warning("No zones were added to session container")
        
        except Exception as e:
            logger.error(
                f"Failed to add zones to session container: {e}",
                exc_info=True,
            )
    
    def _add_mapping_to_session(self):
        """Add image mapping (pixel-to-mm conversion) to session container.
        
        Called when measurements start to store the coordinate transformation.
        """
        if not hasattr(self, 'session_manager') or not self.session_manager.is_session_active():
            return
        
        if not hasattr(self, 'pixel_to_mm_ratio'):
            logger.warning("No pixel_to_mm_ratio available for mapping")
            return
        
        try:
            writer = get_writer(self.config if hasattr(self, "config") else None)
            schema = get_schema(self.config if hasattr(self, "config") else None)
            
            # Find sample_holder zone ID (first zone with sample_holder role)
            sample_holder_zone_id = "zone_001"  # Default to first zone
            
            include_center = getattr(self, "include_center", (0.0, 0.0))
            if not isinstance(include_center, (list, tuple)) or len(include_center) < 2:
                include_center = (0.0, 0.0)
            ref_x_mm = 0.0
            ref_y_mm = 0.0
            if hasattr(self, "real_x_pos_mm") and hasattr(self.real_x_pos_mm, "value"):
                try:
                    ref_x_mm = float(self.real_x_pos_mm.value())
                except Exception as exc:
                    logger.debug(
                        "Failed to read real_x_pos_mm for mapping payload: %s",
                        exc,
                        exc_info=True,
                    )
                    ref_x_mm = 0.0
            if hasattr(self, "real_y_pos_mm") and hasattr(self.real_y_pos_mm, "value"):
                try:
                    ref_y_mm = float(self.real_y_pos_mm.value())
                except Exception as exc:
                    logger.debug(
                        "Failed to read real_y_pos_mm for mapping payload: %s",
                        exc,
                        exc_info=True,
                    )
                    ref_y_mm = 0.0

            # Create pixel-to-mm conversion dict
            pixel_to_mm_conversion = {
                "ratio": float(self.pixel_to_mm_ratio),
                "units": "mm/pixel",
                "include_center_px": [float(include_center[0]), float(include_center[1])],
                "stage_reference_mm": [float(ref_x_mm), float(ref_y_mm)],
                "formula": "x_mm = ref_x_mm - (x_px - center_x_px) / ratio",
            }
            
            # Add orientation if available
            orientation = "standard"
            
            # Call writer to add mapping with overwrite=True
            writer.add_image_mapping(
                file_path=self.session_manager.session_path,
                sample_holder_zone_id=sample_holder_zone_id,
                pixel_to_mm_conversion=pixel_to_mm_conversion,
                orientation=orientation,
                mapping_version=schema.SCHEMA_VERSION,
            )
            
            logger.info(
                f"Added image mapping to session container: ratio={self.pixel_to_mm_ratio}"
            )
            
        except Exception as e:
            logger.error(
                f"Failed to add mapping to session container: {e}",
                exc_info=True,
            )
    
    def on_finalize_session(self):
        """Finalize session - close, lock, and prepare for upload."""
        if not self.session_manager.is_session_active():
            QMessageBox.information(
                self,
                "No Active Session",
                "No session is currently active.",
            )
            return
        if hasattr(self, "_append_session_log"):
            self._append_session_log("Finalizing active session container")
        
        # Get session info
        info = self.session_manager.get_session_info()
        
        # Confirm finalization
        msg = (
            f"Finalize session '{info['sample_id']}'?\n\n"
            f"This will:\n"
            f"  • Close the session container\n"
            f"  • Mark it as read-only (locked)\n"
            f"  • No more data can be added\n\n"
            f"Container: {Path(info['session_path']).name}\n\n"
            f"After finalization, the container can be uploaded to the cloud."
        )
        
        reply = QMessageBox.question(
            self,
            "Finalize Session?",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                container_manager = get_container_manager(self.config if hasattr(self, "config") else None)
                
                session_path = self.session_manager.session_path
                lock_user = getattr(self.session_manager, "operator_id", None)
                
                # Close session
                self.session_manager.close_session()
                
                # Lock the container (mark read-only)
                SessionLifecycleActions.finalize_session_container(
                    session_path=session_path,
                    container_manager=container_manager,
                    lock_user=lock_user,
                )
                
                logger.info(
                    f"Session finalized and locked: {session_path.name}"
                )
                if hasattr(self, "_append_session_log"):
                    self._append_session_log(
                        f"Session finalized and locked: {session_path.name}"
                    )
                
                # Show success message with container location
                QMessageBox.information(
                    self,
                    "Session Finalized",
                    f"Session finalized successfully!\n\n"
                    f"Container: {session_path.name}\n"
                    f"Location: {session_path.parent}\n\n"
                    f"The container is now locked and ready for upload.\n"
                    f"No more data can be added to this session.",
                )
                
                # Update UI
                self.update_session_status()
                
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Finalization Failed",
                    f"Failed to finalize session:\n\n{str(e)}",
                )
                logger.error(f"Failed to finalize session: {e}", exc_info=True)
                if hasattr(self, "_append_session_log"):
                    self._append_session_log(
                        f"Session finalization failed: {type(e).__name__}"
                    )
    
    def _prepare_for_session_container_switch(self, target_path: Path) -> bool:
        """Ensure active session can be replaced according to lock policy."""
        if not hasattr(self, "session_manager") or self.session_manager is None:
            return True
        if not self.session_manager.is_session_active():
            return True

        current_path = Path(self.session_manager.session_path)
        try:
            if current_path.resolve() == Path(target_path).resolve():
                return True
        except Exception as exc:
            logger.debug(
                "Failed to resolve session path comparison (%s vs %s): %s",
                current_path,
                target_path,
                exc,
                exc_info=True,
            )

        container_manager = get_container_manager(self.config if hasattr(self, "config") else None)
        current_locked = bool(container_manager.is_container_locked(current_path))

        if current_locked:
            self.session_manager.close_session()
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Closed active locked session before loading {Path(target_path).name}"
                )
            return True

        info = {}
        try:
            info = self.session_manager.get_session_info() or {}
        except Exception as exc:
            logger.debug(
                "Failed to read active session info before container switch: %s",
                exc,
                exc_info=True,
            )
            info = {}

        sample_id = info.get("sample_id") or self.session_manager.sample_id or "UNKNOWN"
        reply = QMessageBox.question(
            self,
            "Active Session In Progress",
            "Active session container is still under construction (unlocked):\n\n"
            f"Sample ID: {sample_id}\n"
            f"Container: {current_path.name}\n\n"
            "To load another session container, the active one must be locked first.\n\n"
            "Lock current session and continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return False

        try:
            lock_user = getattr(self.session_manager, "operator_id", None)
            SessionLifecycleActions.finalize_session_container(
                session_path=current_path,
                container_manager=container_manager,
                lock_user=lock_user,
            )
            self.session_manager.close_session()
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Locked and closed active session before loading {Path(target_path).name}"
                )
            return True
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Lock Failed",
                f"Failed to lock active session before switching containers:\n\n{exc}",
            )
            logger.error("Failed to lock active session before load: %s", exc, exc_info=True)
            return False

    def load_session_container_from_path(self, file_path: Path) -> bool:
        """Load a session container from an explicit path."""
        return session_flow_actions.load_session_container_from_path(self, file_path)

    def on_restore_session(self):
        """Open an existing session container (including locked ones) for analysis."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Session Container",
            str(Path.home()),
            "NeXus HDF5 Files (*.nxs.h5 *.h5);;All Files (*)",
        )
        if not file_path:
            return
        self.load_session_container_from_path(Path(file_path))
