import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def _tm():
    from difra.gui.main_window_ext.technical import capture_mixin

    return capture_mixin._tm()


class TechnicalCapturePyfaiMixin:
    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"
    @staticmethod
    def _build_windows_conda_pyfai_command(*, env: str, command=None) -> str:
        target_env = str(env or "").strip()
        args = [str(arg) for arg in (command or ["pyfai-calib2"])]
        if args == ["pyfai-calib2"]:
            return f"conda run --no-capture-output -n {target_env} pyfai-calib2"
        quoted = " ".join(TechnicalCapturePyfaiMixin._ps_quote(arg) for arg in args)
        return (
            "conda run --no-capture-output "
            f"-n {TechnicalCapturePyfaiMixin._ps_quote(target_env)} {quoted}"
        )
    @staticmethod
    def _build_posix_conda_pyfai_command(*, env: str, command=None) -> str:
        target_env = str(env or "").strip()
        args = [str(arg) for arg in (command or ["pyfai-calib2"])]
        quoted = " ".join(shlex.quote(arg) for arg in args)
        return f"conda run -n {shlex.quote(target_env)} {quoted}"
    @staticmethod
    def _build_windows_pyfai_script(*, folder: str, env: str, command=None) -> str:
        target_folder = str(folder or "").strip()
        target_env = str(env or "").strip()
        command_line = TechnicalCapturePyfaiMixin._build_windows_conda_pyfai_command(
            env=target_env,
            command=command,
        )
        return "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"Set-Location '{target_folder}'",
                f"Write-Host 'Starting PyFAI calibration in conda environment: {target_env}'",
                f"Write-Host 'Folder: {target_folder}'",
                "Write-Host ''",
                command_line,
                "if ($LASTEXITCODE -ne 0) {",
                "  Write-Host ''",
                "  Write-Host 'Error: Failed to launch PyFAI.'",
                "  Write-Host 'Check that the conda environment exists and pyfai-calib2 is installed there.'",
                "}",
                "",
            ]
        )
    def _resolve_capture_stage_controller(self):
        if hasattr(self, "hardware_controller") and self.hardware_controller:
            stage_controller = getattr(self.hardware_controller, "stage_controller", None)
            if stage_controller is not None:
                return stage_controller
        stage_controller = getattr(self, "stage_controller", None)
        if stage_controller is not None:
            return stage_controller
        if hasattr(self, "hardware_client") and self.hardware_client:
            return getattr(self.hardware_client, "stage_controller", None)
        return None
    def _ensure_capture_continuous_movement_controller(self, stage_controller):
        if stage_controller is None:
            return None
        current = getattr(self, "continuous_movement_controller", None)
        if current is not None:
            if getattr(current, "stage_controller", None) is not None:
                return current
        try:
            from difra.gui.technical.continuous_movement import ContinuousMovementController

            parent = self if hasattr(self, "metaObject") else None
            current = ContinuousMovementController(
                stage_controller=stage_controller,
                parent=parent,
            )
            current.movement_error.connect(
                lambda msg: self._log_technical_event(f"Movement error: {msg}")
            )
            self.continuous_movement_controller = current
            logger.info("Continuous movement controller initialized on demand")
            return current
        except Exception:
            logger.error(
                "Failed to initialize continuous movement controller on demand",
                exc_info=True,
            )
            return getattr(self, "continuous_movement_controller", None)
    def _read_pyfai_conda_from_global_config(self) -> str:
        """Best-effort read of dedicated PyFAI conda env from split global config."""
        try:
            config_dir = Path(__file__).resolve().parents[3] / "resources" / "config"
            global_path = config_dir / "global.json"
            if not global_path.exists():
                return ""
            payload = json.loads(global_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return ""
            return str(payload.get("pyfai_conda") or "").strip()
        except Exception:
            logger.debug("Failed to read pyfai_conda from global config", exc_info=True)
            return ""
    def _list_conda_env_names(self) -> List[str]:
        """Return discovered conda environment names (best-effort, empty on failure)."""
        try:
            proc = subprocess.run(
                ["conda", "env", "list", "--json"],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(proc.stdout or "{}")
            env_paths = payload.get("envs") if isinstance(payload, dict) else []
            if not isinstance(env_paths, list):
                return []
            names: List[str] = []
            for env_path in env_paths:
                name = Path(str(env_path)).name.strip()
                if name:
                    names.append(name)
            return names
        except Exception:
            return []
    def _resolve_pyfai_conda_env(self) -> str:
        """Resolve conda env for PyFAI button with explicit per-tool precedence."""
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}

        explicit = str(cfg.get("pyfai_conda") or "").strip()
        if explicit:
            return explicit

        global_explicit = self._read_pyfai_conda_from_global_config()
        if global_explicit:
            return global_explicit

        fallback = str(cfg.get("conda") or "").strip()
        if not fallback:
            return ""

        return fallback
    def _selected_aux_row_for_pyfai(self):
        try:
            if not hasattr(self, "auxTable") or self.auxTable is None:
                return None
            selection = self.auxTable.selectionModel()
            rows = sorted({index.row() for index in selection.selectedRows()}) if selection else []
            return rows[0] if rows else None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None
    def _aux_row_alias(self, row: int) -> str:
        tm = _tm()
        alias_cb = self.auxTable.cellWidget(row, self.AUX_COL_ALIAS)
        if isinstance(alias_cb, tm.QComboBox):
            alias = str(alias_cb.currentText() or "").strip()
            if alias and alias != getattr(self, "NO_SELECTION_LABEL", ""):
                return alias
        file_item = self.auxTable.item(row, self.AUX_COL_FILE)
        if file_item is not None and ":" in str(file_item.text() or ""):
            return str(file_item.text()).split(":", 1)[0].strip()
        return ""
    def _aux_row_type(self, row: int) -> str:
        type_cb = self.auxTable.cellWidget(row, self.AUX_COL_TYPE)
        if type_cb is not None and callable(getattr(type_cb, "currentText", None)):
            return str(type_cb.currentText() or "").strip().upper()
        return ""
    def _detector_config_for_alias(self, alias: str) -> dict:
        alias_key = str(alias or "").strip()
        for detector_cfg in (self.config.get("detectors", []) if hasattr(self, "config") else []):
            if str(detector_cfg.get("alias") or "").strip() == alias_key:
                return dict(detector_cfg)
        return {"alias": alias_key}
    def _auto_poni_detector_config_for_alias(self, alias: str) -> dict:
        detector_config = self._detector_config_for_alias(alias)
        rule_alias = str(self._auto_poni_rule_alias(alias) or "").strip()
        alias_key = str(alias or "").strip()
        if rule_alias and rule_alias.upper() != alias_key.upper():
            for candidate in (
                self.config.get("detectors", []) if hasattr(self, "config") else []
            ):
                if str(candidate.get("alias") or "").strip().upper() == rule_alias.upper():
                    merged = dict(candidate)
                    merged["alias"] = alias_key
                    merged["id"] = detector_config.get("id", merged.get("id", alias_key))
                    merged["poni_center_rule_alias"] = rule_alias
                    return merged
        return detector_config
    def _distance_m_for_detector_alias(self, alias: str, detector_config: dict):
        distances = getattr(self, "_detector_distances", {}) or {}
        detector_id = str(detector_config.get("id") or "").strip()
        candidates = [detector_id, str(alias or "").strip()]
        for key in candidates:
            if key and key in distances:
                try:
                    return float(distances[key]) / 100.0
                except (TypeError, ValueError):
                    return None
        distance_by_alias = getattr(self, "_distance_map_by_alias", None)
        if callable(distance_by_alias):
            try:
                mapped = distance_by_alias() or {}
                if alias in mapped:
                    return float(mapped[alias]) / 100.0
            except (TypeError, ValueError):
                return None
        standard = (
            self.config.get("standard_distances", {})
            if hasattr(self, "config") and isinstance(self.config, dict)
            else {}
        )
        if alias in standard:
            try:
                value = float(standard[alias])
                return value / 100.0 if value > 1.0 else value
            except (TypeError, ValueError):
                return None
        return None
    def _prepare_selected_agbh_pyfai_review(self):
        tm = _tm()
        row = self._selected_aux_row_for_pyfai()
        if row is None:
            return None
        row_type = self._aux_row_type(row)
        if row_type != "AGBH":
            tm.QMessageBox.warning(
                self,
                "PyFAI Calibration",
                "Select an AGBH row to launch seeded PyFAI calibration.",
            )
            return False

        file_item = self.auxTable.item(row, self.AUX_COL_FILE)
        source_ref = str(file_item.data(tm.Qt.UserRole) or "").strip() if file_item is not None else ""
        if not source_ref:
            tm.QMessageBox.warning(self, "PyFAI Calibration", "Selected AGBH row has no image source.")
            return False

        alias = self._aux_row_alias(row)
        detector_config = self._detector_config_for_alias(alias)
        distance_m = self._distance_m_for_detector_alias(alias, detector_config)
        if distance_m is None:
            tm.QMessageBox.warning(
                self,
                "PyFAI Calibration",
                f"No configured detector distance for {alias or 'selected detector'}.",
            )
            return False

        try:
            from difra.gui.technical.pyfai_calibration import prepare_agbh_calib2_review

            output_dir = Path(self._current_technical_output_folder()) / "pyfai_seed"
            existing_poni = str((getattr(self, "ponis", {}) or {}).get(alias) or "")
            if not existing_poni:
                existing_poni = str(detector_config.get("default_poni") or "")
            review = prepare_agbh_calib2_review(
                source_image=source_ref,
                detector_config=detector_config,
                distance_m=distance_m,
                alias=alias,
                output_dir=output_dir,
                existing_poni_text=existing_poni,
            )
            self._log_technical_event(
                f"Prepared PyFAI seed for {alias}: {review.poni_path.name}, {review.image_path.name}"
            )
            return review
        except Exception as exc:
            self._log_technical_event(f"Failed to prepare PyFAI seed: {exc}")
            logger.warning("Failed to prepare PyFAI seed", exc_info=True)
            tm.QMessageBox.warning(
                self,
                "PyFAI Calibration",
                f"Could not prepare seeded PyFAI calibration:\n{exc}",
            )
            return False
