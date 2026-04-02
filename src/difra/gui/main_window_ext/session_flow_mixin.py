"""Session lifecycle flow helpers for SessionMixin."""

import time

import h5py
from PyQt5.QtWidgets import QInputDialog

from . import session_mixin as _session_module

Path = _session_module.Path
QMessageBox = _session_module.QMessageBox
get_container_manager = _session_module.get_container_manager
get_schema = _session_module.get_schema
get_writer = _session_module.get_writer
logger = _session_module.logger

from difra.gui.main_window_ext import session_flow_actions
from difra.gui.main_window_ext.session_restore_mixin import SessionRestoreMixin
from difra.gui.session_lifecycle_actions import SessionLifecycleActions
from difra.gui.session_lifecycle_service import SessionLifecycleService


class SessionFlowMixin(SessionRestoreMixin):
    def _handle_session_replacement(self) -> bool:
        """Handle replacement of existing session with error checking."""
        container_manager = get_container_manager(
            self.config if hasattr(self, "config") else None
        )

        if not self.session_manager.is_session_active():
            return True

        info = self.session_manager.get_session_info()
        session_path = Path(info["session_path"])
        sample_id = info["sample_id"]
        session_id = info["session_id"]

        is_locked = container_manager.is_container_locked(session_path)

        has_measurements = False
        try:
            with h5py.File(session_path, "r") as h5_file:
                schema = get_schema(self.config if hasattr(self, "config") else None)
                if schema.GROUP_MEASUREMENTS in h5_file:
                    meas_group = h5_file[schema.GROUP_MEASUREMENTS]
                    has_measurements = any(key.startswith("pt_") for key in meas_group.keys())
        except Exception as exc:
            logger.warning(
                "Failed to inspect session measurements before replacement: %s",
                exc,
                exc_info=True,
            )

        status_lines = [
            f"Sample ID: {sample_id}",
            f"Session ID: {session_id}",
            f"Status: {'Finalized (locked)' if is_locked else 'Unfinalized (unlocked)'}",
            f"Measurements: {'Yes' if has_measurements else 'None recorded'}",
        ]
        if info.get("i0_recorded"):
            status_lines.append("Attenuation: I₀ recorded")
        if info.get("attenuation_complete"):
            status_lines.append("Attenuation: Complete")
        status_str = "\n".join(status_lines)

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

        msg = (
            f"⚠️  Found unfinalized session:\n\n{status_str}\n\n"
            "You are about to load a new sample image.\n"
            "The current session will be archived.\n\n"
        )
        if not has_measurements:
            msg += "⚠️  WARNING: No measurements recorded in this session!\n"
        if not info.get("attenuation_complete"):
            msg += "⚠️  WARNING: Attenuation not complete!\n"
        msg += "\nWas this session created by error?"

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Replace Unfinalized Session?")
        msg_box.setText(msg)
        msg_box.setIcon(QMessageBox.Warning)
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

        created_by_error = clicked_button == mark_error_btn
        _ = continue_btn
        error_reason = ""

        if created_by_error:
            reason, ok = QInputDialog.getText(
                self,
                "Error Reason",
                f"Why was session '{sample_id}' created by error?\n\n"
                "(Optional - provide brief description)",
            )
            if ok and reason.strip():
                error_reason = reason.strip()
            else:
                error_reason = "User marked as error without specifying reason"

            logger.info(
                "Session %s marked as created_by_error: %s", session_id, error_reason
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Session {sample_id} marked as error before archive"
                )

        try:
            if created_by_error:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                try:
                    with h5py.File(session_path, "a") as h5_file:
                        h5_file.attrs["created_by_error"] = True
                        h5_file.attrs["error_reason"] = error_reason
                        h5_file.attrs["archived_timestamp"] = timestamp
                    logger.info(
                        "Added error attributes to session container: %s",
                        session_path.name,
                    )
                except Exception as exc:
                    logger.warning("Failed to add error attributes: %s", exc)

            try:
                lock_user = getattr(self.session_manager, "operator_id", None)
                SessionLifecycleActions.finalize_session_container(
                    session_path=session_path,
                    container_manager=container_manager,
                    lock_user=lock_user,
                )
            except Exception as lock_exc:
                logger.warning(
                    "Failed to lock session before archive replacement: %s", lock_exc
                )

            self.session_manager.close_session()
            self._archive_session_container(
                session_path, session_id, created_by_error, error_reason
            )
            return True
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Archive Failed",
                f"Failed to archive session:\n{exc}",
            )
            logger.error("Failed to archive session: %s", exc, exc_info=True)
            if hasattr(self, "_append_session_log"):
                self._append_session_log(f"Failed to archive session: {type(exc).__name__}")
            return False

    def _archive_session_container(
        self,
        session_path: Path,
        session_id: str,
        created_by_error: bool = False,
        error_reason: str = "",
    ):
        """Archive session container to session_archive folder."""
        try:
            destination = SessionLifecycleService.archive_session_container(
                session_path=session_path,
                session_id=session_id,
                config=self.config if hasattr(self, "config") else None,
            )
            SessionLifecycleService.copy_archive_item_to_mirror(
                destination.parent,
                config=self.config if hasattr(self, "config") else None,
                archive_kind="measurements",
            )
            logger.info(
                "Archived session container: %s -> %s/%s",
                session_path.name,
                destination.parent.name,
                f" [ERROR: {error_reason}]" if created_by_error else "",
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Archived session container: {session_path.name}"
                )
        except Exception as exc:
            logger.error("Failed to move session to archive: %s", exc)
            if hasattr(self, "_append_session_log"):
                self._append_session_log(f"Session archive failed: {type(exc).__name__}")
            raise

    def _handle_new_sample_image(self, image_path: str):
        """Handle loading/capturing a new sample image."""
        return session_flow_actions.handle_new_sample_image(self, image_path)

    def _add_zones_to_session(self):
        """Add zones from state to session container."""
        if (
            not hasattr(self, "session_manager")
            or not self.session_manager.is_session_active()
        ):
            return

        if not hasattr(self, "state") or "shapes" not in self.state:
            logger.warning("No shapes found in state")
            return

        shapes_data = self.state.get("shapes", [])
        if not shapes_data:
            logger.warning("Shapes list is empty in state")
            return

        try:
            zone_exports, _sample_holder_zone_id = self._plan_session_zone_exports(shapes_data)
            zone_index = 1
            for export in zone_exports:
                shape = export["shape"]
                role = shape.get("role", "include")
                shape_type = shape.get("type", "Circle").lower()
                geometry = shape.get("geometry", {})
                geometry_px = [
                    geometry.get("x", 0),
                    geometry.get("y", 0),
                    geometry.get("width", 0),
                    geometry.get("height", 0),
                ]
                zone_role = str(export["zone_role"])

                holder_diameter_mm = None
                if zone_role == "sample_holder":
                    explicit_size_mm = shape.get("physical_size_mm")
                    try:
                        explicit_size_mm = float(explicit_size_mm)
                    except Exception:
                        explicit_size_mm = 0.0
                    if explicit_size_mm > 0:
                        holder_diameter_mm = explicit_size_mm
                    elif hasattr(self, "pixel_to_mm_ratio"):
                        if shape_type == "circle" and len(geometry_px) == 4:
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
                    "Added zone %s to session: role=%s, shape=%s, geometry=%s",
                    zone_index,
                    zone_role,
                    shape_type,
                    geometry_px,
                )
                zone_index += 1

            if zone_index > 1:
                logger.info("Added %s zones to session container", zone_index - 1)
            else:
                logger.warning("No zones were added to session container")
        except Exception as exc:
            logger.error(
                "Failed to add zones to session container: %s",
                exc,
                exc_info=True,
            )

    def _add_mapping_to_session(self):
        """Add image mapping (pixel-to-mm conversion) to session container."""
        if (
            not hasattr(self, "session_manager")
            or not self.session_manager.is_session_active()
        ):
            return

        if not hasattr(self, "pixel_to_mm_ratio"):
            logger.warning("No pixel_to_mm_ratio available for mapping")
            return

        try:
            writer = get_writer(self.config if hasattr(self, "config") else None)
            schema = get_schema(self.config if hasattr(self, "config") else None)
            shapes_data = (getattr(self, "state", {}) or {}).get("shapes", [])
            _zone_exports, sample_holder_zone_id = self._plan_session_zone_exports(shapes_data)
            if not sample_holder_zone_id:
                sample_holder_zone_id = "zone_001"
                logger.warning(
                    "No sample-holder zone available for mapping; falling back to %s",
                    sample_holder_zone_id,
                )

            if hasattr(self, "_build_mapping_conversion_payload"):
                pixel_to_mm_conversion = self._build_mapping_conversion_payload()
            else:
                include_center = getattr(self, "include_center", (0.0, 0.0))
                if not isinstance(include_center, (list, tuple)) or len(include_center) < 2:
                    include_center = (0.0, 0.0)
                ref_x_mm = 0.0
                ref_y_mm = 0.0
                if hasattr(self, "real_x_pos_mm") and hasattr(self.real_x_pos_mm, "value"):
                    try:
                        ref_x_mm = float(self.real_x_pos_mm.value())
                    except Exception:
                        ref_x_mm = 0.0
                if hasattr(self, "real_y_pos_mm") and hasattr(self.real_y_pos_mm, "value"):
                    try:
                        ref_y_mm = float(self.real_y_pos_mm.value())
                    except Exception:
                        ref_y_mm = 0.0
                pixel_to_mm_conversion = {
                    "ratio": float(self.pixel_to_mm_ratio),
                    "units": "mm/pixel",
                    "include_center_px": [float(include_center[0]), float(include_center[1])],
                    "stage_reference_mm": [float(ref_x_mm), float(ref_y_mm)],
                    "formula": "x_mm = ref_x_mm - (x_px - center_x_px) / ratio",
                }

            writer.add_image_mapping(
                file_path=self.session_manager.session_path,
                sample_holder_zone_id=sample_holder_zone_id,
                pixel_to_mm_conversion=pixel_to_mm_conversion,
                orientation="standard",
                mapping_version=schema.SCHEMA_VERSION,
            )
            logger.info(
                "Added image mapping to session container: ratio=%s",
                self.pixel_to_mm_ratio,
            )
        except Exception as exc:
            logger.error(
                "Failed to add mapping to session container: %s",
                exc,
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

        info = self.session_manager.get_session_info()
        msg = (
            f"Finalize session '{info['sample_id']}'?\n\n"
            "This will:\n"
            "  • Close the session container\n"
            "  • Mark it as read-only (locked)\n"
            "  • No more data can be added\n\n"
            f"Container: {Path(info['session_path']).name}\n\n"
            "After finalization, the container can be uploaded to the cloud."
        )

        reply = QMessageBox.question(
            self,
            "Finalize Session?",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            try:
                container_manager = get_container_manager(
                    self.config if hasattr(self, "config") else None
                )
                session_path = self.session_manager.session_path
                lock_user = getattr(self.session_manager, "operator_id", None)

                self.session_manager.close_session()
                SessionLifecycleActions.finalize_session_container(
                    session_path=session_path,
                    container_manager=container_manager,
                    lock_user=lock_user,
                )

                logger.info("Session finalized and locked: %s", session_path.name)
                if hasattr(self, "_append_session_log"):
                    self._append_session_log(
                        f"Session finalized and locked: {session_path.name}"
                    )

                QMessageBox.information(
                    self,
                    "Session Finalized",
                    f"Session finalized successfully!\n\n"
                    f"Container: {session_path.name}\n"
                    f"Location: {session_path.parent}\n\n"
                    "The container is now locked and ready for upload.\n"
                    "No more data can be added to this session.",
                )
                self.update_session_status()
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Finalization Failed",
                    f"Failed to finalize session:\n\n{str(exc)}",
                )
                logger.error("Failed to finalize session: %s", exc, exc_info=True)
                if hasattr(self, "_append_session_log"):
                    self._append_session_log(
                        f"Session finalization failed: {type(exc).__name__}"
                    )
