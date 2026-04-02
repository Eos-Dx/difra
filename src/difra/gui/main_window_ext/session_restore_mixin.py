"""Session restore and recovery helpers for SessionMixin."""

from PyQt5.QtWidgets import QFileDialog, QMessageBox

from . import session_mixin as _session_module

Path = _session_module.Path
get_container_manager = _session_module.get_container_manager
logger = _session_module.logger

from difra.gui.session_lifecycle_actions import SessionLifecycleActions
from difra.utils.container_validation import validate_container


class SessionRestoreMixin:
    @staticmethod
    def _read_session_specimen_id(file_path: Path) -> str:
        try:
            import h5py

            with h5py.File(file_path, "r") as h5f:
                specimen = h5f.attrs.get("specimenId", h5f.attrs.get("sample_id"))
                if specimen is None:
                    return ""
                if isinstance(specimen, bytes):
                    return specimen.decode("utf-8", errors="replace").strip()
                return str(specimen).strip()
        except Exception:
            logger.debug(
                "Failed to read specimenId from session container: %s",
                file_path,
                exc_info=True,
            )
            return ""

    def import_workspace_from_session_path(self, file_path: Path) -> bool:
        """Import sample image/zones/points from another session container."""
        file_path = Path(file_path)
        if not file_path.exists():
            QMessageBox.critical(
                self,
                "File Not Found",
                f"Container file not found:\n{file_path}",
            )
            return False

        session_manager = getattr(self, "session_manager", None)
        if session_manager is None or not session_manager.is_session_active():
            QMessageBox.warning(
                self,
                "Import Blocked",
                "Create and keep an active session container open before importing a previous workspace.",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    "Workspace import blocked: no active session container"
                )
            return False

        if session_manager.is_locked():
            QMessageBox.warning(
                self,
                "Import Blocked",
                "The active session container is locked.\n\n"
                "Workspace import is only allowed immediately after session creation, before measurements.",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    "Workspace import blocked: active session is locked"
                )
            return False

        active_session_path = getattr(session_manager, "session_path", None)
        active_specimen = ""
        if active_session_path:
            active_specimen = self._read_session_specimen_id(Path(active_session_path))
        if not active_specimen:
            active_specimen = str(getattr(session_manager, "sample_id", "") or "").strip()
        source_specimen = self._read_session_specimen_id(file_path)

        container_manager = get_container_manager(
            self.config if hasattr(self, "config") else None
        )
        if not bool(container_manager.is_container_locked(file_path)):
            QMessageBox.warning(
                self,
                "Import Blocked",
                "The selected session container is not locked.\n\n"
                "Only finalized, locked session containers can be reused as workspace sources.",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    "Workspace import blocked: selected session is not locked"
                )
            return False

        try:
            report = validate_container(file_path, container_kind="session")
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Import Blocked",
                "The selected session container could not be validated.\n\n"
                f"{exc}",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Workspace import blocked: validation failed ({type(exc).__name__})"
                )
            return False
        if not report.is_valid:
            QMessageBox.warning(
                self,
                "Import Blocked",
                "The selected session container contains validation errors and cannot be used as a workspace source.",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    "Workspace import blocked: selected session is invalid"
                )
            return False

        if active_specimen and source_specimen and source_specimen != active_specimen:
            QMessageBox.warning(
                self,
                "Import Blocked",
                "The selected session container belongs to a different specimen ID.\n\n"
                f"Active session specimen ID: {active_specimen}\n"
                f"Selected container specimen ID: {source_specimen}",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    "Workspace import blocked: selected session has different specimenId"
                )
            return False

        if (
            session_manager is not None
            and session_manager.is_session_active()
            and hasattr(session_manager, "has_point_measurements")
            and session_manager.has_point_measurements()
        ):
            QMessageBox.warning(
                self,
                "Import Blocked",
                "The active session already contains point measurements.\n\n"
                "Import the workspace into a fresh session before measuring so the "
                "saved points stay aligned with the container.",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    "Workspace import blocked: active session already has measurements"
                )
            return False

        self._restore_session_workspace_from_container(
            file_path,
            restore_measurement_history=False,
            lock_shapes_if_measured=False,
        )

        synced_to_active_session = False
        if (
            session_manager is not None
            and session_manager.is_session_active()
            and not session_manager.is_locked()
        ):
            sync_workspace = getattr(self, "sync_workspace_to_session_container", None)
            if callable(sync_workspace):
                sync_workspace(state=getattr(self, "state", None) or {})
                synced_to_active_session = True

        if hasattr(self, "update_session_status"):
            self.update_session_status()

        shapes = len((getattr(self, "state", {}) or {}).get("shapes", []) or [])
        points = len((getattr(self, "state", {}) or {}).get("zone_points", []) or [])
        if hasattr(self, "_append_session_log"):
            suffix = " and synced to active session" if synced_to_active_session else ""
            self._append_session_log(
                f"Imported workspace from {file_path.name}: shapes={shapes}, points={points}{suffix}"
            )

        QMessageBox.information(
            self,
            "Workspace Imported",
            "Image, zones, and points were loaded from the selected session container.\n\n"
            + (
                "The imported workspace was also synced into the active session container."
                if synced_to_active_session
                else "The imported workspace is available in the current GUI."
            ),
        )
        return True

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
        recovery_state = getattr(
            session_manager, "SESSION_STATE_RECOVERY_REQUIRED", "recovery_required"
        )
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
            missing_raw_by_alias = scan.get("missing_raw_by_alias", {}) or {}
            timestamp_start = scan.get("timestamp_start", "")

            if scan.get("is_complete"):
                details = "\n".join(
                    [
                        f"  • {alias}: {Path(path).name}"
                        for alias, path in sorted(files_by_alias.items())
                    ]
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
            missing_raw_text = "none"
            if isinstance(missing_raw_by_alias, dict) and missing_raw_by_alias:
                chunks = []
                for alias, raw_keys in sorted(missing_raw_by_alias.items()):
                    keys = ", ".join(str(item) for item in (raw_keys or [])) or "none"
                    chunks.append(f"{alias}: {keys}")
                missing_raw_text = "; ".join(chunks) if chunks else "none"
            QMessageBox.warning(
                self,
                "Incomplete Point Requires Re-measurement",
                (
                    f"Point {point_index} was started ({timestamp_start}) but could not be recovered from files.\n\n"
                    f"Measurements folder: {measurements_folder}\n"
                    f"Expected detectors: {expected_text}\n"
                    f"Missing files: {missing_text}\n"
                    f"Unreadable files: {unreadable_text}\n\n"
                    f"Missing raw blobs: {missing_raw_text}\n\n"
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

        container_manager = get_container_manager(
            self.config if hasattr(self, "config") else None
        )
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
        from difra.gui.main_window_ext import session_flow_actions

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

    def on_import_workspace_from_session(self):
        """Import image/zones/points from a session container into current workspace."""
        from difra.gui.main_window_ext import session_flow_actions

        session_flow_actions._import_workspace_from_previous_session(self)
