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



class H5LoadingCoreMixin:
    @staticmethod
    def _safe_archive_token(value: str, fallback: str = "unknown") -> str:
        token = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in str(value or "")
        ).strip("_")
        return token or fallback
    def validate_technical_h5(self):
        """Deprecated: validation button workflow was removed."""
        QMessageBox.information(
            self,
            "Removed Workflow",
            "Explicit Validate action is removed.\n"
            "Containers are validated automatically when loaded or locked.",
        )
        self._log_technical_event("Validate action removed from technical workflow")
    @staticmethod
    def _parse_h5ref(value: str):
        raw = str(value or "")
        if not raw.startswith("h5ref://"):
            return None, None
        payload = raw[len("h5ref://") :]
        container_path, sep, dataset_path = payload.partition("#")
        if not sep or not container_path or not dataset_path:
            return None, None
        return container_path, dataset_path
    def _distance_map_by_alias(self, *, prefer_draft: bool = False):
        detector_configs = self.config.get("detectors", []) if hasattr(self, "config") else []
        try:
            active_ids = set(
                str(value)
                for value in (
                    getattr(self, "_get_active_detector_ids", lambda: [])() or []
                )
            )
        except Exception:
            active_ids = set()
        active_aliases = [
            str(detector.get("alias") or detector.get("id") or "").strip()
            for detector in detector_configs
            if not active_ids or str(detector.get("id") or "").strip() in active_ids
        ]

        def _draft_distance_map():
            by_alias = {}
            by_id = getattr(self, "_detector_distances", {}) or {}
            for detector in detector_configs:
                detector_id = detector.get("id")
                alias = detector.get("alias")
                if not detector_id or not alias:
                    continue
                if detector_id in by_id:
                    try:
                        by_alias[str(alias)] = float(by_id[detector_id])
                    except (TypeError, ValueError) as exc:
                        logger.warning(
                            "Failed to parse detector distance for alias=%s id=%s: %s",
                            alias,
                            detector_id,
                            exc,
                        )
            return by_alias

        if prefer_draft:
            draft = _draft_distance_map()
            if draft:
                return draft

        active_path = None
        active_getter = getattr(self, "_active_technical_container_path_obj", None)
        if callable(active_getter):
            try:
                active_path = active_getter()
            except Exception:
                active_path = None
        if active_path is None:
            raw_path = str(getattr(self, "_active_technical_container_path", "") or "").strip()
            active_path = Path(raw_path) if raw_path else None

        if active_path is not None and Path(active_path).exists():
            container_distances = self._read_technical_container_distances_by_alias(
                Path(active_path),
                active_aliases=active_aliases,
            )
            if container_distances:
                return container_distances

        return _draft_distance_map()
    def _read_technical_container_distances_by_alias(self, container_path: Path, *, active_aliases=None):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        aliases = [str(alias).strip() for alias in (active_aliases or []) if str(alias).strip()]
        distances = {}
        root_distance = None
        try:
            with h5py.File(container_path, "r") as h5f:
                root_attr = h5f.attrs.get(schema.ATTR_DISTANCE_CM)
                if root_attr is not None:
                    root_distance = float(root_attr)

                def _record(attrs, fallback_alias=""):
                    alias = str(
                        attrs.get(getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias"), "")
                        or fallback_alias
                    ).strip()
                    distance_attr = attrs.get(schema.ATTR_DISTANCE_CM)
                    if alias and distance_attr is not None:
                        distances[alias] = float(distance_attr)

                poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
                if poni_group is not None:
                    for ds_name in sorted(poni_group.keys()):
                        _record(poni_group[ds_name].attrs)

                technical_group = h5f.get(schema.GROUP_TECHNICAL)
                if technical_group is not None:
                    for event_name in sorted(technical_group.keys()):
                        event_group = technical_group[event_name]
                        for det_name in sorted(event_group.keys()):
                            detector_group = event_group[det_name]
                            fallback = schema.parse_detector_role(det_name) if str(det_name).startswith("det_") else ""
                            _record(detector_group.attrs, fallback_alias=fallback)
        except Exception:
            logger.debug("Failed to read technical container distances from %s", container_path, exc_info=True)
            return {}

        if not distances and root_distance is not None:
            for alias in aliases:
                distances[alias] = root_distance
        return distances
    def _collect_poni_data_by_alias(self):
        poni_data = {}
        poni_files = getattr(self, "poni_files", {}) or {}
        active_aliases = set()
        get_active_aliases = getattr(self, "_get_active_detector_aliases", None)
        if callable(get_active_aliases):
            try:
                active_aliases = {
                    str(alias).strip().upper()
                    for alias in (get_active_aliases() or [])
                    if str(alias).strip()
                }
            except Exception:
                active_aliases = set()

        def _canonical_alias(alias):
            raw = str(alias or "").strip()
            if not raw:
                return ""
            alias_key = raw.upper()
            if not active_aliases:
                return alias_key
            if alias_key in active_aliases:
                return alias_key
            resolver = getattr(self, "_resolve_configured_technical_alias", None)
            if callable(resolver):
                try:
                    resolved, _detector_id, candidates = resolver(alias_key)
                    for candidate in [resolved, *sorted(candidates or [])]:
                        candidate_key = str(candidate or "").strip().upper()
                        if candidate_key in active_aliases:
                            return candidate_key
                except Exception:
                    logger.debug("Failed to canonicalize PONI alias %s", alias, exc_info=True)
            return ""

        for alias, info in poni_files.items():
            if not isinstance(info, dict):
                continue
            alias_key = _canonical_alias(alias)
            if not alias_key:
                continue
            path = str(info.get("path") or "").strip()
            if not path or not os.path.exists(path):
                continue
            try:
                poni_text = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                logger.warning("Failed to read PONI file from path: %s", path, exc_info=True)
                continue
            poni_name = str(info.get("name") or Path(path).name or f"{alias_key}.poni")
            if alias_key not in poni_data or str(alias).strip().upper() == alias_key:
                poni_data[alias_key] = (poni_text, poni_name)
        return poni_data
    def _copy_poni_files_to_container_folder(self, active_path):
        active_path = Path(active_path)
        poni_files = getattr(self, "poni_files", {}) or {}
        if not isinstance(poni_files, dict):
            return 0
        copied = 0
        for alias, info in list(poni_files.items()):
            if not isinstance(info, dict):
                continue
            source = Path(str(info.get("path") or "").strip())
            if not source.exists() or not source.is_file():
                continue
            dest_name = str(info.get("name") or source.name or f"{alias}.poni")
            dest = active_path.parent / dest_name
            try:
                if source.resolve() != dest.resolve():
                    shutil.copy2(source, dest)
                    copied += 1
                info["path"] = str(dest)
                info["name"] = dest.name
                poni_files[alias] = info
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to copy PONI file next to technical container: %s -> %s: %s",
                    source,
                    dest,
                    exc,
                    exc_info=True,
                )
        return copied
    @staticmethod
    def _parse_poni_distance_cm(poni_text: str):
        return parse_poni_distance_cm(poni_text)
    def _poni_distance_validation_config(self):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_distance_validation", {})
        if not isinstance(validation_cfg, dict):
            validation_cfg = {}
        if bool(cfg.get("DEV", False)) and not bool(validation_cfg.get("apply_in_dev_mode", False)):
            return {}
        return validation_cfg
    @classmethod
    def _poni_distance_validation_errors(
        cls,
        poni_data,
        distances_by_alias,
        *,
        tolerance_percent: float = 5.0,
        validation_config=None,
    ):
        if not poni_data or not distances_by_alias:
            return []
        poni_text_by_alias = {}
        poni_name_by_alias = {}
        for alias, payload in dict(poni_data).items():
            try:
                poni_text, poni_name = payload
            except Exception:
                poni_text, poni_name = payload, f"{alias}.poni"
            poni_text_by_alias[str(alias)] = str(poni_text or "")
            poni_name_by_alias[str(alias)] = str(poni_name or f"{alias}.poni")
        cfg = dict(validation_config or {})
        if "tolerance_percent" not in cfg and "default_tolerance_percent" not in cfg:
            cfg["tolerance_percent"] = float(tolerance_percent)
        return validate_poni_distances(
            poni_text_by_alias=poni_text_by_alias,
            distances_by_alias=distances_by_alias,
            poni_name_by_alias=poni_name_by_alias,
            validation_config=cfg,
        )
    def _poni_metadata_validation_config(self):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_metadata_validation", {})
        if not isinstance(validation_cfg, dict) or not bool(validation_cfg.get("enabled", False)):
            return {}
        if bool(cfg.get("DEV", False)) and not bool(validation_cfg.get("apply_in_dev_mode", False)):
            return {}
        return validation_cfg
    def _poni_metadata_validation_errors(self, poni_data):
        validation_cfg = self._poni_metadata_validation_config()
        if not validation_cfg:
            return []
        poni_text_by_alias = {}
        for alias, payload in dict(poni_data or {}).items():
            try:
                poni_text, _poni_name = payload
            except Exception:
                poni_text = payload
            poni_text_by_alias[str(alias)] = str(poni_text or "")
        return validate_poni_metadata(
            poni_text_by_alias=poni_text_by_alias,
            validation_config=validation_cfg,
        )
    def _canonical_faulty_pixel_alias(self, *values) -> str:
        normalize = getattr(self, "_normalize_technical_alias_candidates", None)
        candidates = set()
        for value in values:
            token = str(value or "").strip()
            if not token:
                continue
            if callable(normalize):
                candidates.update(normalize(token))
            else:
                upper = token.upper()
                candidates.add(upper)
                if upper.startswith("DET_"):
                    candidates.add(upper[4:])
                else:
                    candidates.add(f"DET_{upper}")

        primary_tokens = {"PRIMARY", "SAXS", "DET_PRIMARY", "DET_SAXS"}
        secondary_tokens = {"SECONDARY", "WAXS", "DET_SECONDARY", "DET_WAXS"}
        if candidates & primary_tokens:
            return "PRIMARY"
        if candidates & secondary_tokens:
            return "SECONDARY"
        return ""
    def _apply_loaded_masks(self, loaded_masks: dict):
        if not loaded_masks:
            return
        if not isinstance(getattr(self, "masks", None), dict):
            self.masks = {}
        self.masks.update(loaded_masks)
        for widget in (getattr(self, "measurement_widgets", {}) or {}).values():
            if hasattr(widget, "masks"):
                widget.masks = self.masks
    @staticmethod
    def _poni_data_signature(poni_data) -> str:
        normalized = {}
        for alias, payload in sorted((poni_data or {}).items()):
            try:
                poni_text, poni_name = payload
            except Exception:
                poni_text, poni_name = "", ""
            normalized[str(alias)] = {
                "name": str(poni_name or ""),
                "content_sha256": hashlib.sha256(
                    str(poni_text or "").encode("utf-8")
                ).hexdigest(),
            }
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    def _set_active_technical_container(self, file_path: str):
        self._active_technical_container_path = str(file_path)
        container_manager = get_container_manager(self.config if hasattr(self, "config") else None)
        try:
            self._active_technical_container_locked = bool(
                container_manager.is_container_locked(Path(file_path))
            )
        except (AttributeError, OSError, TypeError, ValueError):
            self._active_technical_container_locked = False
        if hasattr(self, "_refresh_technical_output_folder_lock"):
            try:
                self._refresh_technical_output_folder_lock()
            except (AttributeError, RuntimeError, TypeError) as exc:
                logger.warning(
                    "Failed to refresh technical output lock indicator: %s",
                    exc,
                    exc_info=True,
                )
        infer_state = getattr(self, "_infer_container_state", None)
        if callable(infer_state):
            try:
                self._active_technical_container_state = str(
                    infer_state(Path(file_path)) or ""
                ).strip()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                logger.debug(
                    "Suppressed exception while inferring container state on activation",
                    exc_info=True,
                )
    def _active_technical_container_path_obj(self):
        raw = str(getattr(self, "_active_technical_container_path", "") or "").strip()
        if not raw:
            return None
        return Path(raw)
    def _append_runtime_log_to_active_technical_container(
        self,
        message: str,
        *,
        channel: str = "",
        source: str = "gui",
    ) -> bool:
        """Append runtime log line to active technical container TXT dataset."""
        raw_message = str(message or "").strip()
        if not raw_message:
            return False

        active_path = self._active_technical_container_path_obj()
        if active_path is None or not active_path.exists():
            return False

        normalized_channel = str(channel or "").strip().upper()
        if not normalized_channel and raw_message.startswith("["):
            right = raw_message.find("]")
            if right > 1:
                normalized_channel = raw_message[1:right].strip().upper()
        if not normalized_channel:
            normalized_channel = "GENERAL"

        schema = get_schema(self.config if hasattr(self, "config") else None)
        runtime_root = str(getattr(schema, "GROUP_RUNTIME", "/runtime") or "/runtime")
        if not runtime_root.startswith("/"):
            runtime_root = f"/{runtime_root}"
        logs_txt_path = f"{runtime_root.rstrip('/')}/difra_logs_txt"

        max_entries = 5000
        if hasattr(self, "config") and isinstance(self.config, dict):
            try:
                max_entries = int(self.config.get("technical_runtime_log_max_entries", 5000))
            except (TypeError, ValueError):
                max_entries = 5000
        if max_entries < 1:
            max_entries = 1

        restore_mode = None
        try:
            stat_result = active_path.stat()
            restore_mode = stat_result.st_mode
            if not os.access(active_path, os.W_OK):
                os.chmod(active_path, restore_mode | 0o200)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            restore_mode = None

        try:
            import h5py

            timestamp = schema.now_timestamp() if hasattr(schema, "now_timestamp") else time.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            new_line = (
                f"{timestamp} | {normalized_channel} | {str(source or 'gui')} | {raw_message}"
            )
            with h5py.File(active_path, "a") as h5f:
                existing_text = ""
                if logs_txt_path in h5f:
                    raw = h5f[logs_txt_path][()]
                    if isinstance(raw, bytes):
                        existing_text = raw.decode("utf-8", errors="replace")
                    else:
                        existing_text = str(raw or "")
                    try:
                        del h5f[logs_txt_path]
                    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                        logger.debug(
                            "Suppressed exception while replacing technical TXT log dataset",
                            exc_info=True,
                        )

                lines = [ln for ln in str(existing_text).splitlines() if str(ln).strip()]
                lines.append(new_line)
                lines = lines[-int(max_entries):]
                payload = "\n".join(lines)

                ds = h5f.create_dataset(logs_txt_path, data=np.bytes_(payload))
                ds.attrs["line_count"] = int(len(lines))
                ds.attrs["last_timestamp"] = str(timestamp)
                ds.attrs["format"] = "txt"
                ds.attrs["encoding"] = "utf-8"
            return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            logger.debug(
                "Suppressed exception while writing technical runtime log",
                exc_info=True,
            )
            return False
        finally:
            if restore_mode is not None:
                try:
                    os.chmod(active_path, restore_mode)
                except (OSError, RuntimeError, TypeError, ValueError):
                    logger.debug(
                        "Suppressed exception while restoring technical container mode",
                        exc_info=True,
                    )
