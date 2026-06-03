"""Capture manifest helpers for SessionManager."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

import h5py

from difra.gui.session_manager_common_mixin import SessionManagerCommonMixin
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionManagerManifestMixin:
    """Capture manifest persistence and file verification helpers."""

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _read_h5_text_attr(path: Path, attr_name: str, default: str = "") -> str:
        try:
            with h5py.File(path, "r") as h5f:
                value = h5f.attrs.get(attr_name)
        except Exception:
            return default
        return SessionManagerCommonMixin._as_text(value, default)

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
        aliases = [
            str(alias) for alias in (expected_aliases or []) if str(alias).strip()
        ]
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
                        "size_bytes": int(raw_path.stat().st_size)
                        if raw_path.exists()
                        else 0,
                        "sha256": self._sha256_file(raw_path)
                        if raw_path.exists()
                        else "",
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
                if aliases
                and all(
                    files.get(alias, {}).get("status") == "ready" for alias in aliases
                )
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
