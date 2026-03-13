import logging
import json
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PyQt5.QtCore import Qt


def _pm():
    from difra.gui.main_window_ext.zone_measurements.logic import process_mixin as pm

    return pm


logger = logging.getLogger(__name__)


class ZoneMeasurementsProcessResultsMixin:
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

    def _extract_profile_from_measurement(self, measurement_ref: str):
        value = str(measurement_ref or "").strip()
        if not value:
            return None
        try:
            from difra.gui.technical.capture import _load_measurement_array

            data = _load_measurement_array(value)
            arr = np.asarray(data, dtype=float)
            if arr.ndim != 2 or arr.size == 0:
                return None
            return np.nanmean(arr, axis=0)
        except Exception:
            logger.debug(
                "Failed to extract detector profile from '%s'",
                value,
                exc_info=True,
            )
            return None

    def _update_profile_previews_from_result_files(
        self,
        result_files: dict,
        point_uid: Optional[str] = None,
    ):
        updater = getattr(self, "update_detector_profile_preview", None)
        if not callable(updater):
            return
        uid = str(point_uid or "").strip() or None
        for alias, measurement_ref in (result_files or {}).items():
            profile = self._extract_profile_from_measurement(measurement_ref)
            if profile is None:
                continue
            try:
                if uid is None:
                    updater(alias, profile)
                else:
                    try:
                        updater(alias, profile, uid)
                    except TypeError:
                        updater(alias, profile)
            except Exception:
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
        """Return container-backed refs for a just-written session measurement."""
        refs = dict(result_files or {})
        if not session_manager or not measurement_path:
            return refs

        session_path = getattr(session_manager, "session_path", None)
        schema = getattr(session_manager, "schema", None)
        if not session_path or schema is None:
            return refs

        dataset_name = getattr(schema, "DATASET_PROCESSED_SIGNAL", "processed_signal")
        format_detector_role = getattr(schema, "format_detector_role", None)

        for alias, source_path in list(refs.items()):
            if not source_path:
                continue

            role = None
            if callable(format_detector_role):
                try:
                    role = str(format_detector_role(alias) or "").strip()
                except (AttributeError, TypeError, ValueError):
                    role = None

            if not role:
                detector_meta = detector_lookup.get(alias, {})
                detector_id = str(detector_meta.get("id") or alias or "").strip()
                if not detector_id:
                    continue
                role = (
                    detector_id
                    if detector_id.startswith("det_")
                    else f"det_{detector_id.lower()}"
                )

            refs[alias] = (
                f"h5ref://{session_path}"
                f"#{measurement_path}/{role}/{dataset_name}"
            )

        return refs

    def on_capture_finished(self, success: bool, result_files: dict):
        pm = _pm()
        current_index = self.current_measurement_sorted_index
        point_index_1based = (
            self._current_session_point_index()
            if hasattr(self, "_current_session_point_index")
            else current_index + 1
        )
        session_manager = getattr(self, "session_manager", None)

        if not success:
            pm.logger.error("Measurement capture failed")
            marked_failed = False
            if (
                session_manager is not None
                and hasattr(session_manager, "is_session_active")
                and session_manager.is_session_active()
                and hasattr(session_manager, "fail_point_measurement")
            ):
                try:
                    session_manager.fail_point_measurement(
                        point_index=point_index_1based,
                        reason="capture_failed",
                        timestamp_end=time.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    marked_failed = True
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pm.logger.warning("Failed to mark failed measurement in session container", exc_info=True)
            self._append_capture_log(f"Point {point_index_1based}: capture failed")
            if marked_failed:
                self._append_session_log(
                    f"Point {point_index_1based}: marked failed in session container"
                )
            else:
                self._append_session_log(
                    f"Point {point_index_1based}: capture failed before session write"
                )
            return

        pm.logger.info("Measurement capture successful", files=list(result_files.keys()))
        self._append_capture_log(f"Point {point_index_1based}: capture complete")
        if (
            session_manager is not None
            and hasattr(session_manager, "is_session_active")
            and session_manager.is_session_active()
            and hasattr(session_manager, "update_capture_manifest_files")
        ):
            try:
                session_manager.update_capture_manifest_files(
                    point_index=point_index_1based,
                    files_by_alias={k: v for k, v in (result_files or {}).items() if v},
                    source="capture_finished",
                )
            except Exception:
                pm.logger.debug(
                    "Failed to update capture manifest from capture results",
                    exc_info=True,
                )

        detector_lookup = {d["alias"]: d for d in self.config["detectors"]}
        measurements = self.state_measurements.get("measurements_meta", {})
        measurement_points = self.state_measurements.get("measurement_points", [])
        x = self._x_mm
        y = self._y_mm
        point_unique_id = None
        if 0 <= current_index < len(measurement_points):
            point_unique_id = measurement_points[current_index].get("unique_id")
        if not point_unique_id:
            if hasattr(self, "_new_measurement_point_uid"):
                point_unique_id = self._new_measurement_point_uid(point_index_1based)
            else:
                point_unique_id = f"{int(point_index_1based)}_00000000"
            pm.logger.warning(
                "Measurement point metadata index mismatch; using fallback unique_id",
                current_index=int(current_index),
                measurement_points_count=int(len(measurement_points)),
                session_point_index=int(point_index_1based),
            )
        add_to_panel = getattr(self, "add_measurement_widget_to_panel", None)
        if callable(add_to_panel):
            try:
                add_to_panel(point_unique_id)
            except Exception:
                logger.debug(
                    "Suppressed exception in process_results_mixin.py",
                    exc_info=True,
                )
        self._update_profile_previews_from_result_files(
            result_files,
            point_uid=point_unique_id,
        )

        for alias, npy_filename in result_files.items():
            if not npy_filename:
                pm.logger.warning("Capture returned empty file path", detector_alias=alias)
                continue
            detector_meta = detector_lookup.get(alias, {})
            entry = {
                "x": x,
                "y": y,
                "unique_id": point_unique_id,
                "base_file": self._base_name,
                "integration_time": self.integration_time,
                "detector_alias": alias,
                "detector_id": detector_meta.get("id"),
                "detector_type": detector_meta.get("type"),
                "detector_size": detector_meta.get("size"),
                "pixel_size_um": detector_meta.get("pixel_size_um"),
                "faulty_pixels": detector_meta.get("faulty_pixels"),
            }
            gh = getattr(self, "calibration_group_hash", None)
            if gh:
                entry["CALIBRATION_GROUP_HASH"] = gh
            measurements[Path(npy_filename).name] = entry

        self.state_measurements["measurements_meta"] = measurements
        worker_file_map = dict(result_files or {})

        try:
            if hasattr(self, "_dump_state_measurements"):
                self._dump_state_measurements()
            else:
                with open(self.state_path_measurements, "w") as f:
                    json.dump(self.state_measurements, f, indent=4)
        except (OSError, TypeError, ValueError) as exc:
            pm.logger.warning(
                "Failed to persist measurement state file",
                error=str(exc),
                exc_info=True,
            )
            self._append_capture_log(
                f"Warning: failed to persist state file ({type(exc).__name__})"
            )

        pm.logger.info(
            "Measurement state file updated",
            state_file=str(self.state_path_measurements),
            entries=len(measurements),
        )
        self._append_capture_log("Measurement metadata saved to state file")

        if session_manager is not None and hasattr(session_manager, "is_session_active") and session_manager.is_session_active():
            self._append_session_log(f"Point {point_index_1based}: writing to session container")
            try:
                pm.logger.info(f"=== ADDING MEASUREMENT TO H5 (Point {point_index_1based}) ===")
                pm.logger.info(f"Session path: {session_manager.session_path}")

                all_data = {}
                raw_files_data = {}

                detector_lookup = {d["alias"]: d for d in self.config["detectors"]}
                poni_alias_map = {}
                for alias, npy_file in result_files.items():
                    if not npy_file:
                        continue
                    npy_path = Path(npy_file)
                    if not npy_path.exists():
                        pm.logger.warning("Capture file missing on disk", detector_alias=alias, file=str(npy_file))
                        continue
                    detector_meta = detector_lookup.get(alias, {})
                    detector_id = detector_meta.get("id", alias)
                    poni_alias_map[alias] = detector_id
                    pm.logger.info(f"Loading {alias} data from: {npy_path.name}")
                    all_data[detector_id] = np.load(npy_file)
                    pm.logger.info(f"  Data shape: {all_data[detector_id].shape}")

                    base_name = npy_path.stem
                    folder = npy_path.parent

                    detector_controller = self.detector_controller.get(alias)
                    if detector_controller and hasattr(detector_controller, "get_raw_file_patterns"):
                        patterns = detector_controller.get_raw_file_patterns()
                    else:
                        patterns = ["*.txt", "*.dsc", "*.t3pa"]
                        pm.logger.warning(
                            f"Detector {alias} has no get_raw_file_patterns(), using default patterns"
                        )

                    raw_files = {}
                    for pattern in patterns:
                        ext = pattern[1:] if pattern.startswith("*") else pattern
                        raw_file = folder / f"{base_name}{ext}"
                        if raw_file.exists():
                            try:
                                with open(raw_file, "rb") as f:
                                    file_format = ext[1:] if ext.startswith(".") else ext
                                    blob_key = f"raw_{file_format}"
                                    raw_files[blob_key] = f.read()
                                pm.logger.debug(f"Read raw file for blob: {raw_file.name} -> {blob_key}")
                            except OSError as e:
                                pm.logger.warning(f"Failed to read raw file {raw_file}: {e}")

                    if raw_files:
                        raw_files_data[detector_id] = raw_files
                        pm.logger.info(
                            f"  Found {len(raw_files)} raw files for {alias}: {list(raw_files.keys())}"
                        )
                    else:
                        pm.logger.warning(
                            f"  No raw files found for {alias} using patterns {patterns}"
                        )

                pm.logger.info(f"Loaded data from {len(all_data)} detectors")

                detector_metadata = {}
                for detector_id in all_data.keys():
                    detector_metadata[detector_id] = {
                        "integration_time_ms": self.integration_time * 1000,
                        "detector_id": detector_id,
                        "x_mm": x,
                        "y_mm": y,
                        "timestamp": self._timestamp,
                        "unique_id": point_unique_id,
                    }

                if not all_data:
                    pm.logger.error(
                        "No detector payload produced for successful capture; marking failed",
                        point_index=point_index_1based,
                    )
                    if hasattr(session_manager, "fail_point_measurement"):
                        session_manager.fail_point_measurement(
                            point_index=point_index_1based,
                            reason="capture_success_without_payload",
                            timestamp_end=time.strftime("%Y-%m-%d %H:%M:%S"),
                        )
                    raise RuntimeError("No detector payload produced")

                raw_files_by_detector_id = raw_files_data
                pm.logger.info(f"Writing to H5: /measurements/pt_{point_index_1based:03d}/meas_NNNNNNNNN")
                pm.logger.info(f"  Detectors: {list(all_data.keys())}")
                pm.logger.info(f"  Raw files: {len(raw_files_by_detector_id)} detector(s) with blobs")

                if hasattr(session_manager, "complete_point_measurement"):
                    measurement_path = session_manager.complete_point_measurement(
                        point_index=point_index_1based,
                        measurement_data=all_data,
                        detector_metadata=detector_metadata,
                        poni_alias_map=poni_alias_map,
                        raw_files=raw_files_by_detector_id if raw_files_by_detector_id else None,
                        timestamp_end=time.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                else:
                    measurement_path = session_manager.add_measurement(
                        point_index=point_index_1based,
                        measurement_data=all_data,
                        detector_metadata=detector_metadata,
                        poni_alias_map=poni_alias_map,
                        raw_files=raw_files_by_detector_id if raw_files_by_detector_id else None,
                    )
                worker_file_map = self._build_session_measurement_result_refs(
                    session_manager=session_manager,
                    measurement_path=measurement_path,
                    result_files=result_files,
                    detector_lookup=detector_lookup,
                )

                pm.logger.info(f"✓ Measurement added to H5 container for point {point_index_1based}")
                self._append_session_log(
                    f"Point {point_index_1based}: saved to session ({len(all_data)} detector(s))"
                )
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
                pm.logger.error("=" * 60)
                pm.logger.error("✗ CRITICAL ERROR: Failed to add measurement to H5")
                pm.logger.error("=" * 60)
                pm.logger.error(f"Error type: {type(e).__name__}")
                pm.logger.error(f"Error message: {e}")
                pm.logger.error(f"Point index: {point_index_1based}")
                pm.logger.error(f"Detectors: {list(result_files.keys())}")
                pm.logger.error(
                    f"Session path: {session_manager.session_path if session_manager is not None else 'N/A'}"
                )
                pm.logger.error("=" * 60, exc_info=True)
                pm.logger.warning("Continuing measurement workflow despite H5 write failure...")
                self._append_session_log(
                    f"Point {point_index_1based}: session write failed ({type(e).__name__})"
                )
                if hasattr(session_manager, "fail_point_measurement"):
                    try:
                        session_manager.fail_point_measurement(
                            point_index=point_index_1based,
                            reason=f"h5_write_failed:{type(e).__name__}",
                            timestamp_end=time.strftime("%Y-%m-%d %H:%M:%S"),
                        )
                        self._append_session_log(
                            f"Point {point_index_1based}: marked failed after session write error"
                        )
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pm.logger.warning("Failed to persist failed status for point measurement", exc_info=True)
        else:
            pm.logger.warning("⚠ Session manager not active - measurements will NOT be saved to H5!")
            self._append_session_log("No active session container; point saved to files only")

        pm.logger.info("Spawning measurement thread for post-processing...")
        if self.current_measurement_sorted_index < len(self.sorted_indices):
            current_row = self.sorted_indices[self.current_measurement_sorted_index]
            self.spawn_measurement_thread(current_row, worker_file_map)
            self._append_capture_log("Post-processing started")
        else:
            pm.logger.warning(
                "Skipped post-processing thread due to point index mismatch",
                current_index=int(self.current_measurement_sorted_index),
                sorted_indices_count=int(len(self.sorted_indices)),
            )
            self._append_capture_log("Post-processing skipped due to point index mismatch")

        pm.logger.info("Updating UI visual feedback...")
        green_brush = pm.QColor(0, 255, 0)
        self._point_item.setBrush(green_brush)
        try:
            if self._zone_item:
                green_zone = pm.QColor(0, 255, 0)
                green_zone.setAlphaF(0.2)
                self._zone_item.setBrush(green_zone)
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            pm.logger.warning("Error updating zone item color", error=str(e))

        pm.logger.info("Scheduling measurement_finished in 1000ms...")
        pm.QTimer.singleShot(1000, self.measurement_finished)
        self._append_capture_log("Next point scheduled")
        pm.logger.info("<<< on_capture_finished complete")

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
