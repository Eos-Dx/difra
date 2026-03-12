import json
import uuid
from pathlib import Path

from difra.gui.main_window_ext.zone_measurements.logic import process_start_actions


def _pm():
    from difra.gui.main_window_ext.zone_measurements.logic import process_mixin as pm

    return pm


_DEFAULT_PM = _pm


class ZoneMeasurementsProcessStartMixin:
    def _append_capture_log(self, message: str):
        payload = f"[CAPTURE] {message}"
        try:
            self._append_measurement_log(payload)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in process_start_mixin.py",
                exc_info=True,
            )
        try:
            append_runtime = getattr(
                self,
                "_append_runtime_log_to_active_technical_container",
                None,
            )
            if callable(append_runtime):
                append_runtime(payload, channel="CAPTURE", source="process_start")
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in process_start_mixin.py",
                exc_info=True,
            )

    def _append_session_log(self, message: str):
        payload = f"[SESSION] {message}"
        try:
            self._append_measurement_log(payload)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in process_start_mixin.py",
                exc_info=True,
            )
        try:
            append_runtime = getattr(
                self,
                "_append_runtime_log_to_active_technical_container",
                None,
            )
            if callable(append_runtime):
                append_runtime(payload, channel="SESSION", source="process_start")
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in process_start_mixin.py",
                exc_info=True,
            )

    @staticmethod
    def _as_text(value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _json_default(value):
        """Convert numpy/path-like objects to JSON-serializable values."""
        try:
            import numpy as np

            if isinstance(value, np.generic):
                return value.item()
            if isinstance(value, np.ndarray):
                return value.tolist()
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in process_start_mixin.py",
                exc_info=True,
            )

        if isinstance(value, Path):
            return str(value)
        if isinstance(value, set):
            return list(value)
        return str(value)

    @staticmethod
    def _new_measurement_point_uid(counter: int) -> str:
        """Return point UID as '<integer_counter>_<8 hex symbols>'."""
        try:
            counter_int = int(counter)
        except Exception:
            counter_int = 0
        return f"{counter_int}_{uuid.uuid4().hex[:8]}"

    def _point_item_uid(self, point_item, counter: int) -> str:
        """Read or assign stable point UID on graphics item (data key 2)."""
        try:
            existing = point_item.data(2)
            if isinstance(existing, bytes):
                existing = existing.decode("utf-8", errors="replace")
            if isinstance(existing, str) and existing.strip():
                return existing.strip()
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in process_start_mixin.py",
                exc_info=True,
            )

        uid = self._new_measurement_point_uid(counter)
        try:
            point_item.setData(2, uid)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in process_start_mixin.py",
                exc_info=True,
            )
        return uid

    @staticmethod
    def _point_distance_sq(p1, p2) -> float:
        if p1 is None or p2 is None:
            return float("inf")
        try:
            dx = float(p1[0]) - float(p2[0])
            dy = float(p1[1]) - float(p2[1])
            return dx * dx + dy * dy
        except Exception:
            return float("inf")

    def _load_active_session_points_metadata(self):
        """Return ordered point metadata from the active session container."""
        session_manager = getattr(self, "session_manager", None)
        if session_manager is None:
            return []
        if not hasattr(session_manager, "is_session_active") or not session_manager.is_session_active():
            return []

        session_path = getattr(session_manager, "session_path", None)
        schema = getattr(session_manager, "schema", None)
        if not session_path or schema is None:
            return []

        try:
            import h5py

            session_points = []
            with h5py.File(session_path, "r") as h5f:
                points_group = h5f.get(schema.GROUP_POINTS)
                if points_group is None:
                    return []

                for point_id in sorted(points_group.keys()):
                    point_name = str(point_id)
                    if not point_name.startswith("pt_"):
                        continue
                    try:
                        point_index = int(point_name.split("_")[-1])
                    except Exception:
                        continue
                    point_group = points_group[point_id]
                    status = self._as_text(
                        point_group.attrs.get(schema.ATTR_POINT_STATUS, "")
                    ).strip().lower()
                    point_uid = self._as_text(
                        point_group.attrs.get("point_uid", "")
                    ).strip()
                    physical = point_group.attrs.get(
                        getattr(schema, "ATTR_PHYSICAL_COORDINATES_MM", "physical_coordinates_mm"),
                        None,
                    )
                    physical_xy = None
                    try:
                        if physical is not None and len(physical) >= 2:
                            physical_xy = (float(physical[0]), float(physical[1]))
                    except Exception:
                        physical_xy = None
                    session_points.append(
                        {
                            "point_index": int(point_index),
                            "status": status,
                            "point_uid": point_uid,
                            "physical_xy": physical_xy,
                        }
                    )
            return session_points
        except Exception:
            return []

    def _session_has_i0_measurement(self) -> bool:
        """Return True when active session already has an I0 attenuation measurement."""
        session_manager = getattr(self, "session_manager", None)
        if session_manager is None:
            return False

        try:
            if (
                not hasattr(session_manager, "is_session_active")
                or not session_manager.is_session_active()
            ):
                return False
        except Exception:
            return False

        try:
            return getattr(session_manager, "i0_counter", None) is not None
        except Exception:
            return False

    def _dump_state_measurements(self):
        """Write state_measurements JSON with numpy-safe serialization."""
        if not hasattr(self, "state_path_measurements") or not self.state_path_measurements:
            return
        with open(self.state_path_measurements, "w") as f:
            json.dump(
                self.state_measurements,
                f,
                indent=4,
                default=self._json_default,
            )

    def _set_measurement_controls_idle(self):
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        if hasattr(self, "skip_btn") and self.skip_btn is not None:
            self.skip_btn.setEnabled(False)
        self.paused = False
        self.stopped = False

    def _choose_resume_or_remeasure(self, measured_count: int, pending_count: int) -> str:
        pm = _pm()
        reply = pm.QMessageBox.question(
            self,
            "Restored Session Detected",
            (
                "Loaded session already contains measurements.\n\n"
                f"Measured points: {measured_count}\n"
                f"Pending points: {pending_count}\n\n"
                "Yes: continue pending points only.\n"
                "No: re-measure all points (existing measurements will be kept)."
            ),
            pm.QMessageBox.Yes | pm.QMessageBox.No,
            pm.QMessageBox.Yes,
        )
        if reply == pm.QMessageBox.Yes:
            return "resume"
        return "remeasure"

    def _confirm_remeasure_completed_session(self, total_points: int) -> bool:
        pm = _pm()
        reply = pm.QMessageBox.question(
            self,
            "Session Already Complete",
            (
                "All points in the restored session are already measured.\n\n"
                f"Total measured points: {total_points}\n\n"
                "Re-measure all points?\n"
                "(Existing measurements will be kept.)"
            ),
            pm.QMessageBox.Yes | pm.QMessageBox.No,
            pm.QMessageBox.No,
        )
        return reply == pm.QMessageBox.Yes

    def _resolve_session_point_plan(self, measurement_points):
        return process_start_actions.resolve_session_point_plan(
            self,
            measurement_points,
        )

    def _existing_session_point_count(self) -> int:
        session_manager = getattr(self, "session_manager", None)
        if session_manager is None:
            return 0
        if not hasattr(session_manager, "is_session_active") or not session_manager.is_session_active():
            return 0

        session_path = getattr(session_manager, "session_path", None)
        schema = getattr(session_manager, "schema", None)
        if not session_path or schema is None:
            return 0

        try:
            import h5py

            with h5py.File(session_path, "r") as h5f:
                points_group = h5f.get(schema.GROUP_POINTS)
                if points_group is None:
                    return 0
                return int(
                    len([name for name in points_group.keys() if str(name).startswith("pt_")])
                )
        except Exception:
            return 0

    def _ensure_writable_session_for_measurement(self) -> bool:
        pm = _pm()

        session_manager = getattr(self, "session_manager", None)
        if session_manager is None:
            return True
        if not hasattr(session_manager, "is_session_active"):
            return True
        if not session_manager.is_session_active():
            return True
        if not hasattr(session_manager, "is_locked"):
            return True
        if not session_manager.is_locked():
            return True

        info = {}
        try:
            info = session_manager.get_session_info() or {}
        except Exception:
            info = {}

        sample_id = info.get("sample_id") or "UNKNOWN"
        session_id = info.get("session_id") or "UNKNOWN"

        try:
            session_manager.close_session()
        except Exception as exc:
            pm.logger.warning(
                "Failed to close locked session before auto new-session flow",
                error=str(exc),
            )

        if hasattr(self, "update_session_status"):
            try:
                self.update_session_status()
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "Suppressed exception in process_start_mixin.py",
                    exc_info=True,
                )

        pm.QMessageBox.information(
            self,
            "Session Locked",
            "The active session container is locked and cannot accept new measurements.\n\n"
            f"Closed locked session:\nSample ID: {sample_id}\nSession ID: {session_id}\n\n"
            "A new session is required. Session creation dialog will open now.",
        )

        image_path = ""
        try:
            image_path = getattr(getattr(self, "image_view", None), "current_image_path", "") or ""
        except Exception:
            image_path = ""

        if hasattr(self, "_handle_new_sample_image"):
            self._handle_new_sample_image(image_path)
        else:
            pm.QMessageBox.warning(
                self,
                "Session Required",
                "Please create a new session before starting measurements.",
            )
            return False

        if not session_manager.is_session_active() or session_manager.is_locked():
            pm.logger.warning(
                "Measurement start cancelled: writable session was not created after locked-session rollover"
            )
            return False

        return True

    def start_measurements(self):
        return process_start_actions.start_measurements(self)
