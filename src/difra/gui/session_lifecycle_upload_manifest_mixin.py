from __future__ import annotations

from collections import Counter
from fnmatch import fnmatch
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Set
import zipfile

import h5py

from difra.gui.matador_upload_api import (
    MatadorFindOrCreateSessionRequest,
    MatadorRegisterFileRequest,
    MatadorUploadContainerRequest,
    build_matador_upload_api,
    sha256_file,
)
from difra.gui.main_window_ext.technical.helpers import _get_difra_base_folder
from difra.gui.matador_zip_bundle_exporter import MatadorZipBundleExporter
from difra.gui.session_lifecycle_common import (
    SendArchiveResult,
    UploadStubResult,
    _normalize_iso_date,
)
from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.gui.session_old_format_exporter import SessionOldFormatExporter

logger = logging.getLogger(__name__)
SessionLifecycleActions = None


def _actions_module():
    from difra.gui import session_lifecycle_actions as actions

    return actions


def _build_matador_upload_api(*args, **kwargs):
    return _actions_module().build_matador_upload_api(*args, **kwargs)


def _session_old_format_exporter():
    return _actions_module().SessionOldFormatExporter


def _session_lifecycle_service():
    return _actions_module().SessionLifecycleService


class SessionLifecycleUploadManifestMixin:
    @staticmethod
    def _poll_until_hash_verified(
        upload_api: Any,
        *,
        file_id: int,
        attempts: int = 6,
        delay_sec: float = 2.0,
        progress_callback: Optional[Any] = None,
        status_label: str = "",
        current: Optional[int] = None,
        total: Optional[int] = None,
        container_path: Optional[Path] = None,
    ):
        last_status = None
        for index in range(max(int(attempts), 1)):
            status = upload_api.get_file_status(int(file_id))
            last_status = status
            upload_status = str(status.upload_status or "").upper()
            SessionLifecycleActions._notify_progress(
                progress_callback,
                message=(
                    f"{status_label} verification attempt {index + 1}/{max(int(attempts), 1)}: "
                    f"{upload_status or 'PENDING'}"
                ).strip(),
                current=current,
                total=total,
                kind="file_status",
                container_path=container_path,
            )
            if upload_status == "HASH_VERIFIED":
                return status
            if upload_status == "FAILED":
                return status
            if index < max(int(attempts), 1) - 1:
                time.sleep(max(float(delay_sec), 0.0))
        return last_status
    @staticmethod
    def _uploaded_file_is_reusable(status: Any) -> bool:
        upload_status = str(getattr(status, "upload_status", "") or "").upper()
        processing_status = str(getattr(status, "processing_status", "") or "").upper()
        if processing_status == "COMPLETED":
            return True
        return upload_status == "HASH_VERIFIED" and processing_status not in {
            "ABANDONED",
            "FAILED",
            "REJECTED",
        }
    @classmethod
    def _matador_find_reusable_file(
        cls,
        upload_api: Any,
        *,
        ingest_session_id: int,
        file_name: str,
    ) -> Optional[Any]:
        try:
            session_files = upload_api.list_session_files(int(ingest_session_id))
        except Exception:
            logger.debug(
                "Failed to list Matador ingest session files before calibration upload",
                exc_info=True,
            )
            return None
        target_name = str(file_name or "").strip()
        for status in session_files:
            if str(getattr(status, "file_name", "") or "").strip() != target_name:
                continue
            if cls._uploaded_file_is_reusable(status):
                return status
        return None
    @staticmethod
    def _matador_manifest_now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    @staticmethod
    def _load_matador_uploaded_manifest(manifest_path: Optional[Path]) -> Dict[str, Any]:
        if manifest_path is None:
            return {}
        path = Path(manifest_path)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load Matador upload manifest %s", str(path), exc_info=True)
            return {}
        return payload if isinstance(payload, dict) else {}
    @classmethod
    def _write_matador_uploaded_manifest(
        cls,
        manifest_path: Optional[Path],
        manifest: Dict[str, Any],
    ) -> None:
        if manifest_path is None:
            return
        path = Path(manifest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest["version"] = 1
        manifest["updated_at"] = cls._matador_manifest_now()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)
    @staticmethod
    def _manifest_file_key(file_path: Path, manifest_path: Optional[Path]) -> str:
        path = Path(file_path)
        if manifest_path is not None:
            root = Path(manifest_path).parent
            try:
                return path.resolve().relative_to(root.resolve()).as_posix()
            except Exception:
                pass
        return path.name
    @staticmethod
    def _matador_calibration_manifest_key(
        *,
        session_id: int,
        distance_mm: int,
        detector_scope: str,
        calibration_group_hash: str,
    ) -> str:
        return (
            f"{int(session_id)}:{int(distance_mm)}:"
            f"{str(detector_scope or 'PRIMARY').upper()}:{str(calibration_group_hash).strip()}"
        )
    @classmethod
    def _matador_manifest_distance_bucket(
        cls,
        manifest: Dict[str, Any],
        *,
        session_metadata: Dict[str, Any],
        session_id: int,
    ) -> Dict[str, Any]:
        distances = manifest.setdefault("distances", {})
        distance_key = str(int(session_metadata["distance_mm"]))
        bucket = distances.setdefault(distance_key, {})
        bucket["session_id"] = int(session_id)
        bucket["session_date"] = str(session_metadata.get("session_date") or "")
        bucket["study_id"] = int(session_metadata["study_id"])
        bucket["machine_id"] = int(session_metadata["machine_id"])
        bucket["distanceInMm"] = int(session_metadata["distance_mm"])
        bucket["exposureTimeSec"] = float(session_metadata["exposure_time_sec"])
        bucket.setdefault("calibrations", {})
        bucket.setdefault("uploaded_files", {})
        return bucket
    @classmethod
    def _matador_manifest_has_verified_calibration(
        cls,
        manifest: Dict[str, Any],
        *,
        distance_mm: int,
        calibration_key: str,
    ) -> bool:
        bucket = (manifest.get("distances") or {}).get(str(int(distance_mm))) or {}
        entry = (bucket.get("calibrations") or {}).get(str(calibration_key)) or {}
        return str(entry.get("uploadStatus") or "").upper() == "HASH_VERIFIED"
    @classmethod
    def _record_matador_uploaded_file(
        cls,
        *,
        manifest_path: Optional[Path],
        session_metadata: Dict[str, Any],
        session_id: int,
        file_path: Path,
        file_id: int,
        file_type: str,
        ingest_kind: str,
        sha256: str,
        size_bytes: int,
        upload_status: str,
        processing_status: str = "",
        calibration_key: str = "",
        paired_file_id: Optional[int] = None,
    ) -> None:
        manifest = cls._load_matador_uploaded_manifest(manifest_path)
        bucket = cls._matador_manifest_distance_bucket(
            manifest,
            session_metadata=session_metadata,
            session_id=int(session_id),
        )
        entry = {
            "file_id": int(file_id),
            "sha256": str(sha256),
            "size_bytes": int(size_bytes),
            "file_type": str(file_type),
            "ingest_kind": str(ingest_kind),
            "uploadStatus": str(upload_status),
            "processingStatus": str(processing_status or ""),
            "uploaded_at": cls._matador_manifest_now(),
        }
        if paired_file_id is not None:
            entry["paired_file_id"] = int(paired_file_id)
        if calibration_key:
            bucket.setdefault("calibrations", {})[str(calibration_key)] = dict(entry)
        file_key = cls._manifest_file_key(file_path, manifest_path)
        bucket.setdefault("uploaded_files", {})[file_key] = entry
        cls._write_matador_uploaded_manifest(manifest_path, manifest)
