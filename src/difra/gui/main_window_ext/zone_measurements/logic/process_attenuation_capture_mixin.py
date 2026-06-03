import logging
import os
import time
from collections import Counter
from pathlib import Path

from difra.gui.main_window_ext.zone_measurements.logic.process_capture_files import (
    attenuation_capture_settings,
    read_capture_raw_sidecars,
)
from difra.gui.technical.capture_io import _place_raw_capture_file


def _pm():
    from difra.gui.main_window_ext.zone_measurements.logic import (
        process_capture_mixin as capture_mixin,
    )

    return capture_mixin._pm()


def _process_capture_module():
    from difra.gui.main_window_ext.zone_measurements.logic import (
        process_capture_mixin as capture_mixin,
    )

    return capture_mixin


logger = logging.getLogger(__name__)
_read_capture_raw_sidecars = read_capture_raw_sidecars


class ZoneMeasurementsAttenuationCaptureMixin:
    def _get_loading_position(self):
        try:
            att = self.config.get("attenuation", {})
            pos = att.get("loading_position")
            if pos and isinstance(pos, dict):
                return float(pos.get("x")), float(pos.get("y"))
        except (AttributeError, TypeError, ValueError):
            logger.debug(
                "Suppressed exception in process_capture_mixin.py",
                exc_info=True,
            )
        try:
            if hasattr(self, "_get_home_load_positions"):
                positions = self._get_home_load_positions()
            else:
                positions = self.stage_controller.get_home_load_positions()
            return positions.get("load", (None, None))
        except (AttributeError, RuntimeError, TypeError):
            return (None, None)

    def _get_attenuation_capture_settings(self):
        return attenuation_capture_settings(getattr(self, "config", None))

    def _capture_attenuation_background(self):
        pm = _pm()
        frames, short_t = (
            ZoneMeasurementsAttenuationCaptureMixin._get_attenuation_capture_settings(
                self
            )
        )
        pm.logger.info(
            "Attenuation background requested",
            frames=int(frames),
            integration_time_s=float(short_t),
        )

        load_x, load_y = self._get_loading_position()
        if load_x is None or load_y is None:
            pm.logger.warning("Loading position not configured; skipping attenuation background capture")
            self._append_capture_log("I0 skipped: loading position is not configured")
            self._attenuation_bg_files = None
            return

        try:
            self._move_stage(load_x, load_y, timeout_s=20)
            self._append_hw_log(f"Stage moved to load position: ({load_x:.3f}, {load_y:.3f}) mm")
        except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as e:
            pm.logger.warning(
                "Failed to move to loading position; skipping attenuation background capture",
                error=str(e),
            )
            self._append_capture_log(f"I0 skipped: cannot move to loading position ({e})")
            self._attenuation_bg_files = None
            return

        self._append_capture_log("Attenuation: moved to loading position")
        self._append_capture_log(
            f"Attenuation I0 capture: frames={frames}, t={short_t:.6f}s"
        )

        group_ts = time.strftime("%Y%m%d_%H%M%S")
        base_name = self.fileNameLineEdit.text().strip()
        group_base = os.path.join(self.measurement_folder, f"{base_name}_{group_ts}")
        container_version = _process_capture_module().get_container_version(
            self.config if hasattr(self, "config") else None
        )

        if getattr(self, "hardware_client", None) is None:
            pm.logger.warning(
                "Hardware client unavailable; skipping attenuation background capture"
            )
            self._append_capture_log("I0 skipped: hardware client unavailable")
            self._attenuation_bg_files = None
            return

        try:
            raw_outputs = self.hardware_client.capture_exposure(
                exposure_s=short_t,
                frames=max(int(frames), 1),
                timeout_s=max(30.0, float(short_t) * max(int(frames), 1) + 30.0),
            )
        except (AttributeError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as e:
            pm.logger.warning(
                "Hardware client attenuation capture failed",
                error=str(e),
            )
            self._append_capture_log(f"I0 capture failed: {e}")
            self._attenuation_bg_files = None
            return

        source_usage = Counter()
        fallback_single = next(iter(raw_outputs.values())) if len(raw_outputs) == 1 else None
        for alias in self.detector_controller.keys():
            src_raw = raw_outputs.get(alias) or fallback_single
            if not src_raw:
                continue
            try:
                source_usage[str(Path(src_raw).resolve())] += 1
            except (OSError, RuntimeError, TypeError, ValueError):
                source_usage[str(src_raw)] += 1

        results = {}
        for alias, controller in self.detector_controller.items():
            try:
                per_alias_base = f"{group_base}__{alias}_ATTENUATION0"
                src_raw = raw_outputs.get(alias)
                if src_raw is None and len(raw_outputs) == 1:
                    src_raw = next(iter(raw_outputs.values()))
                if not src_raw:
                    results[alias] = None
                    continue

                target_txt = Path(per_alias_base + ".txt")
                src_path = Path(src_raw)
                key = str(src_path.resolve())
                allow_move = source_usage.get(key, 0) <= 1
                _place_raw_capture_file(src_raw=src_raw, target_txt=target_txt, allow_move=allow_move)
                if key in source_usage and source_usage[key] > 0:
                    source_usage[key] -= 1
                npy_path = controller.convert_to_container_format(
                    str(target_txt), container_version
                )
                results[alias] = npy_path
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                pm.logger.warning("Error capturing attenuation background", detector=alias, error=str(e))
                results[alias] = None

        self._attenuation_bg_files = results
        n_ok = sum(1 for v in results.values() if v)
        self._append_capture_log(f"I0 saved for {n_ok} detector(s)")

        if hasattr(self, "session_manager") and self.session_manager.is_session_active():
            try:
                import numpy as np

                all_data = {}
                for alias, npy_file in results.items():
                    if npy_file:
                        all_data[alias] = np.load(npy_file)

                if all_data:
                    detector_lookup = {d.get("alias"): d for d in self.config.get("detectors", [])}
                    measurement_data = {}
                    detector_metadata = {}
                    poni_alias_map = {}
                    raw_files_by_detector_id = {}
                    for alias, signal in all_data.items():
                        detector_meta = detector_lookup.get(alias, {})
                        detector_id = detector_meta.get("id", alias)
                        measurement_data[detector_id] = signal
                        detector_metadata[detector_id] = {
                            "integration_time_ms": short_t * 1000,
                            "detector_id": detector_id,
                            "timestamp": group_ts,
                            "loading_position_mm": [load_x, load_y],
                            "n_frames": frames,
                        }
                        poni_alias_map[alias] = detector_id
                        raw_files, _raw_paths = _read_capture_raw_sidecars(
                            results[alias],
                            alias,
                        )
                        if raw_files:
                            raw_files_by_detector_id[detector_id] = raw_files

                    self.session_manager.add_attenuation_measurement(
                        measurement_data=measurement_data,
                        detector_metadata=detector_metadata,
                        poni_alias_map=poni_alias_map,
                        raw_files=raw_files_by_detector_id,
                        mode="without",
                    )
                    pm.logger.info("Added I₀ (without sample) to session container", detectors=list(all_data.keys()))
                    self._append_session_log(
                        f"I0 saved to session container ({len(all_data)} detector(s))"
                    )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                pm.logger.error(f"Failed to add I₀ to session container: {e}", exc_info=True)
                self._append_session_log(f"I0 session save failed: {type(e).__name__}")

    def _record_attenuation_files(self, key: str, files: dict):
        try:
            mp = self.state_measurements.get("measurement_points", [])
            idx = self.current_measurement_sorted_index
            uid = mp[idx].get("unique_id") if 0 <= idx < len(mp) else None
        except (AttributeError, IndexError, TypeError, ValueError):
            uid = None
        if uid is None:
            return
        try:
            att = self.state_measurements.setdefault("attenuation_files", {})
            entry = att.setdefault(uid, {})
            entry[key] = files or {}
            if hasattr(self, "state_path_measurements") and self.state_path_measurements:
                if hasattr(self, "_dump_state_measurements"):
                    self._dump_state_measurements()
                else:
                    import json

                    with open(self.state_path_measurements, "w") as f:
                        json.dump(self.state_measurements, f, indent=4)
        except (OSError, TypeError, ValueError) as e:
            _pm().logger.warning(
                "Failed to record attenuation files: %s", e, exc_info=True
            )

    def _start_attenuation_then_normal(self, txt_filename_base: str):
        pm = _pm()
        frames, short_t = (
            ZoneMeasurementsAttenuationCaptureMixin._get_attenuation_capture_settings(
                self
            )
        )
        reuse_existing_i0 = bool(getattr(self, "_reuse_existing_i0_from_session", False))

        if getattr(self, "_attenuation_bg_files", None):
            try:
                self._record_attenuation_files("without_sample", self._attenuation_bg_files)
            except (AttributeError, OSError, TypeError, ValueError):
                logger.debug(
                    "Suppressed exception in process_capture_mixin.py",
                    exc_info=True,
                )
        elif reuse_existing_i0:
            pm.logger.info("Using previously recorded I0 from restored session")
            self._append_capture_log("I0 reused from session container")
        else:
            pm.QMessageBox.warning(
                self,
                "Attenuation Background Missing",
                "Background attenuation (without sample) was not captured; proceeding with with-sample and normal measurements.",
            )

        try:
            self._move_stage(self._x_mm, self._y_mm, timeout_s=15)
            self._append_hw_log(f"Stage returned to point: ({self._x_mm:.3f}, {self._y_mm:.3f}) mm")
        except (RuntimeError, TimeoutError, OSError, TypeError, ValueError):
            logger.debug(
                "Suppressed exception in process_capture_mixin.py",
                exc_info=True,
            )

        if not self._zone_technical_imports_available():
            pm.logger.error("Cannot start attenuation capture - technical imports not available")
            try:
                self._append_capture_log("Error: technical imports unavailable for attenuation")
            except (AttributeError, RuntimeError, TypeError):
                logger.debug(
                    "Suppressed exception in process_capture_mixin.py",
                    exc_info=True,
                )
            return

        self._append_capture_log(
            f"Attenuation I capture: frames={frames}, t={short_t:.6f}s"
        )

        container_version = _process_capture_module().get_container_version(
            self.config if hasattr(self, "config") else None
        )
        CaptureWorker = self._get_zone_technical_module("CaptureWorker")
        self._attn2_worker = CaptureWorker(
            detector_controller=self.detector_controller,
            integration_time=short_t,
            txt_filename_base=txt_filename_base,
            frames=frames,
            naming_mode="attenuation_with",
            container_version=container_version,
            hardware_client=getattr(self, "hardware_client", None),
        )
        self._attn2_thread = pm.QThread()
        self._attn2_worker.moveToThread(self._attn2_thread)
        self._attn2_thread.started.connect(self._attn2_worker.run)

        def _after_attn_with(success2, result_files2):
            if success2:
                self._append_capture_log("Attenuation I capture complete")
            else:
                self._append_capture_log("Attenuation I capture failed")

            moved_map = result_files2 or {}

            try:
                self._record_attenuation_files("with_sample", moved_map)
            except (AttributeError, OSError, TypeError, ValueError):
                logger.debug(
                    "Suppressed exception in process_capture_mixin.py",
                    exc_info=True,
                )

            if hasattr(self, "session_manager") and self.session_manager.is_session_active():
                try:
                    import numpy as np

                    all_data = {}
                    for alias, npy_file in moved_map.items():
                        if npy_file:
                            all_data[alias] = np.load(npy_file)

                    if all_data:
                        detector_lookup = {d.get("alias"): d for d in self.config.get("detectors", [])}
                        measurement_data = {}
                        detector_metadata = {}
                        poni_alias_map = {}
                        raw_files_by_detector_id = {}
                        for alias, signal in all_data.items():
                            detector_meta = detector_lookup.get(alias, {})
                            detector_id = detector_meta.get("id", alias)
                            measurement_data[detector_id] = signal
                            detector_metadata[detector_id] = {
                                "integration_time_ms": short_t * 1000,
                                "detector_id": detector_id,
                                "timestamp": self._timestamp,
                                "point_position_mm": [self._x_mm, self._y_mm],
                                "n_frames": frames,
                            }
                            poni_alias_map[alias] = detector_id
                            raw_files, _raw_paths = _read_capture_raw_sidecars(
                                moved_map[alias],
                                alias,
                            )
                            if raw_files:
                                raw_files_by_detector_id[detector_id] = raw_files

                        self.session_manager.add_attenuation_measurement(
                            measurement_data=measurement_data,
                            detector_metadata=detector_metadata,
                            poni_alias_map=poni_alias_map,
                            raw_files=raw_files_by_detector_id,
                            mode="with",
                        )
                        session_point_index = self._current_session_point_index()
                        self._append_session_log(
                            f"I saved to session container at point {session_point_index}"
                        )
                        try:
                            self.session_manager.link_attenuation_to_points(
                                num_points=1,
                                start_point_idx=session_point_index,
                            )
                            pm.logger.info(
                                f"Linked attenuation to point {session_point_index}"
                            )
                            self._append_session_log(
                                f"Attenuation linked to point {session_point_index}"
                            )
                        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                            pm.logger.warning(f"Failed to link attenuation to point: {e}", exc_info=True)
                            self._append_session_log(
                                f"Attenuation link failed: {type(e).__name__}"
                            )

                        pm.logger.info(
                            f"Added I (with sample) to session container at point {session_point_index}",
                            detectors=list(all_data.keys()),
                        )
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                    pm.logger.error(f"Failed to add I to session container: {e}", exc_info=True)
                    self._append_session_log(f"I session save failed: {type(e).__name__}")

            self._append_capture_log("Starting normal capture after attenuation")
            self._start_normal_capture(txt_filename_base)

        self._attn2_worker.finished.connect(_after_attn_with)
        self._attn2_worker.finished.connect(self._attn2_thread.quit)
        self._attn2_worker.finished.connect(self._attn2_worker.deleteLater)
        self._attn2_thread.finished.connect(self._attn2_thread.deleteLater)
        self._attn2_thread.start()
        _process_capture_module().ZoneMeasurementsProcessCaptureMixin._start_capture_progress_logging(
            self,
            label="Attenuation capture",
            expected_seconds=float(short_t) * max(int(frames), 1),
        )
