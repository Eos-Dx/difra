"""Session Manager for DIFRA GUI.

Handles HDF5 session container lifecycle:
- Creating new sessions
- Managing active session state
- Writing measurements to containers
- Tracking measurement counters
"""

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import h5py
from difra.gui.container_api import get_container_module
from difra.gui.session_manager_measurement_ops_mixin import (
    SessionManagerMeasurementOpsMixin,
)
from difra.gui.session_manager_recovery_mixin import (
    SessionManagerRecoveryMixin,
)
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionManager(SessionManagerRecoveryMixin, SessionManagerMeasurementOpsMixin):
    """Manages HDF5 session containers for DIFRA measurements."""

    SESSION_STATE_ATTR = "session_state"
    SESSION_STATE_REASON_ATTR = "session_state_reason"
    SESSION_STATE_UPDATED_ATTR = "session_state_updated_at"

    SESSION_STATE_DRAFT = "draft"
    SESSION_STATE_PREPARED = "prepared"
    SESSION_STATE_MEASURING = "measuring"
    SESSION_STATE_RECOVERY_REQUIRED = "recovery_required"
    SESSION_STATE_LOCKED = "locked"
    SESSION_STATE_ARCHIVED = "archived"

    VALID_SESSION_STATES = {
        SESSION_STATE_DRAFT,
        SESSION_STATE_PREPARED,
        SESSION_STATE_MEASURING,
        SESSION_STATE_RECOVERY_REQUIRED,
        SESSION_STATE_LOCKED,
        SESSION_STATE_ARCHIVED,
    }

    CAPTURE_MANIFEST_ATTR = "capture_manifest_json"
    CAPTURE_MANIFEST_VERSION = 1

    @staticmethod
    def _resolve_machine_name(config: Dict) -> str:
        """Resolve machine name from explicit field or selected setup identity."""
        return (
            config.get("machine_name")
            or config.get("setup_name")
            or config.get("name")
            or config.get("default_setup")
            or "DIFRA-01"
        )

    @staticmethod
    def _as_text(value, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _read_specimen_id(attrs, *, fallback: str = "") -> str:
        specimen = attrs.get("specimenId")
        if specimen is not None:
            return SessionManager._as_text(specimen, fallback)
        sample = attrs.get("sample_id")
        if sample is not None:
            return SessionManager._as_text(sample, fallback)
        return fallback

    @staticmethod
    def _counter_from_measurement_name(name: str):
        try:
            return int(str(name).split("_")[-1])
        except Exception:
            return None

    def _restore_attenuation_counters_from_h5(self, h5f) -> None:
        """Restore attenuation counters from analytical measurements in an existing session."""
        self.i0_counter = None
        self.i_counter = None

        ana_group_path = getattr(
            self.schema, "GROUP_ANALYTICAL_MEASUREMENTS", "/analytical_measurements"
        )
        ana_group = h5f.get(ana_group_path)
        if ana_group is None:
            return

        role_attr_name = getattr(self.schema, "ATTR_ANALYSIS_ROLE", "analysis_role")
        type_attr_name = getattr(self.schema, "ATTR_ANALYSIS_TYPE", "analysis_type")
        counter_attr_name = getattr(
            self.schema, "ATTR_MEASUREMENT_COUNTER", "measurement_counter"
        )
        attenuation_type = str(
            getattr(self.schema, "ANALYSIS_TYPE_ATTENUATION", "attenuation")
        ).strip().lower()
        role_i0 = str(getattr(self.schema, "ANALYSIS_ROLE_I0", "i0")).strip().lower()
        role_i = str(getattr(self.schema, "ANALYSIS_ROLE_I", "i")).strip().lower()

        i0_candidates = []
        i_candidates = []

        for ana_id in sorted(ana_group.keys()):
            ana_group_item = ana_group[ana_id]
            counter = self._safe_int(ana_group_item.attrs.get(counter_attr_name))
            if counter is None:
                counter = self._counter_from_measurement_name(str(ana_id))
            if counter is None:
                continue

            analysis_type = self._as_text(
                ana_group_item.attrs.get(type_attr_name), ""
            ).strip().lower()
            analysis_role = self._as_text(
                ana_group_item.attrs.get(role_attr_name), ""
            ).strip().lower()

            is_attenuation = (
                analysis_type == attenuation_type
                or analysis_type.startswith("attenuation")
                or analysis_role in {role_i0, role_i}
            )
            if not is_attenuation:
                continue

            is_i0 = (
                analysis_role in {role_i0, "without", "without_sample"}
                or analysis_type
                in {
                    "attenuation_i0",
                    "attenuation_without",
                    "attenuation_without_sample",
                }
            )
            is_i = (
                analysis_role in {role_i, "with", "with_sample"}
                or analysis_type
                in {
                    "attenuation_i",
                    "attenuation_with",
                    "attenuation_with_sample",
                }
            )

            if is_i0:
                i0_candidates.append(counter)
                continue
            if is_i:
                i_candidates.append(counter)
                continue

            # Legacy fallback (no explicit role): first attenuation is I0, later ones are I.
            if not i0_candidates:
                i0_candidates.append(counter)
            else:
                i_candidates.append(counter)

        if i0_candidates:
            self.i0_counter = max(i0_candidates)
        if i_candidates:
            self.i_counter = max(i_candidates)

        if self.i0_counter is not None or self.i_counter is not None:
            logger.info(
                "Restored attenuation counters from existing session",
                session_path=str(self.session_path),
                i0_counter=self.i0_counter,
                i_counter=self.i_counter,
            )
    
    def __init__(self, config: Optional[Dict] = None):
        """Initialize SessionManager.
        
        Args:
            config: Optional configuration dict from global.json
                   If provided, beam_energy_kev will be read from config
        """
        self.session_path: Optional[Path] = None
        self.session_id: Optional[str] = None
        self.sample_id: Optional[str] = None
        self.specimen_id: Optional[str] = None
        self.study_name: Optional[str] = None
        self.technical_container_path: Optional[Path] = None
        self.session_state: str = self.SESSION_STATE_DRAFT
        
        # Track counters for linking
        self.i0_counter: Optional[int] = None  # Attenuation without sample
        self.i_counter: Optional[int] = None   # Attenuation with sample
        # Track in-progress point measurements for crash recovery metadata.
        self._pending_measurements: Dict[int, str] = {}
        
        # Store config for later use
        self.config = config or {}
        self.container_module = get_container_module(self.config)
        self.schema = self.container_module.schema
        self.writer = self.container_module.writer
        self.container_manager = self.container_module.container_manager
        self.producer_software: str = str(
            self.config.get("producer_software")
            or self.config.get("app_name")
            or "difra"
        )
        self.producer_version: str = str(
            self.config.get("producer_version")
            or getattr(self.container_module, "__version__", "unknown")
        )
        
        # Configuration - read from config or use defaults
        if config:
            self.operator_id: str = config.get('operator_id', 'operator')
            self.site_id: str = config.get('site_id', 'DIFRA_LAB')
            self.machine_name: str = self._resolve_machine_name(config)
            self.beam_energy_kev: float = config.get('beam_energy_kev', 17.5)
        else:
            self.operator_id: str = "operator"
            self.site_id: str = "DIFRA_LAB"
            self.machine_name: str = "DIFRA-01"
            self.beam_energy_kev: float = 17.5

    def _set_session_state(self, state: str, reason: str = "") -> bool:
        """Persist session workflow state into the active container attrs."""
        if not self.is_session_active():
            return False

        state_token = str(state or "").strip().lower()
        if state_token not in self.VALID_SESSION_STATES:
            logger.warning(
                "Ignoring invalid session state update",
                requested_state=state_token,
            )
            return False

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with h5py.File(self.session_path, "a") as h5f:
                h5f.attrs[self.SESSION_STATE_ATTR] = state_token
                h5f.attrs[self.SESSION_STATE_REASON_ATTR] = str(reason or "").strip()
                h5f.attrs[self.SESSION_STATE_UPDATED_ATTR] = timestamp
            self.session_state = state_token
            return True
        except Exception as exc:
            logger.warning(
                "Failed to persist session state",
                session_path=str(self.session_path),
                state=state_token,
                reason=str(reason or ""),
                error=str(exc),
                exc_info=True,
            )
            return False

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _write_capture_manifest(self, measurement_path: str, payload: Dict) -> bool:
        """Persist measurement capture manifest JSON into measurement attrs."""
        if not self.is_session_active():
            return False
        try:
            encoded = json.dumps(payload, sort_keys=True, default=str)
            with h5py.File(self.session_path, "a") as h5f:
                if measurement_path not in h5f:
                    return False
                measurement_group = h5f[measurement_path]
                measurement_group.attrs[self.CAPTURE_MANIFEST_ATTR] = encoded
                measurement_group.attrs["capture_manifest_version"] = int(
                    self.CAPTURE_MANIFEST_VERSION
                )
            return True
        except Exception as exc:
            logger.debug(
                "Failed to persist capture manifest",
                measurement_path=str(measurement_path),
                error=str(exc),
                exc_info=True,
            )
            return False

    def _read_capture_manifest(self, measurement_path: str) -> Dict:
        """Read capture manifest JSON from measurement attrs."""
        if not self.is_session_active():
            return {}
        try:
            with h5py.File(self.session_path, "r") as h5f:
                if measurement_path not in h5f:
                    return {}
                measurement_group = h5f[measurement_path]
                raw = measurement_group.attrs.get(self.CAPTURE_MANIFEST_ATTR, "")
                text = self._as_text(raw, "").strip()
                if not text:
                    return {}
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            logger.debug(
                "Failed to load capture manifest from measurement attrs",
                measurement_path=str(measurement_path),
                exc_info=True,
            )
        return {}

    def _active_detector_aliases(self) -> List[str]:
        """Return configured detector aliases for current runtime mode."""
        expected_aliases = []
        expected_aliases_fn = getattr(self, "_expected_detector_aliases", None)
        if callable(expected_aliases_fn):
            try:
                expected_aliases = list(expected_aliases_fn() or [])
            except Exception:
                expected_aliases = []
        if expected_aliases:
            return [str(alias) for alias in expected_aliases if str(alias).strip()]
        aliases = []
        for detector in self.config.get("detectors", []) or []:
            alias = str(detector.get("alias") or "").strip()
            if alias:
                aliases.append(alias)
        return aliases

    def _init_capture_manifest(
        self,
        *,
        measurement_path: str,
        point_index: int,
        timestamp_start: Optional[str] = None,
        capture_basename: Optional[str] = None,
        expected_aliases: Optional[List[str]] = None,
        raw_patterns_by_alias: Optional[Dict[str, List[str]]] = None,
    ) -> bool:
        aliases = [str(alias) for alias in (expected_aliases or []) if str(alias).strip()]
        if not aliases:
            aliases = self._active_detector_aliases()

        files = {}
        base_path = str(capture_basename or "").strip()
        for alias in aliases:
            expected_path = ""
            if base_path:
                expected_path = str(Path(f"{base_path}_{alias}").with_suffix(".npy"))

            raw_patterns = []
            if isinstance(raw_patterns_by_alias, dict):
                raw_patterns = list(raw_patterns_by_alias.get(alias) or [])
            required_raw_keys = []
            raw_section = {}
            if base_path:
                raw_base = Path(f"{base_path}_{alias}")
                for pattern in raw_patterns:
                    token = str(pattern or "").strip()
                    if not token:
                        continue
                    extension = token
                    if extension.startswith("*"):
                        extension = extension[1:]
                    if extension and not extension.startswith("."):
                        extension = f".{extension}"
                    extension = extension or ".bin"
                    blob_key = f"raw_{extension[1:]}"
                    required_raw_keys.append(blob_key)
                    raw_path = raw_base.with_suffix(extension)
                    raw_section[blob_key] = {
                        "path": str(raw_path),
                        "exists": bool(raw_path.exists()),
                        "size_bytes": int(raw_path.stat().st_size) if raw_path.exists() else 0,
                        "sha256": self._sha256_file(raw_path) if raw_path.exists() else "",
                        "status": "ready" if raw_path.exists() else "pending",
                    }
            files[alias] = {
                "path": expected_path,
                "exists": bool(expected_path and Path(expected_path).exists()),
                "size_bytes": int(Path(expected_path).stat().st_size)
                if expected_path and Path(expected_path).exists()
                else 0,
                "sha256": (
                    self._sha256_file(Path(expected_path))
                    if expected_path and Path(expected_path).exists()
                    else ""
                ),
                "status": (
                    "ready"
                    if expected_path and Path(expected_path).exists()
                    else "pending"
                ),
                "raw": raw_section,
                "required_raw_keys": required_raw_keys,
            }

        manifest = {
            "version": int(self.CAPTURE_MANIFEST_VERSION),
            "recovery_id": hashlib.sha1(
                f"{measurement_path}:{timestamp_start or ''}:{datetime.now().isoformat()}".encode(
                    "utf-8"
                )
            ).hexdigest()[:16],
            "measurement_path": str(measurement_path),
            "point_index": int(point_index),
            "timestamp_start": str(timestamp_start or ""),
            "capture_basename": base_path,
            "expected_aliases": aliases,
            "files": files,
            "status": (
                "files_ready"
                if aliases and all(files.get(alias, {}).get("status") == "ready" for alias in aliases)
                else "armed"
            ),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "begin_point_measurement",
        }
        return self._write_capture_manifest(measurement_path, manifest)

    def update_capture_manifest_files(
        self,
        *,
        point_index: int,
        files_by_alias: Dict[str, str],
        raw_files_by_alias: Optional[Dict[str, Dict[str, str]]] = None,
        source: str = "capture_finished",
    ) -> bool:
        """Update capture manifest with actual file paths and checksums."""
        self._check_active()
        measurement_path = self._pending_measurements.get(int(point_index))
        if not measurement_path:
            return False

        manifest = self._read_capture_manifest(measurement_path)
        if not manifest:
            self._init_capture_manifest(
                measurement_path=measurement_path,
                point_index=int(point_index),
                timestamp_start="",
                capture_basename="",
                expected_aliases=list((files_by_alias or {}).keys()),
            )
            manifest = self._read_capture_manifest(measurement_path)

        files_section = manifest.get("files")
        if not isinstance(files_section, dict):
            files_section = {}
        expected_aliases = list(manifest.get("expected_aliases") or [])

        for alias, file_ref in (files_by_alias or {}).items():
            alias_token = str(alias or "").strip()
            if not alias_token:
                continue
            path = Path(str(file_ref))
            file_entry = files_section.get(alias_token, {})
            file_entry["path"] = str(path)
            if path.exists():
                file_entry["exists"] = True
                try:
                    file_entry["size_bytes"] = int(path.stat().st_size)
                except Exception:
                    file_entry["size_bytes"] = 0
                try:
                    file_entry["sha256"] = self._sha256_file(path)
                except Exception:
                    file_entry["sha256"] = ""
                file_entry["status"] = "ready"
            else:
                file_entry["exists"] = False
                file_entry["size_bytes"] = 0
                file_entry["sha256"] = ""
                file_entry["status"] = "missing"
            files_section[alias_token] = file_entry
            if alias_token not in expected_aliases:
                expected_aliases.append(alias_token)

        raw_files_by_alias = raw_files_by_alias or {}
        for alias, raw_mapping in raw_files_by_alias.items():
            alias_token = str(alias or "").strip()
            if not alias_token:
                continue
            file_entry = files_section.get(alias_token, {})
            raw_section = file_entry.get("raw")
            if not isinstance(raw_section, dict):
                raw_section = {}
            for blob_key, raw_file_ref in (raw_mapping or {}).items():
                raw_key = str(blob_key or "").strip()
                if not raw_key:
                    continue
                raw_path = Path(str(raw_file_ref))
                raw_info = raw_section.get(raw_key, {})
                raw_info["path"] = str(raw_path)
                if raw_path.exists():
                    raw_info["exists"] = True
                    try:
                        raw_info["size_bytes"] = int(raw_path.stat().st_size)
                    except Exception:
                        raw_info["size_bytes"] = 0
                    try:
                        raw_info["sha256"] = self._sha256_file(raw_path)
                    except Exception:
                        raw_info["sha256"] = ""
                    raw_info["status"] = "ready"
                else:
                    raw_info["exists"] = False
                    raw_info["size_bytes"] = 0
                    raw_info["sha256"] = ""
                    raw_info["status"] = "missing"
                raw_section[raw_key] = raw_info

            required_raw_keys = file_entry.get("required_raw_keys")
            if not isinstance(required_raw_keys, list):
                required_raw_keys = []
            for raw_key in raw_section.keys():
                if raw_key not in required_raw_keys:
                    required_raw_keys.append(raw_key)

            file_entry["required_raw_keys"] = required_raw_keys
            file_entry["raw"] = raw_section
            files_section[alias_token] = file_entry

        if not expected_aliases:
            expected_aliases = list(files_section.keys())

        ready_flags = []
        for alias in expected_aliases:
            entry = files_section.get(alias, {})
            processed_ready = entry.get("status") == "ready"
            required_raw_keys = entry.get("required_raw_keys")
            if not isinstance(required_raw_keys, list):
                required_raw_keys = []
            raw_section = entry.get("raw")
            if not isinstance(raw_section, dict):
                raw_section = {}
            raw_ready = all(
                raw_section.get(raw_key, {}).get("status") == "ready"
                for raw_key in required_raw_keys
            )
            ready_flags.append(bool(processed_ready and raw_ready))
        manifest["expected_aliases"] = expected_aliases
        manifest["files"] = files_section
        manifest["status"] = (
            "files_ready" if ready_flags and all(ready_flags) else "partial"
        )
        manifest["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        manifest["source"] = str(source or "capture_finished")
        return self._write_capture_manifest(measurement_path, manifest)

    def _count_measurement_records(self) -> int:
        """Count persisted point-measurement records in active session."""
        self._check_active()
        with h5py.File(self.session_path, "r") as h5f:
            measurements_group = h5f.get(self.schema.GROUP_MEASUREMENTS)
            if measurements_group is None:
                return 0
            total = 0
            for point_group in measurements_group.values():
                try:
                    total += int(len(list(point_group.keys())))
                except Exception:
                    continue
        return int(total)

    def has_point_measurements(self) -> bool:
        """Return True once any point measurement record exists (incl. in-progress)."""
        if not self.is_session_active():
            return False
        if self._pending_measurements:
            return True
        try:
            return self._count_measurement_records() > 0
        except Exception:
            return False

    def _infer_session_state_from_h5(self, h5f) -> str:
        """Infer session workflow state from container contents."""
        raw_state = self._as_text(
            h5f.attrs.get(self.SESSION_STATE_ATTR),
            "",
        ).strip().lower()
        if raw_state in self.VALID_SESSION_STATES:
            return raw_state

        locked = bool(self.container_manager.is_container_locked(self.session_path))
        if locked:
            return self.SESSION_STATE_LOCKED

        measurements_group = h5f.get(self.schema.GROUP_MEASUREMENTS)
        has_measurements = False
        has_in_progress = False
        if measurements_group is not None:
            for point_group in measurements_group.values():
                for measurement_group in point_group.values():
                    has_measurements = True
                    status = self._as_text(
                        measurement_group.attrs.get(
                            self.schema.ATTR_MEASUREMENT_STATUS, ""
                        ),
                        "",
                    ).strip().lower()
                    if status == self.schema.STATUS_IN_PROGRESS:
                        has_in_progress = True
                        break
                if has_in_progress:
                    break

        if has_in_progress:
            return self.SESSION_STATE_RECOVERY_REQUIRED
        if has_measurements:
            return self.SESSION_STATE_MEASURING

        points_group = h5f.get(self.schema.GROUP_POINTS)
        has_points = bool(points_group is not None and len(points_group.keys()) > 0)
        zones_group = h5f.get(self.schema.GROUP_IMAGES_ZONES)
        has_zones = bool(zones_group is not None and len(zones_group.keys()) > 0)
        has_mapping = bool(
            f"{self.schema.GROUP_IMAGES_MAPPING}/mapping" in h5f
        )
        if has_points or has_zones or has_mapping:
            return self.SESSION_STATE_PREPARED
        return self.SESSION_STATE_DRAFT

    def reset_for_image_reform(
        self,
        *,
        image_data=None,
        reset_attenuation: bool = True,
    ) -> None:
        """Reset session workspace for image reform before first point measurement."""
        self._check_active()
        if self.is_locked():
            raise RuntimeError(
                "Cannot reform image: session container is locked."
            )
        if self.has_point_measurements():
            raise RuntimeError(
                "Cannot reform image: point measurements already exist."
            )

        ana_group = getattr(
            self.schema, "GROUP_ANALYTICAL_MEASUREMENTS", "/analytical_measurements"
        )
        groups_to_delete = [
            self.schema.GROUP_IMAGES,
            self.schema.GROUP_POINTS,
            self.schema.GROUP_MEASUREMENTS,
        ]
        if reset_attenuation:
            groups_to_delete.append(ana_group)

        with h5py.File(self.session_path, "a") as h5f:
            for group_path in groups_to_delete:
                if group_path in h5f:
                    del h5f[group_path]

            h5f.require_group(self.schema.GROUP_IMAGES.lstrip("/"))
            h5f.require_group(self.schema.GROUP_IMAGES_ZONES.lstrip("/"))
            h5f.require_group(self.schema.GROUP_IMAGES_MAPPING.lstrip("/"))
            h5f.require_group(self.schema.GROUP_POINTS.lstrip("/"))
            h5f.require_group(self.schema.GROUP_MEASUREMENTS.lstrip("/"))
            if reset_attenuation:
                h5f.require_group(ana_group.lstrip("/"))

            measurement_counter_attr = getattr(
                self.schema, "ATTR_MEASUREMENT_COUNTER", "measurement_counter"
            )
            h5f.attrs[measurement_counter_attr] = int(0)

        self._pending_measurements = {}
        if reset_attenuation:
            self.i0_counter = None
            self.i_counter = None

        if image_data is not None:
            self.writer.add_image(
                file_path=self.session_path,
                image_index=1,
                image_data=image_data,
                image_type="sample",
            )

        self._set_session_state(
            self.SESSION_STATE_DRAFT,
            reason="image_reformed_workspace_reset",
        )
        self.log_event(
            message="Session workspace reset for image reform",
            event_type="workspace_reformed",
            details={"attenuation_reset": bool(reset_attenuation)},
        )

    def log_event(
        self,
        message: str,
        event_type: str = "event",
        level: str = "INFO",
        details: Optional[Dict] = None,
    ) -> None:
        """Append session runtime event to container log dataset."""
        if not self.is_session_active():
            return
        append_runtime_log = getattr(self.writer, "append_runtime_log", None)
        if not callable(append_runtime_log):
            return
        append_runtime_log(
            file_path=self.session_path,
            message=message,
            level=level,
            event_type=event_type,
            source=self.producer_software,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            details=details or {},
        )
    
    def _get_technical_folder(self) -> Path:
        """Get technical container folder from config.
        
        Returns:
            Path to technical folder from config
        """
        # Try technical_folder from config first
        folder = self.config.get('technical_folder')
        if folder:
            return Path(folder)
        
        # Fall back to difra_base_folder/technical
        base = self.config.get('difra_base_folder')
        if base:
            return Path(base) / 'technical'
        
        # Last resort: home directory
        logger.warning("No technical folder in config, using default")
        return Path.home() / 'difra_technical'
    
    def is_session_active(self) -> bool:
        """Check if a session is currently active."""
        return self.session_path is not None and self.session_path.exists()

    def _find_locked_technical_container_for_distance(
        self,
        folder: Path,
        distance_cm: float,
        tolerance_cm: float = 0.5,
    ) -> Optional[Path]:
        """Find newest locked technical container matching distance."""
        folder = Path(folder)
        if not folder.exists():
            return None

        candidates = []
        seen = set()
        for pattern in ("technical_*.nxs.h5", "technical_*.h5"):
            for tech_path in folder.glob(pattern):
                if "archive" in tech_path.parts:
                    continue
                if not tech_path.is_file():
                    continue

                key = str(tech_path.resolve())
                if key in seen:
                    continue
                seen.add(key)

                try:
                    with h5py.File(tech_path, "r") as h5f:
                        file_distance = float(h5f.attrs.get("distance_cm", float("nan")))
                except Exception:
                    continue

                if abs(file_distance - float(distance_cm)) > float(tolerance_cm):
                    continue
                if not self.container_manager.is_container_locked(tech_path):
                    continue

                candidates.append(tech_path)

        if not candidates:
            return None

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]
    
    def create_session(
        self,
        folder: Path,
        distance_cm: float,
        technical_container_path: Optional[str] = None,
        **session_attrs,
    ) -> Tuple[str, Path]:
        """Create a new session container.
        
        All required session attributes should be provided as keyword arguments.
        These will be passed to the container writer and validated against self.schema.
        
        Required session attributes (from schema):
            sample_id: str - Unique sample identifier
            specimenId: str - Matador specimen identifier stored alongside sample_id
            study_name: str - Study name/identifier (optional, defaults to UNSPECIFIED)
            operator_id: str - Operator ID/name (optional, uses config default)
            site_id: str - Site identifier (optional, uses config default)
            machine_name: str - Machine name (optional, uses config default)
            beam_energy_keV: float - Beam energy (optional, uses config default)
            acquisition_date: str - Acquisition date (optional, auto-generated)
        
        Optional session attributes:
            patient_id: str - Patient identifier
            
        Args:
            folder: Directory for session container (measurements folder)
            distance_cm: Sample-detector distance (for technical container lookup)
            technical_container_path: Optional explicit technical container path
                (preferred when GUI has an active selected container)
            **session_attrs: All session attributes as keyword arguments
            
        Returns:
            Tuple of (session_id, session_path)
            
        Raises:
            RuntimeError: If no valid technical container found
            ValueError: If required session attributes are missing
        """
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)

        existing_sessions = sorted(
            [
                path
                for path in folder.glob("session_*.nxs.h5")
                if path.is_file()
            ]
        )
        if existing_sessions:
            existing_names = "\n".join(f"• {path.name}" for path in existing_sessions[:3])
            if len(existing_sessions) > 3:
                existing_names += f"\n• ... and {len(existing_sessions) - 3} more"
            raise RuntimeError(
                "A session container already exists in the measurements folder.\n\n"
                f"{existing_names}\n\n"
                "Close, send/archive, or explicitly clear the existing session container before creating a new one."
            )

        schema = self.schema
        writer = self.writer
        find_active_technical_container = self.container_manager.find_active_technical_container
        is_container_locked = self.container_manager.is_container_locked

        # Get technical folder from config
        technical_folder = self._get_technical_folder()
        
        # Prefer explicit active technical container from UI when provided.
        explicit_tech_path = str(technical_container_path or "").strip()
        if explicit_tech_path:
            tech_path = Path(explicit_tech_path)
            if not tech_path.exists():
                raise RuntimeError(
                    f"Selected technical container was not found: {tech_path}\n"
                    "Please load or create a technical container and try again."
                )
        else:
            # Find active technical container for this distance in technical folder
            tech_path = find_active_technical_container(
                folder=technical_folder,
                distance_cm=distance_cm,
            )

        if not tech_path:
            raise RuntimeError(
                f"No technical container found for distance {distance_cm} cm. "
                "Please create technical measurements first."
            )

        # If distance-based lookup returned an unlocked container while a locked one
        # exists for the same distance, automatically pick the locked candidate.
        if not explicit_tech_path and not is_container_locked(tech_path):
            locked_match = self._find_locked_technical_container_for_distance(
                folder=technical_folder,
                distance_cm=distance_cm,
            )
            if locked_match is not None and Path(locked_match) != Path(tech_path):
                logger.warning(
                    "Distance lookup returned unlocked technical container; using locked match instead",
                    requested_distance_cm=float(distance_cm),
                    unlocked_container=str(tech_path),
                    locked_container=str(locked_match),
                )
                tech_path = locked_match

        if not is_container_locked(tech_path):
            raise RuntimeError(
                f"Technical container is not locked: {tech_path}\n"
                "Please lock the technical container before creating sessions."
            )
        
        # Build session attributes from provided kwargs and config defaults
        # Required attributes from schema
        specimen_id = session_attrs.get("specimenId", session_attrs.get("specimen_id"))
        if specimen_id is None:
            specimen_id = session_attrs.get(
                self.schema.ATTR_SAMPLE_ID,
                session_attrs.get("sample_id"),
            )

        container_attrs = {
            self.schema.ATTR_SAMPLE_ID: session_attrs.get(
                self.schema.ATTR_SAMPLE_ID,
                session_attrs.get('sample_id', specimen_id),  # Support both snake_case and schema names
            ),
            self.schema.ATTR_STUDY_NAME: session_attrs.get(
                self.schema.ATTR_STUDY_NAME,
                session_attrs.get('study_name', "UNSPECIFIED"),
            ),
            self.schema.ATTR_OPERATOR_ID: session_attrs.get(
                self.schema.ATTR_OPERATOR_ID,
                session_attrs.get('operator_id', self.operator_id),
            ),
            self.schema.ATTR_SITE_ID: session_attrs.get(
                self.schema.ATTR_SITE_ID,
                session_attrs.get('site_id', self.site_id),
            ),
            self.schema.ATTR_MACHINE_NAME: session_attrs.get(
                self.schema.ATTR_MACHINE_NAME,
                session_attrs.get('machine_name', self.machine_name),
            ),
            self.schema.ATTR_BEAM_ENERGY_KEV: session_attrs.get(
                self.schema.ATTR_BEAM_ENERGY_KEV,
                session_attrs.get('beam_energy_keV', self.beam_energy_kev),
            ),
            self.schema.ATTR_ACQUISITION_DATE: session_attrs.get(
                self.schema.ATTR_ACQUISITION_DATE,
                session_attrs.get('acquisition_date', datetime.now().strftime("%Y-%m-%d")),
            ),
        }

        if hasattr(self.schema, "ATTR_PROJECT_ID"):
            project_attr = self.schema.ATTR_PROJECT_ID
            container_attrs[project_attr] = session_attrs.get(
                project_attr,
                session_attrs.get("project_id", container_attrs[self.schema.ATTR_STUDY_NAME]),
            )
        
        # Add optional attributes if provided
        if self.schema.ATTR_PATIENT_ID in session_attrs or 'patient_id' in session_attrs:
            container_attrs[self.schema.ATTR_PATIENT_ID] = session_attrs.get(
                self.schema.ATTR_PATIENT_ID,
                session_attrs.get('patient_id'),
            )
        
        # Validate required sample_id
        if not container_attrs[self.schema.ATTR_SAMPLE_ID]:
            raise ValueError("sample_id is required to create a session")
        
        sample_id = container_attrs[self.schema.ATTR_SAMPLE_ID]
        study_name = container_attrs[self.schema.ATTR_STUDY_NAME]
        
        logger.info(
            "Creating new session",
            sample_id=sample_id,
            distance_cm=distance_cm,
            technical_container=str(tech_path),
            study_name=study_name,
            operator_id=container_attrs.get(self.schema.ATTR_OPERATOR_ID),
            site_id=container_attrs.get(self.schema.ATTR_SITE_ID),
            machine_name=container_attrs.get(self.schema.ATTR_MACHINE_NAME),
        )
        
        # Create session container with schema-driven attributes
        self.session_id, session_path_str = self.writer.create_session_container(
            folder=folder,
            producer_software=self.producer_software,
            producer_version=self.producer_version,
            **container_attrs,
        )
        
        self.session_path = Path(session_path_str)
        self.sample_id = sample_id
        self.study_name = study_name
        self.technical_container_path = Path(tech_path)

        # Copy technical data to session
        self.writer.copy_technical_to_session(
            technical_file=tech_path,
            session_file=self.session_path,
        )
        specimen_text = self._as_text(specimen_id, sample_id)
        self.specimen_id = specimen_text
        extra_attrs = {
            "specimenId": specimen_text,
            "distance_cm": float(distance_cm),
        }
        study_id = session_attrs.get("matadorStudyId", session_attrs.get("matador_study_id"))
        machine_id = session_attrs.get(
            "matadorMachineId",
            session_attrs.get("matador_machine_id"),
        )
        if study_id not in (None, ""):
            extra_attrs["matadorStudyId"] = int(study_id)
        if machine_id not in (None, ""):
            extra_attrs["matadorMachineId"] = int(machine_id)
        try:
            with h5py.File(self.session_path, "a") as h5f:
                for key, value in extra_attrs.items():
                    h5f.attrs[key] = value
                sample_group = h5f.get(self.schema.GROUP_SAMPLE)
                if sample_group is not None:
                    sample_group.attrs["specimenId"] = specimen_text
        except Exception:
            logger.warning(
                "Failed to persist extra specimen/Matador attrs into session container",
                session_path=str(self.session_path),
                exc_info=True,
            )
        self.log_event(
            message="Technical snapshot copied into session",
            event_type="technical_snapshot_copied",
            details={"technical_container": str(tech_path)},
        )
        self._set_session_state(
            self.SESSION_STATE_DRAFT,
            reason="session_created",
        )
        
        logger.info(
            "Session created successfully",
            session_id=self.session_id,
            session_path=str(self.session_path),
        )
        
        # Reset counters
        self.i0_counter = None
        self.i_counter = None
        self._pending_measurements = {}
        
        return self.session_id, self.session_path
    
    def close_session(self):
        """Close the current session and clear state."""
        if self.session_path:
            logger.info(
                "Closing session",
                session_id=self.session_id,
                sample_id=self.sample_id,
            )
        
        self.session_path = None
        self.session_id = None
        self.sample_id = None
        self.specimen_id = None
        self.study_name = None
        self.technical_container_path = None
        self.i0_counter = None
        self.i_counter = None
        self._pending_measurements = {}
        self.session_state = self.SESSION_STATE_DRAFT

    def open_existing_session(self, session_file: Path) -> Dict:
        """Load metadata from an existing session container into manager state."""
        import h5py

        session_file = Path(session_file)
        if not session_file.exists():
            raise FileNotFoundError(f"Session container not found: {session_file}")

        with h5py.File(session_file, "r") as f:
            self.session_path = session_file
            sample_group = f.get(self.schema.GROUP_SAMPLE)
            user_group = f.get(self.schema.GROUP_USER)
            calibration_snapshot = f.get(self.schema.GROUP_CALIBRATION_SNAPSHOT)

            self.sample_id = self._read_specimen_id(
                {
                    "specimenId": f.attrs.get("specimenId"),
                    "sample_id": f.attrs.get(
                        self.schema.ATTR_SAMPLE_ID,
                        sample_group.attrs.get(self.schema.ATTR_SAMPLE_ID) if sample_group else None,
                    ),
                },
                fallback="unknown",
            )
            self.specimen_id = self.sample_id
            self.study_name = self._as_text(
                f.attrs.get(
                    self.schema.ATTR_STUDY_NAME,
                    sample_group.attrs.get(self.schema.ATTR_STUDY_NAME) if sample_group else None,
                ),
                "UNSPECIFIED",
            )
            self.session_id = self._as_text(
                f.attrs.get(self.schema.ATTR_SESSION_ID),
                "unknown",
            )
            self.operator_id = self._as_text(
                f.attrs.get(
                    self.schema.ATTR_OPERATOR_ID,
                    user_group.attrs.get(self.schema.ATTR_OPERATOR_ID) if user_group else None,
                ),
                self.operator_id,
            )
            self.machine_name = self._as_text(
                f.attrs.get(
                    self.schema.ATTR_MACHINE_NAME,
                    user_group.attrs.get(self.schema.ATTR_MACHINE_NAME) if user_group else None,
                ),
                self.machine_name,
            )

            if calibration_snapshot is not None:
                source = calibration_snapshot.attrs.get("source_file")
                self.technical_container_path = Path(source) if source else None
            else:
                self.technical_container_path = None

            self._restore_attenuation_counters_from_h5(f)
            self.session_state = self._infer_session_state_from_h5(f)

        # Rebuild pending map from in-progress measurements for crash recovery.
        incomplete = self._load_incomplete_measurements_from_container(session_file)
        self._pending_measurements = {
            item["point_index"]: item["measurement_path"] for item in incomplete
        }

        return self.get_session_info()

    def _check_active(self):
        """Check if session is active, raise if not."""
        if not self.is_session_active():
            raise RuntimeError(
                "No active session. Please create a session first using create_session()."
            )
    
    def is_locked(self) -> bool:
        """Check if the current session container is locked.
        
        Returns:
            True if locked, False if unlocked or no active session
        """
        if not self.is_session_active():
            return False
        
        return self.container_manager.is_container_locked(self.session_path)
    
    def update_sample_id(self, new_sample_id: str) -> bool:
        """Update the sample ID in the session container.
        
        Can only update if container is unlocked.
        
        Args:
            new_sample_id: New sample identifier
            
        Returns:
            True if updated successfully, False if locked or failed
        """
        self._check_active()
        
        if self.is_locked():
            logger.warning(
                "Cannot update specimenId: container is locked",
                specimen_id=new_sample_id,
            )
            return False
        
        try:
            import h5py
            
            with h5py.File(self.session_path, 'a') as f:
                old_sample_id = f.attrs.get(self.schema.ATTR_SAMPLE_ID, 'unknown')
                f.attrs[self.schema.ATTR_SAMPLE_ID] = new_sample_id
                f.attrs["specimenId"] = new_sample_id
                if self.schema.GROUP_SAMPLE in f:
                    f[self.schema.GROUP_SAMPLE].attrs[self.schema.ATTR_SAMPLE_ID] = new_sample_id
                    f[self.schema.GROUP_SAMPLE].attrs["specimenId"] = new_sample_id

            refresh_summary = getattr(self.writer, "refresh_human_summary", None)
            if callable(refresh_summary):
                refresh_summary(self.session_path)
                
            self.sample_id = new_sample_id
            self.specimen_id = new_sample_id
            
            logger.info(
                "Updated sample_id in session container",
                old_sample_id=old_sample_id,
                new_sample_id=new_sample_id,
                session_path=str(self.session_path),
            )
            
            return True
            
        except Exception as e:
            logger.error(
                f"Failed to update sample_id: {e}",
                exc_info=True,
            )
            return False
    
    def get_session_info(self) -> Dict:
        """Get current session information.
        
        Returns:
            Dict with session metadata
        """
        if not self.is_session_active():
            return {"active": False}

        try:
            with h5py.File(self.session_path, "r") as h5f:
                persisted_state = self._as_text(
                    h5f.attrs.get(self.SESSION_STATE_ATTR),
                    "",
                ).strip().lower()
                if persisted_state in self.VALID_SESSION_STATES:
                    self.session_state = persisted_state
        except Exception:
            pass

        transfer_status = "unsent"
        get_transfer_status = getattr(self.container_manager, "get_transfer_status", None)
        if callable(get_transfer_status):
            try:
                transfer_status = str(get_transfer_status(self.session_path) or "unsent")
            except Exception:
                transfer_status = "unsent"
        
        return {
            "active": True,
            "session_id": self.session_id,
            "session_path": str(self.session_path),
            "sample_id": self.sample_id,
            "specimenId": self.specimen_id or self.sample_id,
            "study_name": self.study_name,
            "operator_id": self.operator_id,
            "machine_name": self.machine_name,
            "beam_energy_kev": self.beam_energy_kev,
            "is_locked": self.is_locked(),
            "transfer_status": transfer_status,
            "session_state": str(self.session_state or self.SESSION_STATE_DRAFT),
            "i0_recorded": self.i0_counter is not None,
            "i_recorded": self.i_counter is not None,
            "attenuation_complete": (
                self.i0_counter is not None and self.i_counter is not None
            ),
        }
