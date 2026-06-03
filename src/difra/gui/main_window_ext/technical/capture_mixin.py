import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List

from difra.gui.container_api import get_container_version
from difra.gui.main_window_ext.technical.capture_auto_poni_mixin import TechnicalCaptureAutoPoniMixin
from difra.gui.main_window_ext.technical.capture_core_mixin import TechnicalCaptureCoreMixin
from difra.gui.main_window_ext.technical.capture_pyfai_mixin import TechnicalCapturePyfaiMixin

logger = logging.getLogger(__name__)

def _tm():
    from difra.gui.main_window_ext import technical_measurements as tm

    return tm

class TechnicalCaptureMixin(
    TechnicalCaptureAutoPoniMixin,
    TechnicalCapturePyfaiMixin,
    TechnicalCaptureCoreMixin,
):
    TECHNICAL_TYPE_ORDER = {
        "DARK": "001",
        "EMPTY": "002",
        "AGBH": "003",
        "BACKGROUND": "004",
    }

    def _is_container_backed_aux_row(self, row: int) -> bool:
        tm = _tm()
        if row < 0:
            return False
        file_item = self.auxTable.item(row, self.AUX_COL_FILE)
        if file_item is None:
            return False
        source_ref = str(file_item.data(tm.Qt.UserRole) or "").strip()
        return source_ref.startswith("h5ref://")

    def _handle_aux_table_cell_clicked(self, row: int, col: int):
        """Open container-backed measurements on single-click of the file cell."""
        if col != self.AUX_COL_FILE:
            return
        if not self._is_container_backed_aux_row(row):
            return
        self._open_measurement_from_table(row, col)

    def _handle_aux_table_cell_double_clicked(self, row: int, col: int):
        """Keep double-click open for regular files, without double-opening h5 refs."""
        if col == self.AUX_COL_FILE and self._is_container_backed_aux_row(row):
            return
        self._open_measurement_from_table(row, col)

    def _start_capture(self, typ: str):
        tm = _tm()
        if not self._technical_imports_available():
            error_msg = (
                f"Cannot start {typ} capture - technical measurement modules failed to import. "
                "Check application logs for detailed error information. "
                "Common causes: missing pyFAI or fabio dependencies."
            )
            self._log_technical_event(error_msg)
            logger.error(error_msg)
            tm.QMessageBox.warning(
                self,
                "Import Error",
                error_msg + "\n\nPlease check the application log file for detailed traceback.",
            )
            return

        counter_attr = f"{typ.lower()}_counter"
        count = getattr(self, counter_attr, 0) + 1
        setattr(self, counter_attr, count)

        validate_folder = self._get_technical_module("validate_folder")
        folder = validate_folder(self._current_technical_output_folder())
        ts = time.strftime("%Y%m%d_%H%M%S")
        integration_time_s = float(self.integrationTimeSpin.value())
        frames = int(self.captureFramesSpin.value())
        txt_filename_base = os.path.join(
            folder,
            self._technical_capture_base_stem(
                typ=typ,
                count=count,
                timestamp_token=ts,
                integration_time_s=integration_time_s,
                frames=frames,
            ),
        )

        stage_controller = self._resolve_capture_stage_controller()

        enable_continuous_movement = (
            getattr(self, "moveContinuousCheck", None) is not None
            and self.moveContinuousCheck.isChecked()
            and str(typ).strip().upper() == "AGBH"
        )
        continuous_movement_controller = getattr(
            self, "continuous_movement_controller", None
        )
        if enable_continuous_movement:
            continuous_movement_controller = (
                self._ensure_capture_continuous_movement_controller(stage_controller)
            )
        movement_radius = (
            self.movementRadiusSpin.value()
            if getattr(self, "movementRadiusSpin", None) is not None
            else 2.0
        )

        logger.debug(
            f"Starting {typ} capture: integration_time={integration_time_s}s, frames={frames}, "
            f"continuous_movement={enable_continuous_movement}, radius={movement_radius}mm"
        )
        self._pending_aux_capture_metadata = {
            "integration_time_ms": integration_time_s * 1000.0,
            "n_frames": frames,
        }

        container_version = get_container_version(
            self.config if hasattr(self, "config") else None
        )
        CaptureWorker = self._get_technical_module("CaptureWorker")
        worker = CaptureWorker(
            detector_controller=self.detector_controller,
            integration_time=integration_time_s,
            txt_filename_base=txt_filename_base,
            frames=frames,
            naming_mode="normal",
            continuous_movement_controller=continuous_movement_controller,
            stage_controller=stage_controller,
            enable_continuous_movement=enable_continuous_movement,
            movement_radius=movement_radius,
            container_version=container_version,
            hardware_client=getattr(self, "hardware_client", None),
        )
        thread = tm.QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _cleanup(success, result_files, t=typ):
            try:
                self._on_capture_done(
                    success,
                    result_files,
                    t,
                    error_messages=getattr(worker, "error_messages", None),
                )
            except Exception as e:
                logger.error(f"Error in _on_capture_done for {t}: {e}", exc_info=True)
            finally:
                worker.deleteLater()
                thread.quit()
                thread.deleteLater()
                self._capture_workers.remove(worker)

        worker.finished.connect(_cleanup)
        thread.start()

        if not hasattr(self, "_capture_workers"):
            self._capture_workers = []
        self._capture_workers.append(worker)

    def _on_capture_done(
        self,
        success: bool,
        result_files: dict,
        typ: str,
        error_messages=None,
    ):
        if not success:
            details = [
                str(msg).strip()
                for msg in (error_messages or [])
                if str(msg).strip()
            ]
            if details:
                joined = " | ".join(details[:3])
                self._log_technical_event(f"{typ} capture failed: {joined}")
            else:
                self._log_technical_event(f"{typ} capture failed")
            logger.warning(
                "[%s] capture failed; details=%s; result_files=%s",
                typ,
                details,
                result_files,
            )
            self._aux_timer.stop()
            self._aux_status.setText("")
            return

        self._log_technical_event(f"{typ} capture successful: {len(result_files)} files")
        logger.info(f"[{typ}] capture successful: {list(result_files.keys())}")
        self._aux_timer.stop()
        self._aux_status.setText("Processing...")

        if not self._technical_imports_available():
            error_msg = "Cannot process files - technical imports not available"
            self._log_technical_event(error_msg)
            logger.error(error_msg)
            self._aux_status.setText("Import error")
            return

        self._log_technical_event("Processing measurement files...")
        try:
            append_to_container = getattr(
                self,
                "_append_captured_result_files_to_active_container",
                None,
            )
            if callable(append_to_container):
                appended = bool(
                    append_to_container(
                        result_files=result_files,
                        technical_type=typ,
                        show_errors=True,
                    )
                )
                self._aux_status.setText("" if appended else "Container sync error")
                return

            MeasurementWorker = self._get_technical_module("MeasurementWorker")
            worker = MeasurementWorker(
                filenames=result_files,
                frames=1,
                average_frames=False,
            )
            worker.add_aux_item.connect(self._add_aux_item_to_list)
            worker.run()
            if hasattr(self, "_sync_active_technical_container_from_table"):
                self._sync_active_technical_container_from_table(show_errors=True)
            self._aux_status.setText("")
        finally:
            self._pending_aux_capture_metadata = None

    def measure_aux(self):
        tm = _tm()
        if not self._technical_imports_available():
            self._log_technical_event("Cannot start Aux measurement - technical imports not available")
            logger.warning(
                "Cannot start Aux measurement - technical measurements disabled due to import errors"
            )
            tm.QMessageBox.warning(
                self,
                "Technical Measurements Unavailable",
                "Technical measurements are disabled due to import errors.\n\nCheck the console for details.",
            )
            return

        if hasattr(self, "_ensure_active_technical_container_available"):
            ready = self._ensure_active_technical_container_available(
                for_edit=True,
                prompt_on_locked=True,
            )
            if not ready:
                self._log_technical_event(
                    "Aux measurement cancelled: technical container is not editable"
                )
                return

        self._log_technical_event("Starting auxiliary measurement...")
        self._aux_start = time.time()
        self._aux_spinner_state = 0
        self._aux_status.setText("0 s ⁑")
        self._aux_timer.start()
        self._start_capture("Aux")

    def _open_measurement_from_table(self, row: int, _col: int):
        tm = _tm()
        file_item = self.auxTable.item(row, self.AUX_COL_FILE)
        if not file_item:
            return
        source_info = (
            file_item.data(self._aux_source_info_role())
            if file_item is not None and hasattr(self, "_aux_source_info_role")
            else {}
        )
        if not isinstance(source_info, dict):
            source_info = {}

        file_path = file_item.data(tm.Qt.UserRole)
        resolved_path = str(file_path or "").strip()
        source_kind = str(source_info.get("source_kind") or "").strip().lower()
        container_path = str(source_info.get("container_path") or "").strip()
        dataset_path = str(source_info.get("dataset_path") or "").strip()
        if (
            not resolved_path.startswith("h5ref://")
            and source_kind == "container"
            and container_path
            and dataset_path
        ):
            resolved_path = f"h5ref://{container_path}#{dataset_path}"
            try:
                file_item.setData(tm.Qt.UserRole, resolved_path)
            except Exception:
                logger.debug(
                    "Failed to repoint stale technical-table source ref for row %s",
                    row,
                    exc_info=True,
                )
        is_h5_ref = resolved_path.startswith("h5ref://")
        if resolved_path and not is_h5_ref and not os.path.exists(resolved_path):
            folder = self._current_technical_output_folder()
            candidate = os.path.join(folder, os.path.basename(resolved_path)) if folder else ""
            if candidate and os.path.exists(candidate):
                resolved_path = candidate

        self._log_technical_event(
            f"Opening measurement file: {os.path.basename(resolved_path) if resolved_path else 'Unknown'}"
        )

        if not resolved_path or (not is_h5_ref and not os.path.exists(resolved_path)):
            tm.QMessageBox.warning(
                self,
                "File Not Found",
                f"Measurement file is missing:\n{resolved_path or str(file_path)}",
            )
            self._log_technical_event(
                f"Cannot open measurement: missing file {resolved_path or str(file_path)}"
            )
            return

        alias_cb = self.auxTable.cellWidget(row, self.AUX_COL_ALIAS)
        alias = None
        if isinstance(alias_cb, tm.QComboBox):
            a = alias_cb.currentText().strip()
            if a and a != self.NO_SELECTION_LABEL:
                alias = a

        if not alias:
            disp = file_item.text()
            if ":" in disp:
                alias = disp.split(":", 1)[0].strip()

        if not alias:
            try:
                alias = next(iter(self.detector_controller))
            except Exception:
                alias = None

        if not self._technical_imports_available():
            self._log_technical_event("Cannot open measurement window - technical imports not available")
            return

        source_ref = str(file_item.data(tm.Qt.UserRole) or "").strip() if file_item is not None else ""
        show_measurement_window = self._get_technical_module("show_measurement_window")
        poni_text = self._resolve_technical_measurement_poni(
            alias=alias,
            source_ref=source_ref,
            source_info=source_info if isinstance(source_info, dict) else {},
        )
        mask = self._resolve_technical_measurement_mask(
            alias=alias,
            source_ref=source_ref,
            source_info=source_info if isinstance(source_info, dict) else {},
        )
        try:
            show_measurement_window(
                resolved_path,
                mask,
                poni_text,
                self,
            )
        except Exception as exc:
            self._log_technical_event(f"Failed to open measurement window: {exc}")
            tm.QMessageBox.warning(
                self,
                "Open Measurement Failed",
                f"Could not open measurement file:\n{resolved_path}\n\nError: {exc}",
            )

    def run_pyfai(self):
        tm = _tm()
        self._log_technical_event("Starting PyFAI calibration...")
        env = self._resolve_pyfai_conda_env()
        if not env:
            msg = (
                "No conda environment configured for PyFAI.\n\n"
                "Set 'pyfai_conda' (or 'conda') in your configuration to specify "
                "which conda environment contains pyfai-calib2."
            )
            self._log_technical_event("Error: No conda environment configured")
            logger.warning(
                "No conda env set in self.config['pyfai_conda'] or self.config['conda']"
            )
            tm.QMessageBox.warning(self, "PyFAI Not Configured", msg)
            return

        review = self._prepare_selected_agbh_pyfai_review()
        if review is False:
            return

        validate_folder = self._get_technical_module("validate_folder")
        if review is not None:
            folder = validate_folder(str(review.image_path.parent))
            pyfai_command = list(review.command)
        else:
            folder = validate_folder(self._current_technical_output_folder())
            pyfai_command = ["pyfai-calib2"]

        if os.name == "nt":
            ps_content = self._build_windows_pyfai_script(
                folder=str(folder),
                env=env,
                command=pyfai_command,
            )
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".ps1", delete=False, encoding="utf-8"
                ) as ps_file:
                    ps_file.write(ps_content)
                    ps_path = ps_file.name
                start_cmd = (
                    f'Start-Process powershell '
                    f'-ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "{ps_path}"'
                )
                subprocess.Popen(["powershell", "-NoProfile", "-Command", start_cmd])
                self._log_technical_event(
                    f"PyFAI calibration launched in new PowerShell window via conda run ({env})"
                )
                logger.info("Launched PyFAI in new PowerShell window via %s", ps_path)
            except Exception as e:
                self._log_technical_event(f"Failed to launch PyFAI on Windows: {e}")
                logger.warning("Failed to launch PyFAI on Windows: %s", e, exc_info=True)
            return

        try:
            command_line = self._build_posix_conda_pyfai_command(
                env=env,
                command=pyfai_command,
            )
            if sys.platform == "darwin":
                script_content = f"""#!/bin/bash
cd "{folder}"
echo "Starting PyFAI calibration in conda environment: {env}"
echo "Folder: {folder}"
echo ""
{command_line}
if [ $? -ne 0 ]; then
    echo ""
    echo "Error: Failed to launch PyFAI. Check that:"
    echo "  1. Conda environment '{env}' exists (run: conda env list)"
    echo "  2. pyfai-calib2 is installed (run: conda run -n {env} which pyfai-calib2)"
    echo ""
    echo "Press any key to close..."
    read -n 1
fi
"""
                with tempfile.NamedTemporaryFile(mode="w", suffix=".command", delete=False) as f:
                    f.write(script_content)
                    script_path = f.name
                os.chmod(script_path, 0o755)
                subprocess.Popen(["open", "-a", "Terminal", script_path])
                self._log_technical_event(f"PyFAI calibration script created: {script_path}")
            else:
                bash_cmd = (
                    f'cd "{folder}" && '
                    f'echo "Starting PyFAI in environment: {env}" && '
                    f'{command_line} || '
                    f'(echo "\\nError: Failed to launch PyFAI"; read -p "Press Enter to close...")'
                )
                for terminal in ["gnome-terminal", "konsole", "xterm"]:
                    try:
                        subprocess.Popen([terminal, "--", "bash", "-c", bash_cmd])
                        break
                    except FileNotFoundError:
                        continue
            self._log_technical_event("PyFAI calibration launched in new terminal window")
            logger.info("Launched PyFAI in new terminal window")
        except Exception as e:
            self._log_technical_event(f"Failed to launch PyFAI on Unix: {e}")
            logger.warning("Failed to launch PyFAI on Unix: %s", e, exc_info=True)

    def _update_aux_status(self):
        elapsed = int(time.time() - self._aux_start)
        spinner = ["⁑", "⁙", "⁹", "⁸", "‼", "‴", "…", "‧", " ", "‏"]
        ch = spinner[self._aux_spinner_state % len(spinner)]
        self._aux_spinner_state += 1
        self._aux_status.setText(f"{elapsed} s {ch}")

        if elapsed > 0 and elapsed % 10 == 0 and self._aux_spinner_state % len(spinner) == 0:
            self._log_technical_event(f"Auxiliary measurement in progress: {elapsed} seconds")
