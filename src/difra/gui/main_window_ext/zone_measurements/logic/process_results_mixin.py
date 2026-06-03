import logging
import time
from typing import Optional, Tuple

from PyQt5.QtCore import Qt

from difra.gui.main_window_ext.zone_measurements.logic.process_result_files import (
    build_session_measurement_result_refs,
    find_nearby_dsc,
    measurement_ref_to_filename,
)
from difra.gui.main_window_ext.zone_measurements.logic.process_capture_finished_mixin import (
    ZoneMeasurementsCaptureFinishedMixin,
)
from difra.gui.main_window_ext.zone_measurements.logic.process_profile_previews_mixin import (
    ZoneMeasurementsProfilePreviewMixin,
)


def _pm():
    from difra.gui.main_window_ext.zone_measurements.logic import process_mixin as pm

    return pm


logger = logging.getLogger(__name__)


_find_nearby_dsc = find_nearby_dsc


class ZoneMeasurementsProcessResultsMixin(
    ZoneMeasurementsCaptureFinishedMixin,
    ZoneMeasurementsProfilePreviewMixin,
):
    @staticmethod
    def _measurement_ref_to_filename(measurement_ref) -> str:
        return measurement_ref_to_filename(measurement_ref)

    def _append_capture_log(self, message: str):
        payload = f"[CAPTURE] {message}"
        try:
            self._append_measurement_log(payload)
        except (AttributeError, RuntimeError, TypeError):
            logger.debug(
                "Suppressed exception in process_results_mixin.py",
                exc_info=True,
            )
        try:
            append_runtime = getattr(
                self,
                "_append_runtime_log_to_active_technical_container",
                None,
            )
            if callable(append_runtime):
                append_runtime(payload, channel="CAPTURE", source="process_results")
        except (AttributeError, RuntimeError, TypeError):
            logger.debug(
                "Suppressed exception in process_results_mixin.py",
                exc_info=True,
            )

    def _append_session_log(self, message: str):
        payload = f"[SESSION] {message}"
        try:
            self._append_measurement_log(payload)
        except (AttributeError, RuntimeError, TypeError):
            logger.debug(
                "Suppressed exception in process_results_mixin.py",
                exc_info=True,
            )
        try:
            append_runtime = getattr(
                self,
                "_append_runtime_log_to_active_technical_container",
                None,
            )
            if callable(append_runtime):
                append_runtime(payload, channel="SESSION", source="process_results")
        except (AttributeError, RuntimeError, TypeError):
            logger.debug(
                "Suppressed exception in process_results_mixin.py",
                exc_info=True,
            )

    def _build_session_measurement_result_refs(
        self,
        *,
        session_manager,
        measurement_path: Optional[str],
        result_files: dict,
        detector_lookup: dict,
    ) -> dict:
        return build_session_measurement_result_refs(
            session_manager=session_manager,
            measurement_path=measurement_path,
            result_files=result_files,
            detector_lookup=detector_lookup,
        )

    def spawn_measurement_thread(self, row, file_map):
        pm = _pm()
        if not self._zone_technical_imports_available():
            pm.logger.error("Cannot spawn measurement thread - technical imports not available")
            return

        thread = pm.QThread(self)
        MeasurementWorker = self._get_zone_technical_module("MeasurementWorker")
        worker = MeasurementWorker(
            row=row,
            filenames=file_map,
            masks=self.masks,
            ponis=self.ponis,
            parent=self,
            hf_cutoff_fraction=0.2,
            columns_to_remove=30,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.measurement_ready.connect(self.add_measurement_to_table)
        worker.measurement_ready.connect(thread.quit)
        worker.measurement_ready.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        if not hasattr(self, "_measurement_threads"):
            self._measurement_threads = []
        self._measurement_threads.append((thread, worker))
        thread.start()

    def measurement_finished(self):
        pm = _pm()
        pm.logger.info(
            f">>> measurement_finished called (point {self.current_measurement_sorted_index + 1}/{self.total_points})"
        )

        if self.stopped:
            pm.logger.debug("Measurement stopped in measurement_finished")
            return

        pm.logger.info("Advancing to next point...")
        self.current_measurement_sorted_index += 1
        self.progressBar.setValue(self.current_measurement_sorted_index)
        pm.logger.info(f"Progress: {self.current_measurement_sorted_index}/{self.total_points}")
        elapsed = time.time() - self.measurementStartTime
        if self.current_measurement_sorted_index > 0:
            avg_time = elapsed / self.current_measurement_sorted_index
            remaining = avg_time * (self.total_points - self.current_measurement_sorted_index)
            percent_complete = (self.current_measurement_sorted_index / self.total_points) * 100
            self.timeRemainingLabel.setText(f"{percent_complete:.0f}% done, {remaining:.0f} sec remaining")

        if self.current_measurement_sorted_index < self.total_points and not self.paused and not self.stopped:
            pm.logger.info(
                f"Moving to next point ({self.current_measurement_sorted_index + 1}/{self.total_points})"
            )
            self.measure_next_point()
        else:
            if self.current_measurement_sorted_index >= self.total_points:
                pm.logger.info("=== ALL MEASUREMENT POINTS COMPLETED ===")
                self._append_capture_log("Measurement sequence complete")
                self.pause_btn.setEnabled(False)
                self.stop_btn.setEnabled(False)
                self.start_btn.setEnabled(True)
                if hasattr(self, "skip_btn") and self.skip_btn is not None:
                    self.skip_btn.setEnabled(False)
            else:
                pm.logger.warning(f"Measurement stopped: paused={self.paused}, stopped={self.stopped}")

        pm.logger.info("<<< measurement_finished complete")

    def add_measurement_to_table(self, row, results, timestamp=None):
        pm = _pm()
        point_uid, point_display_id = self._get_point_identity_from_table_row(row)
        if not point_uid:
            pm.logger.warning("Could not determine point_uid for measurement", row=row)
            return
        point_label = (
            f"#{point_display_id}" if point_display_id is not None else str(point_uid)
        )

        x_mm = None
        y_mm = None
        try:
            x_item = self.pointsTable.item(row, 3)
            y_item = self.pointsTable.item(row, 4)
            if x_item is not None and y_item is not None:
                x_mm = float(x_item.text()) if x_item.text() not in (None, "", "N/A") else None
                y_mm = float(y_item.text()) if y_item.text() not in (None, "", "N/A") else None
        except (AttributeError, TypeError, ValueError):
            logger.debug(
                "Suppressed exception in process_results_mixin.py",
                exc_info=True,
            )

        add_to_panel = getattr(self, "add_measurement_widget_to_panel", None)
        if callable(add_to_panel):
            try:
                add_to_panel(point_uid, point_display_id=point_display_id)
            except TypeError:
                add_to_panel(point_uid)

        widget = self._get_or_create_measurement_widget(
            point_uid=point_uid,
            point_display_id=point_display_id,
        )
        if widget is None:
            pm.logger.error(
                "Could not get/create measurement widget",
                point_uid=point_uid,
                point_display_id=point_display_id,
            )
            return

        try:
            if x_mm is not None and y_mm is not None:
                if hasattr(widget, "set_mm_coordinates"):
                    widget.set_mm_coordinates(x_mm, y_mm)
                else:
                    widget.setWindowTitle(
                        f"Measurement History: Point {point_label} {x_mm:.2f}:{y_mm:.2f} mm"
                    )
            else:
                widget.setWindowTitle(f"Measurement History: Point {point_label}")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Suppressed exception in process_results_mixin.py",
                exc_info=True,
            )

        try:
            items_map = getattr(self, "_measurement_items", {})
            if point_uid in items_map:
                top_item, _child_item, _w = items_map.get(
                    point_uid, (None, None, None)
                )
                if top_item is not None:
                    if x_mm is not None and y_mm is not None:
                        top_item.setText(
                            0, f"Point {point_label} {x_mm:.2f}:{y_mm:.2f} mm"
                        )
                    else:
                        top_item.setText(0, f"Point {point_label}")
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Suppressed exception in process_results_mixin.py",
                exc_info=True,
            )

        widget.add_measurement(results, timestamp or getattr(self, "_timestamp", ""))
        pm.logger.debug(
            "Added measurement to widget",
            point_uid=point_uid,
            point_display_id=point_display_id,
            row=row,
        )

    def _get_point_identity_from_table_row(
        self,
        row: int,
    ) -> Tuple[Optional[str], Optional[int]]:
        getter = getattr(self, "_get_point_identity_from_row", None)
        if callable(getter):
            try:
                return getter(row)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug(
                    "Suppressed exception in process_results_mixin.py",
                    exc_info=True,
                )

        point_uid: Optional[str] = None
        point_id = None
        item0 = self.pointsTable.item(row, 0)
        if item0 is not None:
            try:
                uid_data = item0.data(Qt.UserRole + 1)
                if uid_data is not None:
                    uid_txt = str(uid_data).strip()
                    if uid_txt:
                        point_uid = uid_txt
            except (AttributeError, TypeError, ValueError):
                point_uid = None
            try:
                role_data = item0.data(Qt.UserRole)
                if role_data is not None:
                    point_id = int(role_data)
            except (AttributeError, TypeError, ValueError):
                point_id = None
            txt = item0.text().strip()
            if txt and point_id is None:
                try:
                    point_id = int(txt)
                except ValueError:
                    logger.debug(
                        "Suppressed exception in process_results_mixin.py",
                        exc_info=True,
                    )

        point_item = None
        if point_id is None or not point_uid:
            gp = self.image_view.points_dict["generated"]["points"]
            up = self.image_view.points_dict["user"]["points"]

            if row < len(gp):
                point_item = gp[row]
            else:
                urow = row - len(gp)
                if 0 <= urow < len(up):
                    point_item = up[urow]

            if point_item is not None:
                if point_id is None:
                    pid = point_item.data(1)
                    point_id = int(pid) if pid is not None else None
                if not point_uid:
                    uid_data = point_item.data(2)
                    if uid_data is not None:
                        uid_txt = str(uid_data).strip()
                        if uid_txt:
                            point_uid = uid_txt

        if point_id is None and point_uid:
            try:
                point_id = int(str(point_uid).split("_", 1)[0])
            except (TypeError, ValueError):
                point_id = None

        if not point_uid and point_id is not None:
            if hasattr(self, "_new_measurement_point_uid"):
                point_uid = self._new_measurement_point_uid(point_id)
            else:
                point_uid = f"{int(point_id)}_00000000"
            if point_item is not None:
                try:
                    point_item.setData(2, point_uid)
                except (AttributeError, RuntimeError, TypeError):
                    logger.debug(
                        "Suppressed exception in process_results_mixin.py",
                        exc_info=True,
                    )

        return point_uid, point_id

    def _get_point_id_from_table_row(self, row: int) -> Optional[int]:
        _uid, point_id = self._get_point_identity_from_table_row(row)
        return point_id

    def _get_or_create_measurement_widget(
        self,
        point_uid: str,
        point_display_id: Optional[int] = None,
    ):
        pm = _pm()
        point_uid = str(point_uid or "").strip()
        if not point_uid:
            return None
        widget = getattr(self, "measurement_widgets", {}).get(point_uid)
        if widget is not None and not getattr(widget, "isHidden", None) is None:
            return widget

        add_to_panel = getattr(self, "add_measurement_widget_to_panel", None)
        if callable(add_to_panel):
            try:
                add_to_panel(point_uid, point_display_id=point_display_id)
            except TypeError:
                add_to_panel(point_uid)
            widget = getattr(self, "measurement_widgets", {}).get(point_uid)
            if widget is not None:
                return widget

        if not self._zone_technical_imports_available():
            pm.logger.error("Cannot create measurement widget - technical imports not available")
            return None

        MeasurementHistoryWidget = self._get_zone_technical_module("MeasurementHistoryWidget")
        widget = MeasurementHistoryWidget(
            masks=getattr(self, "masks", {}),
            ponis=getattr(self, "ponis", {}),
            parent=self,
            point_id=point_display_id if point_display_id is not None else point_uid,
        )
        if not hasattr(self, "measurement_widgets"):
            self.measurement_widgets = {}
        self.measurement_widgets[point_uid] = widget
        return widget

    def pause_measurements(self):
        pm = _pm()
        if not hasattr(self, "paused"):
            self.paused = False
        if not self.paused:
            self.paused = True
            self.pause_btn.setText("Resume")
            pm.logger.info("Measurements paused")
        else:
            self.paused = False
            self.pause_btn.setText("Pause")
            pm.logger.info("Measurements resumed")
            self.measure_next_point()

    def skip_current_point(self):
        pm = _pm()
        if getattr(self, "stopped", False):
            return
        total_points = int(getattr(self, "total_points", 0))
        current = int(getattr(self, "current_measurement_sorted_index", 0))
        if total_points <= 0 or current >= total_points:
            return
        sorted_indices = list(getattr(self, "sorted_indices", []) or [])
        if current >= len(sorted_indices):
            return

        from PyQt5.QtWidgets import QInputDialog

        reason, ok = QInputDialog.getText(
            self,
            "Skip Point",
            "Skip reason:",
        )
        if not ok:
            return
        reason = str(reason or "").strip() or "user_skipped"

        row = int(sorted_indices[current])
        skip_impl = getattr(self, "_skip_point_by_row", None)
        if callable(skip_impl):
            changed = bool(skip_impl(row=row, reason=reason))
            if changed:
                self._append_capture_log(
                    f"Point {current + 1}: skipped ({reason})"
                )
        else:
            pm.logger.warning("Skip requested but _skip_point_by_row is unavailable")

    def stop_measurements(self):
        pm = _pm()
        self.stopped = True
        self.paused = False
        self.current_measurement_sorted_index = 0
        clear_previews = getattr(self, "clear_detector_profile_previews", None)
        if callable(clear_previews):
            clear_previews()
        self.progressBar.setValue(0)
        self.timeRemainingLabel.setText("Measurement stopped.")
        self.start_btn.setEnabled(True)
        self.pause_btn.setText("Pause")
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        if hasattr(self, "skip_btn") and self.skip_btn is not None:
            self.skip_btn.setEnabled(False)
        pm.logger.info("Measurements stopped and reset")

    def _confirm_poni_settings_before_measurement(self):
        pm = _pm()
        try:
            active_aliases = self.hardware_controller.active_detector_aliases
        except (AttributeError, RuntimeError):
            dev_mode = self.config.get("DEV", False)
            ids = self.config.get("dev_active_detectors", []) if dev_mode else self.config.get("active_detectors", [])
            active_aliases = [d.get("alias") for d in self.config.get("detectors", []) if d.get("id") in ids]

        ponis = getattr(self, "ponis", {}) or {}
        poni_files = getattr(self, "poni_files", {}) or {}
        missing = [a for a in active_aliases if not ponis.get(a)]
        if missing:
            pm.QMessageBox.warning(
                self,
                "Missing PONI Calibration",
                "PONI calibration must be set for detectors: "
                + ", ".join(missing)
                + "\nLoad/select a valid technical container before starting measurements.",
            )
            return False

        # No confirmation popup: start measurements immediately when required PONI exists.
        return True
