import json
import logging
import time
from pathlib import Path

from difra.gui.main_window_ext.zone_measurements.logic.process_result_files import (
    collect_capture_payloads,
)


def _pm():
    from difra.gui.main_window_ext.zone_measurements.logic import (
        process_results_mixin as prm,
    )

    return prm._pm()


logger = logging.getLogger(__name__)


class ZoneMeasurementsCaptureFinishedMixin:
    def on_capture_finished(self, success: bool, result_files: dict):
        pm = _pm()
        stop_progress = getattr(self, "_stop_capture_progress_logging", None)
        if callable(stop_progress):
            try:
                stop_progress()
            except Exception:
                logger.debug(
                    "Suppressed exception in process_capture_finished_mixin.py",
                    exc_info=True,
                )
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
                    pm.logger.warning(
                        "Failed to mark failed measurement in session container",
                        exc_info=True,
                    )
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
                    "Suppressed exception in process_capture_finished_mixin.py",
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

        if (
            session_manager is not None
            and hasattr(session_manager, "is_session_active")
            and session_manager.is_session_active()
        ):
            self._append_session_log(
                f"Point {point_index_1based}: writing to session container"
            )
            try:
                pm.logger.info(
                    f"=== ADDING MEASUREMENT TO H5 (Point {point_index_1based}) ==="
                )
                pm.logger.info(f"Session path: {session_manager.session_path}")

                detector_lookup = {d["alias"]: d for d in self.config["detectors"]}
                payload = collect_capture_payloads(
                    result_files=result_files,
                    detector_lookup=detector_lookup,
                    detector_controller=getattr(self, "detector_controller", {}),
                    logger=pm.logger,
                )
                all_data = payload["all_data"]
                raw_files_data = payload["raw_files_data"]
                raw_paths_by_alias = payload["raw_paths_by_alias"]
                poni_alias_map = payload["poni_alias_map"]

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
                pm.logger.info(
                    f"Writing to H5: /measurements/pt_{point_index_1based:03d}/meas_NNNNNNNNN"
                )
                pm.logger.info(f"  Detectors: {list(all_data.keys())}")
                pm.logger.info(
                    f"  Raw files: {len(raw_files_by_detector_id)} detector(s) with blobs"
                )
                if hasattr(session_manager, "update_capture_manifest_files"):
                    try:
                        session_manager.update_capture_manifest_files(
                            point_index=point_index_1based,
                            files_by_alias={
                                alias: path
                                for alias, path in (result_files or {}).items()
                                if path
                            },
                            raw_files_by_alias=raw_paths_by_alias,
                            source="capture_finished_with_raw",
                        )
                    except Exception:
                        pm.logger.debug(
                            "Failed to update capture manifest with raw file paths",
                            exc_info=True,
                        )

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

                pm.logger.info(
                    f"✓ Measurement added to H5 container for point {point_index_1based}"
                )
                self._append_session_log(
                    f"Point {point_index_1based}: saved to session ({len(all_data)} detector(s))"
                )
            except (
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as e:
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
                pm.logger.warning(
                    "Continuing measurement workflow despite H5 write failure..."
                )
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
                        pm.logger.warning(
                            "Failed to persist failed status for point measurement",
                            exc_info=True,
                        )
        else:
            pm.logger.warning(
                "⚠ Session manager not active - measurements will NOT be saved to H5!"
            )
            self._append_session_log(
                "No active session container; point saved to files only"
            )

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
            self._append_capture_log(
                "Post-processing skipped due to point index mismatch"
            )

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
