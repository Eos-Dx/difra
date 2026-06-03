"""Technical H5 loading/table population responsibilities."""

from pathlib import Path
import hashlib
import json

import numpy as np

from . import h5_management_mixin as _module
from .poni_center_validation import resolve_poni_rule_alias, validate_poni_metadata
from .poni_distance_validation import parse_poni_distance_cm, validate_poni_distances
from . import technical_startup_reconcile
from difra.gui.technical.analysis_compat import detect_faulty_pixel_masks

os = _module.os
shutil = _module.shutil
time = _module.time
logger = _module.logger
QInputDialog = _module.QInputDialog
QMessageBox = _module.QMessageBox
QFileDialog = _module.QFileDialog
get_container_manager = _module.get_container_manager
get_schema = _module.get_schema
get_technical_validator = _module.get_technical_validator

from difra.gui.main_window_ext.technical import h5_management_loading_actions



class H5LoadingPreviewMixin:
    def _normalize_center_preview_alias(self, alias: str) -> str:
        detector_cfgs = self.config.get("detectors", []) if hasattr(self, "config") else []
        return resolve_poni_rule_alias(alias, detector_cfgs)
    def _detector_sizes_for_center_preview(self):
        sizes = {}
        for detector_cfg in self.config.get("detectors", []) if hasattr(self, "config") else []:
            alias = str(detector_cfg.get("alias") or "").strip()
            if not alias:
                continue
            size_cfg = detector_cfg.get("size", {})
            if isinstance(size_cfg, dict):
                width = size_cfg.get("width", 256)
                height = size_cfg.get("height", 256)
            else:
                width = 256
                height = 256
            try:
                size_tuple = (int(width), int(height))
            except Exception:
                size_tuple = (256, 256)
            sizes[alias] = size_tuple
            sizes[self._normalize_center_preview_alias(alias)] = size_tuple
        return sizes
    def _collect_agbh_images_for_center_preview(self, h5_path: str):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        detector_configs = self.config.get("detectors", []) if hasattr(self, "config") else []
        detector_id_to_alias = {
            str(cfg.get("id")): str(cfg.get("alias"))
            for cfg in detector_configs
            if cfg.get("id") and cfg.get("alias")
        }

        agbh_images = {}

        with h5py.File(h5_path, "r") as h5f:
            tech_group = h5f.get(schema.GROUP_TECHNICAL)
            if tech_group is None:
                tech_group = h5f.get(f"{schema.GROUP_CALIBRATION_SNAPSHOT}/events")
            if tech_group is None:
                return {}

            for event_name in sorted(tech_group.keys()):
                if not str(event_name).startswith("tech_evt_"):
                    continue
                event_group = tech_group[event_name]
                technical_type = event_group.attrs.get(
                    "type",
                    event_group.attrs.get(schema.ATTR_TECHNICAL_TYPE, ""),
                )
                if isinstance(technical_type, bytes):
                    technical_type = technical_type.decode("utf-8", errors="replace")
                if str(technical_type or "").strip().upper() != str(
                    schema.TECHNICAL_TYPE_AGBH
                ).upper():
                    continue

                for detector_name in sorted(event_group.keys()):
                    detector_group = event_group[detector_name]
                    if schema.DATASET_PROCESSED_SIGNAL not in detector_group:
                        continue

                    alias = detector_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                    if isinstance(alias, bytes):
                        alias = alias.decode("utf-8", errors="replace")
                    alias = str(alias or "").strip()
                    if not alias:
                        detector_id = detector_group.attrs.get(schema.ATTR_DETECTOR_ID, "")
                        if isinstance(detector_id, bytes):
                            detector_id = detector_id.decode("utf-8", errors="replace")
                        alias = detector_id_to_alias.get(
                            str(detector_id),
                            str(detector_name).replace("det_", "").upper(),
                        )
                    alias_key = self._normalize_center_preview_alias(alias)
                    if alias_key in agbh_images:
                        continue
                    try:
                        data = detector_group[schema.DATASET_PROCESSED_SIGNAL][()]
                        data = np.asarray(data, dtype=float)
                        if data.ndim == 2:
                            agbh_images[alias_key] = data
                    except Exception:
                        logger.warning(
                            "Failed to extract AGBH image for alias '%s' from %s",
                            alias,
                            h5_path,
                            exc_info=True,
                        )
        return agbh_images
    def _show_poni_center_preview_for_container(
        self,
        h5_path: str,
        *,
        decision_mode: bool = False,
    ):
        validation_cfg = self.config.get("poni_center_validation", {}) if hasattr(self, "config") else {}
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return None if decision_mode else False

        detector_rules = validation_cfg.get("detectors", {})
        if not isinstance(detector_rules, dict) or not detector_rules:
            return None if decision_mode else False

        aliases = [self._normalize_center_preview_alias(a) for a in detector_rules.keys()]
        aliases = [a for a in aliases if a]
        if not aliases:
            return None if decision_mode else False

        poni_by_alias = {}
        try:
            embedded = self._collect_container_poni_text_by_alias(Path(h5_path))
        except Exception:
            embedded = {}
        for alias, text in (embedded or {}).items():
            key = self._normalize_center_preview_alias(alias)
            if key and text:
                poni_by_alias[key] = str(text)

        if not poni_by_alias:
            ponis = getattr(self, "ponis", {}) or {}
            for alias, text in ponis.items():
                key = self._normalize_center_preview_alias(alias)
                if key and text:
                    poni_by_alias[key] = str(text)

        if not poni_by_alias:
            return None if decision_mode else False

        detector_sizes = self._detector_sizes_for_center_preview()
        agbh_images = self._collect_agbh_images_for_center_preview(str(h5_path))

        show_preview = None
        if hasattr(self, "_get_technical_module"):
            show_preview = self._get_technical_module("show_poni_centers_preview_window")
        if not callable(show_preview):
            return None if decision_mode else False

        try:
            dialog = show_preview(
                aliases=aliases,
                poni_by_alias=poni_by_alias,
                detector_sizes_by_alias=detector_sizes,
                validation_cfg=validation_cfg,
                agbh_images_by_alias=agbh_images,
                decision_mode=bool(decision_mode),
                parent=self,
            )
            if bool(decision_mode):
                if isinstance(dialog, dict):
                    result_dialog = dialog.get("dialog")
                    if result_dialog is not None:
                        self._poni_center_preview_dialog = result_dialog
                    return bool(dialog.get("accepted", False))
                if isinstance(dialog, bool):
                    return bool(dialog)
                return None

            if dialog is not None:
                self._poni_center_preview_dialog = dialog
                return True
            return False
        except Exception:
            logger.warning(
                "Failed to show PONI center preview for container %s",
                h5_path,
                exc_info=True,
            )
            return False
