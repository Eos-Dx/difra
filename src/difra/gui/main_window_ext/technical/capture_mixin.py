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
from difra.gui.container_api import get_schema

logger = logging.getLogger(__name__)


def _tm():
    from difra.gui.main_window_ext import technical_measurements as tm

    return tm


class TechnicalCaptureMixin:
    TECHNICAL_TYPE_ORDER = {
        "DARK": "001",
        "EMPTY": "002",
        "AGBH": "003",
        "BACKGROUND": "004",
    }

    @staticmethod
    def _format_distance_token_cm(distance_cm) -> str:
        try:
            value = float(distance_cm)
        except (TypeError, ValueError):
            return "unknowncm"
        if abs(value - round(value)) < 1e-6:
            return f"{int(round(value))}cm"
        token = f"{value:.6f}".rstrip("0").rstrip(".")
        token = token.replace("-", "m").replace(".", "p")
        return f"{token}cm"

    def _technical_capture_distance_token(self) -> str:
        active_path = None
        active_getter = getattr(self, "_active_technical_container_path_obj", None)
        if callable(active_getter):
            try:
                active_path = active_getter()
            except Exception:
                active_path = None
        if active_path is None:
            raw_active_path = str(
                getattr(self, "_active_technical_container_path", "") or ""
            ).strip()
            active_path = Path(raw_active_path) if raw_active_path else None
        if active_path is not None:
            try:
                import h5py

                with h5py.File(active_path, "r") as h5f:
                    token = self._format_distance_token_cm(h5f.attrs.get("distance_cm"))
                    if token != "unknowncm":
                        return token
            except Exception:
                logger.debug(
                    "Failed to read active technical container distance from %s",
                    active_path,
                    exc_info=True,
                )

        distances = getattr(self, "_detector_distances", {}) or {}
        for value in distances.values():
            token = self._format_distance_token_cm(value)
            if token != "unknowncm":
                return token
        standard_distances = getattr(self, "config", {}).get("standard_distances", {}) or {}
        if isinstance(standard_distances, dict):
            for value in standard_distances.values():
                token = self._format_distance_token_cm(value)
                if token != "unknowncm":
                    return token
        return "unknowncm"

    def _technical_capture_order_token(self, typ: str, count: int) -> str:
        key = str(typ or "").strip().upper()
        return self.TECHNICAL_TYPE_ORDER.get(key, f"{int(count):03d}")

    def _technical_capture_base_stem(
        self,
        *,
        typ: str,
        count: int,
        timestamp_token: str,
        integration_time_s: float,
        frames: int,
    ) -> str:
        base = self._file_base(typ)
        distance_token = self._technical_capture_distance_token()
        order_token = self._technical_capture_order_token(typ, count)
        time_token = f"{float(integration_time_s):.6f}s"
        return (
            f"{base}_{distance_token}_{order_token}_{timestamp_token}_"
            f"{time_token}_{int(frames)}frames"
        )

    @staticmethod
    def _normalize_technical_alias_candidates(alias: str | None):
        token = str(alias or "").strip().upper()
        if not token:
            return set()
        if token.startswith("PONI_"):
            token = token[5:]
        if not token:
            return set()
        candidates = {token}
        bare = token[4:] if token.startswith("DET_") else token
        if bare:
            candidates.add(bare)
            candidates.add(f"DET_{bare}")
        mapping = {
            "PRIMARY": "SAXS",
            "SAXS": "PRIMARY",
            "SECONDARY": "WAXS",
            "WAXS": "SECONDARY",
        }
        detector_groups = (
            {"PRIMARY", "SAXS", "DET_PRIMARY", "DET_SAXS"},
            {"SECONDARY", "WAXS", "DET_SECONDARY", "DET_WAXS"},
        )
        if bare in mapping:
            candidates.add(mapping[bare])
        for group in detector_groups:
            bare_group = {
                value[4:] if value.startswith("DET_") else value for value in group
            }
            if token in group or bare in bare_group:
                candidates.update(group)
                candidates.update(bare_group)
        return {value for value in candidates if value}

    def _resolve_technical_measurement_poni(
        self,
        *,
        alias: str | None,
        source_ref: str = "",
        source_info: dict | None = None,
    ) -> str | None:
        detector_context = self._read_technical_measurement_container_context(
            source_ref=source_ref,
            source_info=source_info,
        )
        direct_poni_text = str(detector_context.get("poni_text") or "").strip()
        if direct_poni_text:
            return direct_poni_text

        container_path = ""
        raw_source_ref = str(source_ref or "").strip()
        source_payload = source_info if isinstance(source_info, dict) else {}
        if raw_source_ref.startswith("h5ref://"):
            payload = raw_source_ref[len("h5ref://") :]
            container_path = payload.partition("#")[0]
        if not container_path:
            container_path = str(source_payload.get("container_path") or "").strip()
        if not container_path:
            active_getter = getattr(self, "_active_technical_container_path_obj", None)
            if callable(active_getter):
                active_path = active_getter()
                if active_path is not None:
                    container_path = str(active_path)
        if not container_path:
            container_path = str(getattr(self, "_active_technical_container_path", "") or "").strip()
        if not container_path:
            return None

        collect = getattr(self, "_collect_container_poni_text_by_alias", None)
        if not callable(collect):
            return None
        try:
            poni_by_alias = collect(Path(container_path)) or {}
        except Exception:
            logger.debug("Failed to collect PONI from technical container %s", container_path, exc_info=True)
            return None
        alias_candidates = self._normalize_technical_alias_candidates(alias)
        alias_candidates.update(
            self._normalize_technical_alias_candidates(detector_context.get("detector_alias"))
        )
        alias_candidates.update(
            self._normalize_technical_alias_candidates(detector_context.get("detector_id"))
        )
        for key, text in (poni_by_alias or {}).items():
            key_candidates = self._normalize_technical_alias_candidates(key)
            if alias_candidates & key_candidates and str(text or "").strip():
                return str(text).strip()
        return None

    def _read_technical_measurement_container_context(
        self,
        *,
        source_ref: str = "",
        source_info: dict | None = None,
    ) -> dict:
        raw_source_ref = str(source_ref or "").strip()
        source_payload = source_info if isinstance(source_info, dict) else {}
        container_path = ""
        dataset_path = ""

        if raw_source_ref.startswith("h5ref://"):
            payload = raw_source_ref[len("h5ref://") :]
            container_path, _sep, dataset_path = payload.partition("#")

        if not container_path:
            container_path = str(source_payload.get("container_path") or "").strip()
        if not dataset_path:
            dataset_path = str(source_payload.get("dataset_path") or "").strip()

        if not container_path or not dataset_path:
            return {}

        try:
            import h5py
        except Exception:
            logger.debug("h5py unavailable while reading technical measurement context", exc_info=True)
            return {}

        try:
            with h5py.File(container_path, "r") as h5f:
                if dataset_path not in h5f:
                    return {}

                dataset = h5f[dataset_path]
                detector_group = dataset.parent
                schema = get_schema(self.config if hasattr(self, "config") else None)
                context = {
                    "detector_alias": self._decode_technical_h5_attr(
                        detector_group.attrs.get(
                            getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias"),
                            "",
                        )
                    ),
                    "detector_id": self._decode_technical_h5_attr(
                        detector_group.attrs.get(
                            getattr(schema, "ATTR_DETECTOR_ID", "detector_id"),
                            "",
                        )
                    ),
                    "poni_text": "",
                }

                candidate_paths = []

                attr_poni_ref = getattr(schema, "ATTR_PONI_REF", "poni_ref")
                for attr_name in (attr_poni_ref, "poni_path"):
                    ref_path = self._decode_technical_h5_attr(
                        detector_group.attrs.get(attr_name, "")
                    ).strip()
                    if ref_path and ref_path not in candidate_paths:
                        candidate_paths.append(ref_path)

                role_name = str(detector_group.name.rsplit("/", 1)[-1] or "").strip()
                if role_name.startswith("det_"):
                    technical_poni_group = getattr(
                        schema,
                        "GROUP_TECHNICAL_PONI",
                        "/entry/technical/poni",
                    )
                    for suffix in (role_name[4:], role_name):
                        canonical_path = f"{technical_poni_group}/poni_{suffix}"
                        if canonical_path not in candidate_paths:
                            candidate_paths.append(canonical_path)

                if not role_name.startswith("det_"):
                    format_detector_role = getattr(schema, "format_detector_role", None)
                    if callable(format_detector_role):
                        for candidate in (
                            context["detector_alias"],
                            context["detector_id"],
                        ):
                            try:
                                role = str(format_detector_role(candidate) or "").strip()
                            except Exception:
                                role = ""
                            if role.startswith("det_"):
                                technical_poni_group = getattr(
                                    schema,
                                    "GROUP_TECHNICAL_PONI",
                                    "/entry/technical/poni",
                                )
                                for suffix in (role[4:], role):
                                    canonical_path = f"{technical_poni_group}/poni_{suffix}"
                                    if canonical_path not in candidate_paths:
                                        candidate_paths.append(canonical_path)

                for ref_path in candidate_paths:
                    if ref_path and ref_path in h5f:
                        try:
                            value = h5f[ref_path][()]
                            context["poni_text"] = self._decode_technical_h5_attr(value).strip()
                            if context["poni_text"]:
                                context["poni_path"] = ref_path
                                break
                        except Exception:
                            logger.debug(
                                "Failed reading detector-linked technical PONI %s from %s",
                                ref_path,
                                container_path,
                                exc_info=True,
                            )
                return context
        except Exception:
            logger.debug(
                "Failed to read technical measurement context from %s#%s",
                container_path,
                dataset_path,
                exc_info=True,
            )
            return {}

    @staticmethod
    def _decode_technical_h5_attr(value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    def _resolve_technical_measurement_mask(
        self,
        *,
        alias: str | None,
        source_ref: str = "",
        source_info: dict | None = None,
    ):
        masks = getattr(self, "masks", None)
        if not isinstance(masks, dict) or not masks:
            return None

        detector_context = self._read_technical_measurement_container_context(
            source_ref=source_ref,
            source_info=source_info,
        )
        alias_candidates = []
        for candidate in (
            alias,
            detector_context.get("detector_alias"),
            detector_context.get("detector_id"),
        ):
            for normalized in sorted(self._normalize_technical_alias_candidates(candidate)):
                if normalized not in alias_candidates:
                    alias_candidates.append(normalized)

        for key in alias_candidates:
            if key in masks:
                return masks.get(key)
        return masks.get(alias)

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def _build_windows_conda_pyfai_command(*, env: str, command=None) -> str:
        target_env = str(env or "").strip()
        args = [str(arg) for arg in (command or ["pyfai-calib2"])]
        if args == ["pyfai-calib2"]:
            return f"conda run --no-capture-output -n {target_env} pyfai-calib2"
        quoted = " ".join(TechnicalCaptureMixin._ps_quote(arg) for arg in args)
        return (
            "conda run --no-capture-output "
            f"-n {TechnicalCaptureMixin._ps_quote(target_env)} {quoted}"
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
        command_line = TechnicalCaptureMixin._build_windows_conda_pyfai_command(
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

    def _resolve_auto_poni_pyfai_calib2_env(self) -> str:
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        auto_cfg = cfg.get("auto_poni_calibration", {})
        if isinstance(auto_cfg, dict):
            explicit = str(auto_cfg.get("pyfai_calib2_env") or "").strip()
            if explicit:
                return explicit

        env_explicit = str(os.environ.get("DIFRA_PYFAI_CALIB2_ENV") or "").strip()
        if env_explicit:
            return env_explicit

        sidecar_env = str(os.environ.get("SIDECAR_ENV") or os.environ.get("DIFRA_SIDECAR_ENV") or "").strip()
        if sidecar_env:
            return sidecar_env

        return self._resolve_pyfai_conda_env()

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

    def _auto_poni_config(self) -> dict:
        from difra.gui.technical.pyfai_calibration import normalized_auto_poni_config

        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        return normalized_auto_poni_config(cfg)

    def _auto_poni_rule_alias(self, alias: str) -> str:
        try:
            from difra.gui.main_window_ext.technical.poni_center_validation import (
                resolve_poni_rule_alias,
            )

            detector_cfgs = self.config.get("detectors", []) if hasattr(self, "config") else []
            return resolve_poni_rule_alias(alias, detector_cfgs)
        except Exception:
            return str(alias or "").strip().upper()

    @staticmethod
    def _auto_poni_distance_key(distance_cm) -> str:
        try:
            value = float(distance_cm)
        except (TypeError, ValueError):
            return ""
        rounded = round(value)
        if abs(value - rounded) <= 0.25:
            return str(int(rounded))
        return f"{value:.3f}".rstrip("0").rstrip(".")

    def _active_technical_container_distance_cm_for_auto_poni(self):
        active_path = None
        active_getter = getattr(self, "_active_technical_container_path_obj", None)
        if callable(active_getter):
            try:
                active_path = active_getter()
            except Exception:
                active_path = None
        if active_path is None:
            raw_path = str(
                getattr(self, "_active_technical_container_path", "") or ""
            ).strip()
            active_path = Path(raw_path) if raw_path else None
        if active_path is None:
            return None

        reader = getattr(self, "_read_technical_container_distance_cm", None)
        if callable(reader):
            try:
                value = reader(Path(active_path))
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                return None
            except Exception:
                logger.debug(
                    "Failed to read Auto PONI container distance",
                    exc_info=True,
                )

        try:
            import h5py

            with h5py.File(active_path, "r") as h5f:
                value = h5f.attrs.get("distance_cm")
                return None if value is None else float(value)
        except Exception:
            logger.debug("Failed to read Auto PONI root distance", exc_info=True)
            return None

    def _auto_poni_default_distance_cm_by_alias(self, aliases) -> dict:
        distance_cm = self._active_technical_container_distance_cm_for_auto_poni()
        if distance_cm is not None:
            return {
                str(alias).strip().upper(): float(distance_cm)
                for alias in aliases
            }

        result = {}
        for alias in aliases:
            alias_key = str(alias or "").strip().upper()
            detector_config = self._detector_config_for_alias(alias)
            distance_m = self._distance_m_for_detector_alias(alias, detector_config)
            if distance_m is not None:
                result[alias_key] = float(distance_m) * 100.0
        return result

    def _auto_poni_default_first_visible_ring(
        self,
        *,
        alias: str,
        distance_cm,
        auto_cfg: dict,
    ) -> int:
        alias_key = str(alias or "").strip().upper()
        rule_key = str(self._auto_poni_rule_alias(alias) or "").strip().upper()
        by_distance = auto_cfg.get("first_visible_ring_by_distance_cm", {})
        distance_key = self._auto_poni_distance_key(distance_cm)
        if isinstance(by_distance, dict):
            distance_rules = by_distance.get(distance_key, {})
            if isinstance(distance_rules, dict):
                for key in (alias_key, rule_key):
                    try:
                        ring = int(distance_rules.get(key))
                    except (TypeError, ValueError):
                        continue
                    if ring > 0:
                        return ring

        configured = auto_cfg.get("first_visible_ring_by_alias", {})
        if isinstance(configured, dict):
            for key in (alias_key, rule_key):
                try:
                    ring = int(configured.get(key))
                except (TypeError, ValueError):
                    continue
                if ring > 0:
                    return ring
        return 1

    def _auto_poni_default_rings_to_search(
        self,
        *,
        alias: str,
        distance_cm,
        auto_cfg: dict,
    ) -> int:
        alias_key = str(alias or "").strip().upper()
        rule_key = str(self._auto_poni_rule_alias(alias) or "").strip().upper()
        by_distance = auto_cfg.get("rings_to_search_by_distance_cm", {})
        distance_key = self._auto_poni_distance_key(distance_cm)
        if isinstance(by_distance, dict):
            distance_rules = by_distance.get(distance_key, {})
            if isinstance(distance_rules, dict):
                for key in (alias_key, rule_key):
                    try:
                        count = int(distance_rules.get(key))
                    except (TypeError, ValueError):
                        continue
                    if count > 0:
                        return count

        configured = auto_cfg.get("rings_to_search_by_alias", {})
        if isinstance(configured, dict):
            for key in (alias_key, rule_key):
                try:
                    count = int(configured.get(key))
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    return count
        try:
            return max(1, int(auto_cfg.get("rings_to_show", 3)))
        except (TypeError, ValueError):
            return 3

    def _auto_poni_default_settings(self, auto_cfg: dict, aliases) -> dict:
        distance_by_alias = self._auto_poni_default_distance_cm_by_alias(aliases)
        first_visible = {}
        rings_to_search = {}
        for alias in aliases:
            alias_key = str(alias or "").strip().upper()
            distance_cm = distance_by_alias.get(alias_key)
            first_visible[alias_key] = self._auto_poni_default_first_visible_ring(
                alias=alias,
                distance_cm=distance_cm,
                auto_cfg=auto_cfg,
            )
            rings_to_search[alias_key] = self._auto_poni_default_rings_to_search(
                alias=alias,
                distance_cm=distance_cm,
                auto_cfg=auto_cfg,
            )
        return {
            "distance_cm_by_alias": distance_by_alias,
            "first_visible_ring_by_alias": first_visible,
            "rings_to_search_by_alias": rings_to_search,
            "energy_kev": float(auto_cfg.get("energy_kev", 8.04) or 8.04),
        }

    @staticmethod
    def _auto_poni_float_or_none(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _auto_poni_center_px_from_validation_config(
        self,
        alias: str,
        detector_config: dict,
    ):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return None

        detector_rules = validation_cfg.get("detectors", {})
        if not isinstance(detector_rules, dict):
            return None

        try:
            from difra.gui.main_window_ext.technical.poni_center_validation import (
                resolve_poni_rule_alias,
            )

            rule_alias = resolve_poni_rule_alias(alias, cfg.get("detectors", []))
        except Exception:
            rule_alias = str(alias or "").strip().upper()
        rule_alias = str(rule_alias or "").strip().upper()

        rule = {}
        defaults = validation_cfg.get("defaults", {})
        if isinstance(defaults, dict):
            rule.update(defaults)
        for key, value in detector_rules.items():
            if str(key or "").strip().upper() == rule_alias and isinstance(value, dict):
                rule.update(value)
                break
        if not rule:
            return None

        size_cfg = detector_config.get("size", {}) if isinstance(detector_config, dict) else {}
        if isinstance(size_cfg, dict):
            width = self._auto_poni_float_or_none(size_cfg.get("width")) or 256.0
            height = self._auto_poni_float_or_none(size_cfg.get("height")) or 256.0
        else:
            width = height = 256.0

        row = self._auto_poni_float_or_none(rule.get("row_target_px"))
        if row is None:
            row = height / 2.0

        col = self._auto_poni_float_or_none(rule.get("col_target_px"))
        col_min = self._auto_poni_float_or_none(rule.get("col_min_px"))
        col_max = self._auto_poni_float_or_none(rule.get("col_max_px"))
        col_gt = self._auto_poni_float_or_none(rule.get("col_gt_px"))
        col_lt = self._auto_poni_float_or_none(rule.get("col_lt_px"))

        if col is None:
            if col_gt is not None and col_max is not None and col_max > col_gt:
                col = col_max
            elif col_min is not None and col_lt is not None and col_lt > col_min:
                col = col_min
            elif col_gt is not None and col_lt is not None and col_lt > col_gt:
                col = (col_gt + col_lt) / 2.0
            elif col_gt is not None:
                col = col_gt + 1.0
            elif col_min is not None and col_max is not None and col_max >= col_min:
                col = (col_min + col_max) / 2.0
            elif col_min is not None:
                col = col_min
            elif col_lt is not None:
                col = col_lt - 1.0
            elif col_max is not None:
                col = col_max
            else:
                col = width / 2.0

        if col_gt is not None and not (col > col_gt):
            col = col_gt + 1.0
        if col_min is not None and col < col_min:
            col = col_min
        if col_lt is not None and not (col < col_lt):
            col = col_lt - 1.0
        if col_max is not None and col > col_max:
            col = col_max

        return float(row), float(col)

    def _confirm_auto_poni_config(self, auto_cfg: dict) -> bool:
        tm = _tm()
        first_visible = auto_cfg.get("first_visible_ring_by_alias", {})
        primary_ring = int(first_visible.get("PRIMARY", 3) or 3)
        secondary_ring = int(first_visible.get("SECONDARY", 5) or 5)
        reply = tm.QMessageBox.question(
            self,
            "Auto PONI",
            "Auto PONI uses first visible AgBh ring indexes from global config.\n\n"
            f"PRIMARY: ring {primary_ring} (rings 1-2 can be hidden by beam stop)\n"
            f"SECONDARY: ring {secondary_ring}\n\n"
            "Config key:\n"
            "auto_poni_calibration.first_visible_ring_by_alias\n\n"
            "Continue automatic PONI generation?",
            tm.QMessageBox.Yes | tm.QMessageBox.No,
            tm.QMessageBox.Yes,
        )
        return reply == tm.QMessageBox.Yes

    def _prompt_auto_poni_settings(self, auto_cfg: dict, aliases):
        defaults = self._auto_poni_default_settings(auto_cfg, aliases)
        sources = {
            str(alias or "").strip().upper(): str(source or "")
            for alias, source in (
                getattr(self, "_pending_auto_poni_sources", {}) or {}
            ).items()
        }
        tm = _tm()
        try:
            from PyQt5.QtWidgets import QDialogButtonBox
        except Exception:
            if self._confirm_auto_poni_config(auto_cfg):
                return defaults
            return None

        try:
            dialog = tm.QDialog(self)
            dialog.setWindowTitle("DIFRA Auto PONI setup")
            dialog.setModal(True)
            dialog.resize(980, 620)
            layout = tm.QVBoxLayout(dialog)
            note = tm.QLabel(dialog)
            note.setWordWrap(True)
            container_distance = (
                self._active_technical_container_distance_cm_for_auto_poni()
            )
            if container_distance is None:
                note.setText(
                    "Container distance not found. "
                    "Set distance and visible rings."
                )
            else:
                note.setText(
                    f"Container distance: {float(container_distance):.3f} cm. "
                    "Adjust if needed."
                )
            layout.addWidget(note)

            controls = {}
            energy_row = tm.QHBoxLayout()
            energy_row.addWidget(tm.QLabel("Energy", dialog))
            energy_spin = tm.QDoubleSpinBox(dialog)
            energy_spin.setRange(0.001, 1000.0)
            energy_spin.setDecimals(4)
            energy_spin.setSuffix(" keV")
            energy_spin.setValue(float(defaults.get("energy_kev", 8.04) or 8.04))
            energy_row.addWidget(energy_spin)
            layout.addLayout(energy_row)

            def _pixel_pair(detector_config):
                pixel_cfg = detector_config.get("pixel_size_um", [50, 50])
                if not isinstance(pixel_cfg, (list, tuple)):
                    pixel_cfg = [pixel_cfg, pixel_cfg]
                first = pixel_cfg[0] if len(pixel_cfg) >= 1 else 50
                second = pixel_cfg[1] if len(pixel_cfg) >= 2 else first
                return float(first), float(second)

            for alias in aliases:
                alias_key = str(alias or "").strip().upper()
                detector_config = self._auto_poni_detector_config_for_alias(alias)
                center_px = self._auto_poni_center_px_from_validation_config(
                    alias,
                    detector_config,
                )
                if center_px is None:
                    center_px = self._auto_poni_center_px_for_alias(alias, detector_config)
                if center_px is None:
                    center_px = (128.0, 128.0)
                width, height = 256, 256
                size_cfg = detector_config.get("size", {})
                if isinstance(size_cfg, dict):
                    width = int(size_cfg.get("width", width) or width)
                    height = int(size_cfg.get("height", height) or height)
                pixel1, pixel2 = _pixel_pair(detector_config)

                group = tm.QGroupBox(alias_key, dialog)
                form = tm.QFormLayout(group)

                file_row = tm.QHBoxLayout()
                file_edit = tm.QLineEdit(sources.get(alias_key, ""), group)
                browse_btn = tm.QPushButton("Browse", group)
                file_row.addWidget(file_edit)
                file_row.addWidget(browse_btn)
                form.addRow("AGBH file", file_row)

                distance_spin = tm.QDoubleSpinBox(dialog)
                distance_spin.setRange(0.01, 100000.0)
                distance_spin.setDecimals(3)
                distance_spin.setSuffix(" cm")
                distance_spin.setValue(
                    float(
                        defaults["distance_cm_by_alias"].get(
                            alias_key,
                            17.0,
                        )
                    )
                )
                form.addRow("Distance", distance_spin)

                ring_spin = tm.QSpinBox(dialog)
                ring_spin.setRange(1, 99)
                ring_spin.setValue(
                    int(
                        defaults["first_visible_ring_by_alias"].get(
                            alias_key,
                            1,
                        )
                    )
                )
                form.addRow("First visible ring", ring_spin)

                rings_spin = tm.QSpinBox(dialog)
                rings_spin.setRange(1, 99)
                rings_spin.setValue(
                    int(
                        defaults["rings_to_search_by_alias"].get(
                            alias_key,
                            3,
                        )
                    )
                )
                form.addRow("Rings to search", rings_spin)

                center_row = tm.QHBoxLayout()
                center_r = tm.QDoubleSpinBox(dialog)
                center_r.setRange(-100000.0, 100000.0)
                center_r.setDecimals(3)
                center_r.setValue(float(center_px[0]))
                center_c = tm.QDoubleSpinBox(dialog)
                center_c.setRange(-100000.0, 100000.0)
                center_c.setDecimals(3)
                center_c.setValue(float(center_px[1]))
                center_row.addWidget(tm.QLabel("row", dialog))
                center_row.addWidget(center_r)
                center_row.addWidget(tm.QLabel("col", dialog))
                center_row.addWidget(center_c)
                form.addRow("Expected center", center_row)

                size_row = tm.QHBoxLayout()
                width_spin = tm.QSpinBox(dialog)
                width_spin.setRange(1, 100000)
                width_spin.setValue(int(width))
                height_spin = tm.QSpinBox(dialog)
                height_spin.setRange(1, 100000)
                height_spin.setValue(int(height))
                size_row.addWidget(tm.QLabel("w", dialog))
                size_row.addWidget(width_spin)
                size_row.addWidget(tm.QLabel("h", dialog))
                size_row.addWidget(height_spin)
                form.addRow("Image/detector size", size_row)

                pixel_row = tm.QHBoxLayout()
                pixel1_spin = tm.QDoubleSpinBox(dialog)
                pixel1_spin.setRange(0.001, 100000.0)
                pixel1_spin.setDecimals(3)
                pixel1_spin.setSuffix(" um")
                pixel1_spin.setValue(float(pixel1))
                pixel2_spin = tm.QDoubleSpinBox(dialog)
                pixel2_spin.setRange(0.001, 100000.0)
                pixel2_spin.setDecimals(3)
                pixel2_spin.setSuffix(" um")
                pixel2_spin.setValue(float(pixel2))
                pixel_row.addWidget(tm.QLabel("p1", dialog))
                pixel_row.addWidget(pixel1_spin)
                pixel_row.addWidget(tm.QLabel("p2", dialog))
                pixel_row.addWidget(pixel2_spin)
                form.addRow("Pixel size", pixel_row)

                layout.addWidget(group)
                controls[alias_key] = {
                    "file": file_edit,
                    "distance": distance_spin,
                    "ring": ring_spin,
                    "rings": rings_spin,
                    "center_r": center_r,
                    "center_c": center_c,
                    "width": width_spin,
                    "height": height_spin,
                    "pixel1": pixel1_spin,
                    "pixel2": pixel2_spin,
                    "detector_config": detector_config,
                }

                def _browse(_checked=False, *, edit=file_edit):
                    current = str(edit.text() or "").strip()
                    start = str(Path(current).parent) if current else str(Path.cwd())
                    path, _ = tm.QFileDialog.getOpenFileName(
                        dialog,
                        "Select AGBH file",
                        start,
                        "Images (*.npy *.tif *.tiff *.txt *.csv);;All (*)",
                    )
                    if path:
                        edit.setText(path)

                browse_btn.clicked.connect(_browse)

                def _sync_defaults(
                    _value,
                    *,
                    key=alias_key,
                    ring_control=ring_spin,
                    rings_control=rings_spin,
                ):
                    ring_control.setValue(
                        self._auto_poni_default_first_visible_ring(
                            alias=key,
                            distance_cm=controls[key]["distance"].value(),
                            auto_cfg=auto_cfg,
                        )
                    )
                    rings_control.setValue(
                        self._auto_poni_default_rings_to_search(
                            alias=key,
                            distance_cm=controls[key]["distance"].value(),
                            auto_cfg=auto_cfg,
                        )
                    )

                distance_spin.valueChanged.connect(_sync_defaults)

            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                dialog,
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)

            result = dialog.exec_()
            if result != tm.QDialog.Accepted:
                return None

            distance_by_alias = {}
            first_visible = {}
            rings_to_search = {}
            sources_by_alias = {}
            detector_config_by_alias = {}
            center_px_by_alias = {}
            for alias_key, control in controls.items():
                distance_by_alias[alias_key] = float(control["distance"].value())
                first_visible[alias_key] = int(control["ring"].value())
                rings_to_search[alias_key] = int(control["rings"].value())
                sources_by_alias[alias_key] = str(control["file"].text() or "").strip()
                detector_config = dict(control["detector_config"])
                detector_config["alias"] = alias_key
                detector_config["size"] = {
                    "width": int(control["width"].value()),
                    "height": int(control["height"].value()),
                }
                detector_config["pixel_size_um"] = [
                    float(control["pixel1"].value()),
                    float(control["pixel2"].value()),
                ]
                detector_config_by_alias[alias_key] = detector_config
                center_px_by_alias[alias_key] = (
                    float(control["center_r"].value()),
                    float(control["center_c"].value()),
                )
            return {
                "sources_by_alias": sources_by_alias,
                "distance_cm_by_alias": distance_by_alias,
                "first_visible_ring_by_alias": first_visible,
                "rings_to_search_by_alias": rings_to_search,
                "detector_config_by_alias": detector_config_by_alias,
                "center_px_by_alias": center_px_by_alias,
                "energy_kev": float(energy_spin.value()),
            }
        except Exception:
            logger.warning("Failed to show Auto PONI settings dialog", exc_info=True)
            if self._confirm_auto_poni_config(auto_cfg):
                return defaults
            return None

    def _auto_poni_center_px_for_alias(self, alias: str, detector_config: dict):
        configured_center = self._auto_poni_center_px_from_validation_config(
            alias,
            detector_config,
        )
        if configured_center is not None:
            return configured_center

        existing_poni = str((getattr(self, "ponis", {}) or {}).get(alias) or "")
        if existing_poni:
            return None
        if str(detector_config.get("default_poni") or "").strip():
            return None

        resolver = getattr(self, "_resolve_fake_demo_center_px", None)
        if not callable(resolver):
            return None
        size_cfg = detector_config.get("size", {})
        if isinstance(size_cfg, dict):
            size = (size_cfg.get("width", 256), size_cfg.get("height", 256))
        else:
            size = (256, 256)
        try:
            return resolver(alias, size)
        except Exception:
            logger.debug("Failed to resolve auto PONI center for %s", alias, exc_info=True)
            return None

    def _collect_auto_poni_agbh_sources(self) -> dict:
        tm = _tm()
        sources = {}
        if not hasattr(self, "auxTable") or self.auxTable is None:
            return sources

        for row in range(self.auxTable.rowCount()):
            if self._aux_row_type(row) != "AGBH":
                continue
            alias = self._aux_row_alias(row)
            if not alias:
                continue
            file_item = self.auxTable.item(row, self.AUX_COL_FILE)
            source_ref = str(file_item.data(tm.Qt.UserRole) or "").strip() if file_item is not None else ""
            if not source_ref:
                continue
            sources.setdefault(alias, source_ref)
        return sources

    @staticmethod
    def _auto_poni_output_path_for_source(source_path, fallback_poni_path):
        fallback = Path(fallback_poni_path)
        try:
            source = Path(source_path) if source_path else None
        except (TypeError, ValueError):
            source = None
        if source is not None and str(source).strip():
            return fallback.parent / f"{source.stem}.poni"
        return fallback

    def _autopony_output_dir(self) -> Path:
        return Path(self._current_technical_output_folder()) / "autopony"

    def _reset_autopony_output_dir(self) -> Path:
        output_dir = self._autopony_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        for child in output_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        return output_dir

    def _auto_poni_source_path_from_h5ref(self, source_ref: str):
        parser = getattr(self, "_parse_h5ref", None)
        if callable(parser):
            container_path, dataset_path = parser(source_ref)
        else:
            raw = str(source_ref or "")
            payload = raw[len("h5ref://") :] if raw.startswith("h5ref://") else ""
            container_path, sep, dataset_path = payload.partition("#")
            if not sep:
                container_path, dataset_path = None, None
        if not container_path or not dataset_path:
            return None
        try:
            import h5py

            with h5py.File(container_path, "r") as h5f:
                if dataset_path not in h5f:
                    return None
                obj = h5f[dataset_path]
                candidates = []
                for item in (obj, getattr(obj, "parent", None)):
                    if item is None:
                        continue
                    attrs = getattr(item, "attrs", {})
                    for key in ("source_file", "source_path", "source_ref"):
                        value = attrs.get(key)
                        if isinstance(value, bytes):
                            value = value.decode("utf-8", errors="replace")
                        if value:
                            candidates.append(str(value))
                for value in candidates:
                    if value.startswith("h5ref://"):
                        continue
                    path = Path(value)
                    if path.is_absolute():
                        return path
                    return Path(container_path).parent / path
        except Exception:
            logger.debug("Failed to resolve Auto PONI h5ref source path", exc_info=True)
        return None

    def _auto_poni_output_dir_for_source(self, source_ref: str):
        text = str(source_ref or "").strip()
        if text.startswith("h5ref://"):
            source_path = self._auto_poni_source_path_from_h5ref(text)
            if source_path is not None:
                return self._autopony_output_dir(), source_path
            parser = getattr(self, "_parse_h5ref", None)
            if callable(parser):
                container_path, _dataset_path = parser(text)
                if container_path:
                    return self._autopony_output_dir(), None
            payload = text[len("h5ref://") :]
            container_path, sep, _dataset_path = payload.partition("#")
            if sep and container_path:
                return self._autopony_output_dir(), None
            return self._autopony_output_dir(), None
        source_path = Path(text)
        return self._autopony_output_dir(), source_path

    def _prepare_auto_poni_reviews(
        self,
        auto_cfg: dict,
        *,
        sources: dict | None = None,
        distance_cm_by_alias: dict | None = None,
        first_visible_ring_by_alias: dict | None = None,
        rings_to_search_by_alias: dict | None = None,
        detector_config_by_alias: dict | None = None,
        center_px_by_alias: dict | None = None,
    ):
        tm = _tm()
        sources = (
            sources
            if isinstance(sources, dict)
            else self._collect_auto_poni_agbh_sources()
        )
        if not sources:
            tm.QMessageBox.warning(
                self,
                "Auto PONI",
                "No AGBH rows found. Measure or load AGBH NumPy images first.",
            )
            return False

        try:
            from difra.gui.technical.pyfai_calibration import (
                build_pyfai_calib2_command,
                energy_kev_to_wavelength_m,
                is_headless_agbh_fit_plausible,
                load_calibration_array,
                prepare_agbh_calib2_review,
                run_headless_agbh_fit,
            )
        except Exception as exc:
            tm.QMessageBox.warning(
                self,
                "Auto PONI",
                f"Auto PONI helpers are unavailable:\n{exc}",
            )
            return False

        reviews = {}
        images = {}
        detector_configs = {}
        missing = []
        output_dir = self._reset_autopony_output_dir()

        for alias, source_ref in sorted(sources.items()):
            alias_key = str(alias or "").strip().upper()
            detector_config = (
                dict(detector_config_by_alias.get(alias_key, {}))
                if isinstance(detector_config_by_alias, dict)
                and isinstance(detector_config_by_alias.get(alias_key), dict)
                else self._auto_poni_detector_config_for_alias(alias)
            )
            detector_configs[alias] = detector_config
            first_visible_ring = None
            if isinstance(first_visible_ring_by_alias, dict):
                try:
                    first_visible_ring = int(first_visible_ring_by_alias.get(alias_key))
                except (TypeError, ValueError):
                    first_visible_ring = None
            try:
                rings_to_search = int(
                    (rings_to_search_by_alias or {}).get(
                        alias_key,
                        auto_cfg.get("rings_to_show", 3),
                    )
                )
            except (TypeError, ValueError):
                rings_to_search = int(auto_cfg.get("rings_to_show", 3) or 3)
            rings_to_search = max(1, rings_to_search)
            distance_cm = (
                (distance_cm_by_alias or {}).get(alias_key)
                if isinstance(distance_cm_by_alias, dict)
                else None
            )
            if distance_cm is None:
                distance_m = self._distance_m_for_detector_alias(alias, detector_config)
            else:
                try:
                    distance_m = float(distance_cm) / 100.0
                except (TypeError, ValueError):
                    distance_m = None
            if distance_m is None:
                missing.append(f"{alias}: distance")
                continue
            center_px = None
            if isinstance(center_px_by_alias, dict):
                center_px = center_px_by_alias.get(alias_key)
                if isinstance(center_px, (list, tuple)) and len(center_px) >= 2:
                    center_px = (float(center_px[0]), float(center_px[1]))
                else:
                    center_px = None
            if center_px is None:
                center_px = self._auto_poni_center_px_for_alias(alias, detector_config)

            existing_poni = str((getattr(self, "ponis", {}) or {}).get(alias) or "")
            if not existing_poni:
                existing_poni = str(detector_config.get("default_poni") or "")

            try:
                _, source_path = self._auto_poni_output_dir_for_source(source_ref)
                wavelength_m = energy_kev_to_wavelength_m(
                    float(auto_cfg.get("energy_kev", 8.04) or 8.04)
                )
                review = prepare_agbh_calib2_review(
                    source_image=source_ref,
                    detector_config=detector_config,
                    distance_m=distance_m,
                    alias=alias,
                    output_dir=output_dir,
                    existing_poni_text=existing_poni,
                    wavelength_m=wavelength_m,
                    calibrant=str(auto_cfg.get("calibrant") or "AgBh"),
                    center_px=center_px,
                    first_visible_ring=first_visible_ring,
                    rings_to_show=rings_to_search,
                )
                if source_path is not None:
                    review = type(review)(
                        image_path=review.image_path,
                        poni_path=review.poni_path,
                        command=review.command,
                        poni_text=review.poni_text,
                        source_path=source_path,
                    )
                if first_visible_ring is not None:
                    try:
                        fit_result = run_headless_agbh_fit(
                            source_image=source_ref,
                            detector_config=detector_config,
                            distance_m=distance_m,
                            output_dir=output_dir,
                            alias=alias,
                            center_px=center_px,
                            wavelength_m=wavelength_m,
                            calibrant=str(auto_cfg.get("calibrant") or "AgBh"),
                            first_visible_ring=first_visible_ring,
                            rings_to_show=rings_to_search,
                        )
                    except Exception as fit_exc:
                        self._log_technical_event(
                            f"Auto PONI headless fit failed for {alias}: {fit_exc}"
                        )
                    else:
                        if is_headless_agbh_fit_plausible(
                            fit_result,
                            seed_poni_text=review.poni_text,
                            detector_config=detector_config,
                        ):
                            command = build_pyfai_calib2_command(
                                image_path=review.image_path,
                                poni_text=fit_result.poni_text,
                                detector_config=detector_config,
                                calibrant=str(auto_cfg.get("calibrant") or "AgBh"),
                            )
                            command = [
                                *command[:-1],
                                "-n",
                                str(fit_result.npt_path),
                                command[-1],
                            ]
                            review = type(review)(
                                image_path=review.image_path,
                                poni_path=fit_result.poni_path,
                                command=command,
                                poni_text=fit_result.poni_text,
                                source_path=source_path or getattr(review, "source_path", None),
                            )
                            self._log_technical_event(
                                "Auto PONI headless fit "
                                f"{alias}: points={fit_result.extracted_points}, "
                                f"chi2={fit_result.chi2}"
                            )
                        else:
                            from difra.gui.technical.pyfai_calibration import (
                                parse_poni_parameters,
                            )

                            seed_params = parse_poni_parameters(review.poni_text)
                            fit_params = parse_poni_parameters(fit_result.poni_text)
                            self._log_technical_event(
                                "Auto PONI headless fit rejected "
                                f"{alias}: points={fit_result.extracted_points}, "
                                f"chi2={fit_result.chi2}, "
                                f"seed_dist={seed_params.get('Distance')}, "
                                f"fit_dist={fit_params.get('Distance')}"
                            )
                reviews[alias] = review
                images[alias] = load_calibration_array(source_ref)
            except Exception as exc:
                missing.append(f"{alias}: {exc}")

        if missing:
            tm.QMessageBox.warning(
                self,
                "Auto PONI",
                "Could not prepare Auto PONI for:\n\n" + "\n".join(missing),
            )

        if not reviews:
            return False
        return {
            "reviews": reviews,
            "images": images,
            "detector_configs": detector_configs,
        }

    def _first_visible_rings_for_auto_poni(self, aliases, auto_cfg: dict) -> dict:
        configured = auto_cfg.get("first_visible_ring_by_alias", {})
        result = {}
        for alias in aliases:
            rule_alias = self._auto_poni_rule_alias(alias)
            alias_key = str(alias or "").strip().upper()
            rule_key = str(rule_alias or "").strip().upper()
            try:
                value = configured.get(alias_key, configured.get(rule_key, 1))
                ring = int(value)
            except (TypeError, ValueError):
                ring = 1
            result[alias_key] = max(1, ring)
        return result

    def _launch_pyfai_reviews(self, reviews: dict) -> bool:
        env = self._resolve_auto_poni_pyfai_calib2_env()
        if not env:
            _tm().QMessageBox.warning(self, "Auto PONI", "No conda env configured for pyFAI.")
            return False
        try:
            from difra.gui.technical.pyfai_calibration import (
                write_pyfai_calib2_launcher,
            )
        except Exception:
            write_pyfai_calib2_launcher = None

        commands = []
        for alias, review in reviews.items():
            command = list(review.command)
            if (
                "DIFRA-256-50UM" in command
                and callable(write_pyfai_calib2_launcher)
            ):
                launcher = write_pyfai_calib2_launcher(
                    output_dir=Path(review.image_path).parent,
                    command=command,
                    launcher_stem=f"run_pyfai_calib2_{alias}",
                )
                command = ["python", str(launcher)]
            commands.append(command)
        if not commands:
            return False
        folder = Path(next(iter(reviews.values())).image_path).parent

        try:
            if os.name == "nt":
                command_lines = [
                    self._build_windows_conda_pyfai_command(env=env, command=cmd)
                    for cmd in commands
                ]
                script = "\n".join(
                    [
                        "$ErrorActionPreference = 'Stop'",
                        f"Set-Location {self._ps_quote(str(folder))}",
                        *command_lines,
                        "",
                    ]
                )
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".ps1", delete=False, encoding="utf-8"
                ) as handle:
                    handle.write(script)
                    script_path = handle.name
                start_cmd = (
                    f'Start-Process powershell '
                    f'-ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "{script_path}"'
                )
                subprocess.Popen(["powershell", "-NoProfile", "-Command", start_cmd])
            else:
                command_lines = [
                    self._build_posix_conda_pyfai_command(env=env, command=cmd)
                    for cmd in commands
                ]
                script = "\n".join(
                    [
                        "#!/bin/bash",
                        f"cd {shlex.quote(str(folder))}",
                        *command_lines,
                        "",
                    ]
                )
                with tempfile.NamedTemporaryFile(mode="w", suffix=".command", delete=False) as handle:
                    handle.write(script)
                    script_path = handle.name
                os.chmod(script_path, 0o755)
                if sys.platform == "darwin":
                    subprocess.Popen(["open", "-a", "Terminal", script_path])
                else:
                    subprocess.Popen(["bash", script_path])
            self._log_technical_event(f"Auto PONI correction launched for {len(commands)} detector(s)")
            return True
        except Exception as exc:
            logger.warning("Failed to launch Auto PONI correction", exc_info=True)
            _tm().QMessageBox.warning(self, "Auto PONI", f"Could not launch pyFAI:\n{exc}")
            return False

    def _validate_auto_poni_reviews(self, reviews: dict) -> bool:
        tm = _tm()
        if not isinstance(getattr(self, "ponis", None), dict):
            self.ponis = {}
        if not isinstance(getattr(self, "poni_files", None), dict):
            self.poni_files = {}

        active_path = None
        active_getter = getattr(self, "_active_technical_container_path_obj", None)
        if callable(active_getter):
            try:
                active_path = active_getter()
            except Exception:
                active_path = None
        if active_path is None or not Path(active_path).exists():
            self._log_technical_event("Auto PONI validate ignored: no active technical container")
            return False

        try:
            from difra.gui.container_api import get_container_manager

            manager = get_container_manager(self.config if hasattr(self, "config") else None)
            if manager.is_container_locked(Path(active_path)):
                self._log_technical_event(
                    f"Auto PONI validate ignored: active container is locked ({Path(active_path).name})"
                )
                app = tm.QApplication.instance() if hasattr(tm, "QApplication") else None
                if app is not None:
                    widget_cls = getattr(tm, "QWidget", None)
                    parent = self if widget_cls is not None and isinstance(self, widget_cls) else None
                    tm.QMessageBox.warning(
                        parent,
                        "Auto PONI",
                        "Active technical container is locked. PONI files were not moved or updated in the container.",
                    )
                return False
        except Exception:
            logger.warning("Failed to check active technical container lock state", exc_info=True)
            return False

        for alias, review in reviews.items():
            poni_text = str(review.poni_text or "")
            autopony_path = self._auto_poni_output_path_for_source(
                getattr(review, "source_path", None),
                getattr(review, "poni_path", ""),
            )
            autopony_path.parent.mkdir(parents=True, exist_ok=True)
            autopony_path.write_text(poni_text, encoding="utf-8")
            target_path = autopony_path.parent.parent / autopony_path.name
            if target_path.exists():
                target_path.unlink()
            shutil.move(str(autopony_path), str(target_path))
            self.ponis[alias] = poni_text
            self.poni_files[alias] = {
                "path": str(target_path),
                "name": target_path.name,
            }

        sync_fn = getattr(self, "_sync_active_technical_container_from_table", None)
        if callable(sync_fn):
            synced = bool(sync_fn(show_errors=True))
            if not synced:
                self._log_technical_event("Auto PONI validated, but container sync failed")
                tm.QMessageBox.warning(
                    self,
                    "Auto PONI",
                    "Generated PONI files were saved, but could not be synced into an unlocked technical container.",
                )
                return False

        self._log_technical_event(f"Auto PONI validated for {len(reviews)} detector(s)")
        app = tm.QApplication.instance() if hasattr(tm, "QApplication") else None
        if app is not None:
            widget_cls = getattr(tm, "QWidget", None)
            parent = self if widget_cls is not None and isinstance(self, widget_cls) else None
            tm.QMessageBox.information(
                parent,
                "Auto PONI",
                "Generated PONI files moved next to the technical container and synced to it.",
            )
        return True

    def run_auto_poni(self):
        auto_cfg = self._auto_poni_config()
        sources = self._collect_auto_poni_agbh_sources()
        if not sources:
            _tm().QMessageBox.warning(
                self,
                "Auto PONI",
                "No AGBH rows found. Measure or load AGBH NumPy images first.",
            )
            return False
        aliases = sorted(sources.keys())
        self._pending_auto_poni_sources = sources
        settings = self._prompt_auto_poni_settings(auto_cfg, aliases)
        self._pending_auto_poni_sources = {}
        if not settings:
            return False
        auto_cfg["energy_kev"] = float(settings.get("energy_kev", 8.04) or 8.04)

        prepared = self._prepare_auto_poni_reviews(
            auto_cfg,
            sources=settings.get("sources_by_alias") or sources,
            distance_cm_by_alias=settings.get("distance_cm_by_alias", {}),
            first_visible_ring_by_alias=settings.get("first_visible_ring_by_alias", {}),
            rings_to_search_by_alias=settings.get("rings_to_search_by_alias", {}),
            detector_config_by_alias=settings.get("detector_config_by_alias", {}),
            center_px_by_alias=settings.get("center_px_by_alias", {}),
        )
        if not prepared:
            return False

        reviews = prepared["reviews"]
        aliases = list(reviews.keys())
        first_visible = settings.get("first_visible_ring_by_alias") or (
            self._first_visible_rings_for_auto_poni(aliases, auto_cfg)
        )
        show_review = self._get_technical_module("show_auto_poni_review_window")
        if not callable(show_review):
            _tm().QMessageBox.warning(self, "Auto PONI", "Auto PONI review UI unavailable.")
            return False

        decision_payload = show_review(
            aliases=aliases,
            review_by_alias=reviews,
            images_by_alias=prepared["images"],
            detector_config_by_alias=prepared["detector_configs"],
            first_visible_ring_by_alias=first_visible,
            rings_to_show=settings.get("rings_to_search_by_alias")
            or int(auto_cfg.get("rings_to_show", 8)),
            parent=self,
        )
        decision = ""
        if isinstance(decision_payload, dict):
            decision = str(decision_payload.get("decision") or "").strip().lower()

        if decision == "validate":
            return self._validate_auto_poni_reviews(reviews)
        if decision == "correct":
            return self._launch_pyfai_reviews(reviews)
        self._log_technical_event("Auto PONI cancelled")
        return False

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
