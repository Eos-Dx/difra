import os
import time

from difra.gui.container_api import get_container_version


def _pm():
    from difra.gui.main_window_ext.zone_measurements.logic import process_mixin as pm

    return pm


class ZoneMeasurementsProcessCaptureMixin:
    def _append_capture_log(self, message: str):
        try:
            self._append_measurement_log(f"[CAPTURE] {message}")
        except Exception:
            pass

    def _append_session_log(self, message: str):
        try:
            self._append_measurement_log(f"[SESSION] {message}")
        except Exception:
            pass

    def _current_session_point_index(self) -> int:
        try:
            current_index = int(getattr(self, "current_measurement_sorted_index", 0))
        except Exception:
            current_index = 0

        mapped = getattr(self, "_session_point_indices", None)
        if isinstance(mapped, (list, tuple)) and 0 <= current_index < len(mapped):
            try:
                return int(mapped[current_index])
            except Exception:
                pass
        return current_index + 1

    def _move_stage(self, x_mm: float, y_mm: float, timeout_s: float):
        pm = _pm()
        if getattr(self, "hardware_client", None) is not None:
            pm.logger.info(
                "Stage move requested via hardware client",
                target_x_mm=float(x_mm),
                target_y_mm=float(y_mm),
                timeout_s=float(timeout_s),
            )
            self.hardware_client.move_to(x_mm, axis="x", timeout_s=timeout_s)
            result = self.hardware_client.move_to(y_mm, axis="y", timeout_s=timeout_s)
            pm.logger.info(
                "Stage move completed via hardware client",
                final_x_mm=float(result[0]),
                final_y_mm=float(result[1]),
            )
            return result
        if hasattr(self, "stage_controller") and self.stage_controller is not None:
            pm.logger.info(
                "Stage move requested via stage controller",
                target_x_mm=float(x_mm),
                target_y_mm=float(y_mm),
                timeout_s=float(timeout_s),
            )
            result = self.stage_controller.move_stage(x_mm, y_mm, move_timeout=timeout_s)
            pm.logger.info(
                "Stage move completed via stage controller",
                final_x_mm=float(result[0]),
                final_y_mm=float(result[1]),
            )
            return result
        pm.logger.error("Stage move requested without initialized stage")
        raise RuntimeError("Stage not initialized")

    def measure_next_point(self):
        pm = _pm()
        if self.stopped:
            pm.logger.debug("Measurement stopped")
            return
        if self.paused:
            pm.logger.debug("Measurement is paused. Waiting for resume")
            return
        sorted_count = len(getattr(self, "sorted_indices", []) or [])
        if sorted_count != int(getattr(self, "total_points", 0)):
            pm.logger.warning(
                "Measurement point count mismatch; syncing to sorted indices",
                total_points=int(getattr(self, "total_points", 0)),
                sorted_indices_count=int(sorted_count),
            )
            self.total_points = sorted_count
            try:
                self.progressBar.setMaximum(self.total_points)
            except Exception:
                pass

        if self.current_measurement_sorted_index >= sorted_count:
            pm.logger.info("All points measured")
            try:
                self.progressBar.setValue(sorted_count)
            except Exception:
                pass
            self.start_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            if hasattr(self, "skip_btn") and self.skip_btn is not None:
                self.skip_btn.setEnabled(False)
            return

        index = self.sorted_indices[self.current_measurement_sorted_index]
        gp = self.image_view.points_dict["generated"]["points"]
        up = self.image_view.points_dict["user"]["points"]
        if index < len(gp):
            self._point_item = gp[index]
            self._zone_item = self.image_view.points_dict["generated"]["zones"][index]
        else:
            user_index = index - len(gp)
            self._point_item = up[user_index]
            self._zone_item = self.image_view.points_dict["user"]["zones"][user_index]

        self.update_xy_pos()
        center = self._point_item.sceneBoundingRect().center()
        planned_points = (
            getattr(self, "state_measurements", {}).get("measurement_points", [])
            or getattr(self, "state", {}).get("measurement_points", [])
        )
        planned_xy = None
        if 0 <= self.current_measurement_sorted_index < len(planned_points):
            planned = planned_points[self.current_measurement_sorted_index]
            try:
                planned_xy = (float(planned.get("x")), float(planned.get("y")))
            except Exception:
                planned_xy = None

        if planned_xy is not None:
            self._x_mm, self._y_mm = planned_xy
        else:
            self._x_mm = self.real_x_pos_mm.value() - (center.x() - self.include_center[0]) / self.pixel_to_mm_ratio
            self._y_mm = self.real_y_pos_mm.value() - (center.y() - self.include_center[1]) / self.pixel_to_mm_ratio

        point_index_1based = self.current_measurement_sorted_index + 1
        pm.logger.info(
            "Preparing measurement point",
            point_index=point_index_1based,
            total_points=self.total_points,
            target_x_mm=float(self._x_mm),
            target_y_mm=float(self._y_mm),
            integration_time_s=float(getattr(self, "integration_time", 0.0)),
        )
        self._append_capture_log(
            f"Point {point_index_1based}/{self.total_points}: move to ({self._x_mm:.3f}, {self._y_mm:.3f}) mm"
        )

        self._timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._base_name = self.fileNameLineEdit.text().strip()
        txt_filename_base = os.path.join(
            self.measurement_folder,
            f"{self._base_name}_{self._x_mm:.2f}_{self._y_mm:.2f}_{self._timestamp}",
        )

        try:
            self._move_stage(self._x_mm, self._y_mm, timeout_s=15)
            self._append_hw_log(f"Stage positioned: ({self._x_mm:.3f}, {self._y_mm:.3f}) mm")
        except TimeoutError:
            pm.logger.warning(
                "Stage movement timed out before capture",
                point_index=point_index_1based,
                target_x_mm=float(self._x_mm),
                target_y_mm=float(self._y_mm),
            )
            self._append_hw_log("Stage move timeout before capture")
            pm.QMessageBox.warning(
                self,
                "Stage Timeout",
                "Stage movement timed out. Please check the hardware and try again. That's SAD",
            )
            return
        except Exception as e:
            pm.logger.error(
                "Stage movement failed before capture",
                point_index=point_index_1based,
                error=str(e),
            )
            self._append_hw_log(f"Stage move error before capture: {e}")
            pm.QMessageBox.warning(
                self,
                "Stage Error",
                f"Stage movement failed: {str(e)}",
            )
            return

        self._start_normal_capture(txt_filename_base)

    def _start_normal_capture(self, txt_filename_base: str):
        pm = _pm()
        if not self._zone_technical_imports_available():
            pm.logger.error("Cannot start normal capture - technical imports not available")
            try:
                self._append_capture_log("Error: technical imports unavailable")
            except Exception:
                pass
            return

        point_index_1based = self.current_measurement_sorted_index + 1
        session_point_index = self._current_session_point_index()
        self._append_capture_log(
            f"Normal capture start: point {point_index_1based}/{self.total_points}, t={self.integration_time:.3f}s"
        )

        session_manager = getattr(self, "session_manager", None)
        if (
            session_manager is not None
            and hasattr(session_manager, "is_session_active")
            and session_manager.is_session_active()
            and hasattr(session_manager, "begin_point_measurement")
        ):
            try:
                session_manager.begin_point_measurement(
                    point_index=session_point_index,
                    timestamp_start=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
                self._append_session_log(f"Point {session_point_index}: opened in session container")
                if hasattr(session_manager, "log_event"):
                    session_manager.log_event(
                        message="Normal detector capture started",
                        event_type="capture_started",
                        details={
                            "point_index": session_point_index,
                            "x_mm": float(getattr(self, "_x_mm", 0.0)),
                            "y_mm": float(getattr(self, "_y_mm", 0.0)),
                            "integration_time_s": float(getattr(self, "integration_time", 0.0)),
                        },
                    )
            except Exception as exc:
                pm.logger.warning(
                    "Failed to mark measurement start in session container",
                    error=str(exc),
                )
                self._append_session_log(
                    f"Point {session_point_index}: failed to mark session start ({type(exc).__name__})"
                )

        container_version = get_container_version(
            self.config if hasattr(self, "config") else None
        )
        pm.logger.info(
            "Creating normal capture worker",
            point_index=point_index_1based,
            integration_time_s=float(self.integration_time),
            base_file=str(txt_filename_base),
            container_version=str(container_version),
        )
        CaptureWorker = self._get_zone_technical_module("CaptureWorker")
        self.capture_worker = CaptureWorker(
            detector_controller=self.detector_controller,
            integration_time=self.integration_time,
            txt_filename_base=txt_filename_base,
            frames=1,
            naming_mode="normal",
            container_version=container_version,
            hardware_client=getattr(self, "hardware_client", None),
        )
        self.capture_thread = pm.QThread()
        self.capture_worker.moveToThread(self.capture_thread)
        self.capture_thread.started.connect(self.capture_worker.run)
        self.capture_worker.finished.connect(self.on_capture_finished)
        self.capture_worker.finished.connect(self.capture_thread.quit)
        self.capture_worker.finished.connect(self.capture_worker.deleteLater)
        self.capture_thread.finished.connect(self.capture_thread.deleteLater)
        self.capture_thread.start()
        self._append_capture_log("Normal capture worker started")
