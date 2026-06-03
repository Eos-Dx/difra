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


class SessionLifecycleUploadOldFormatMixin:
    @classmethod
    def create_upload_session_id(
        cls,
        *,
        uploader_id: Optional[str] = None,
        lock_user: Optional[str] = None,
    ) -> str:
        """Create deterministic upload-session identifier for Matador stub flow."""
        resolved_uploader = cls._resolve_uploader_id(
            explicit_uploader_id=uploader_id,
            lock_user=lock_user,
        )
        stamp = time.strftime("%Y%m%d_%H%M%S")
        return f"upload_{cls._safe_token(resolved_uploader)}_{stamp}"
    @classmethod
    def execute_upload_stub(
        cls,
        container_path: Path,
        *,
        uploader_id: Optional[str] = None,
        lock_user: Optional[str] = None,
        upload_session_id: Optional[str] = None,
        upload_api: Optional[Any] = None,
        simulate_failure: bool = False,
        failure_message: Optional[str] = None,
    ) -> UploadStubResult:
        """Simulate Matador upload and return response payload."""
        path = Path(container_path)
        resolved_uploader = cls._resolve_uploader_id(
            explicit_uploader_id=uploader_id,
            lock_user=lock_user,
        )
        resolved_session_id = upload_session_id or cls.create_upload_session_id(
            uploader_id=resolved_uploader
        )
        local_checksum = sha256_file(path)
        bytes_uploaded = int(path.stat().st_size) if path.exists() else 0
        upload_backend = upload_api or _build_matador_upload_api()
        upload_request = MatadorUploadContainerRequest(
            upload_session_id=resolved_session_id,
            operator_id=resolved_uploader,
            local_container_id=cls._safe_token(path.stem, "container"),
            file_name=path.name,
            file_size_bytes=bytes_uploaded,
            file_sha256=local_checksum,
        )
        backend_response = upload_backend.upload_container(
            upload_request,
            container_path=path,
        )
        success = bool(backend_response.success) and not bool(simulate_failure)
        message = (
            str(backend_response.message or "Matador upload accepted (stub)")
            if not simulate_failure
            else str(failure_message or "Matador upload failed (stub)")
        )
        response_checksum = (
            str(backend_response.received_sha256 or local_checksum) if success else ""
        )
        remote_container_id = str(backend_response.remote_container_id or "") if success else ""
        return UploadStubResult(
            success=success,
            upload_session_id=resolved_session_id,
            message=message,
            bytes_uploaded=bytes_uploaded,
            local_checksum_sha256=local_checksum,
            response_checksum_sha256=response_checksum,
            remote_container_id=remote_container_id,
        )
    @staticmethod
    def _resolve_old_format_archive_root(
        config: Optional[Dict[str, Any]] = None,
        archive_folder: Optional[Path] = None,
    ) -> Path:
        cfg = config or {}
        configured = cfg.get("old_format_archive_folder")
        if configured:
            return Path(configured)
        return _session_old_format_exporter().resolve_old_format_root(
            config=config,
            archive_folder=archive_folder,
        )
    @classmethod
    def resolve_matador_logs_root(cls, config: Optional[Dict[str, Any]] = None) -> Path:
        cfg = config or {}
        configured = str(cfg.get("matador_logs_folder") or "").strip()
        if configured:
            path = Path(configured)
        else:
            difra_base = Path(_get_difra_base_folder(cfg))
            path = difra_base.parent / "matador_logs"
        path.mkdir(parents=True, exist_ok=True)
        return path
    @classmethod
    def _zip_directory(
        cls,
        source_dir: Path,
        output_zip: Path,
        *,
        include_root: bool = True,
    ) -> Path:
        source_dir = Path(source_dir)
        output_zip = Path(output_zip)
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(source_dir.rglob("*")):
                if not file_path.is_file():
                    continue
                arcname = (
                    file_path.relative_to(source_dir.parent)
                    if include_root
                    else file_path.relative_to(source_dir)
                )
                zf.write(file_path, arcname=str(arcname))
        return output_zip
    @classmethod
    def _prepare_old_format_payload(
        cls,
        session_path: Path,
        *,
        archive_folder: Path,
        config: Optional[Dict[str, Any]] = None,
        persist_old_format: bool = True,
    ):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        payload_names = cls._read_upload_payload_names(Path(session_path))
        temp_root = Path(tempfile.gettempdir()) / "difra" / ".matador_old_format_tmp"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root = temp_root / (
            f"{cls._safe_token(Path(session_path).stem, 'session')}_{stamp}"
        )
        temp_root.mkdir(parents=True, exist_ok=True)
        summary = MatadorZipBundleExporter.export_from_session_container(
            session_path,
            config=config,
            archive_folder=archive_folder,
            target_root=temp_root / "measurement_bundle",
        )
        export_dir = Path(summary.export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)
        zip_path = cls._zip_directory(
            export_dir,
            export_dir.parent / str(payload_names.get("measurement_zip_name") or export_dir.with_suffix(".zip").name),
            include_root=False,
        )
        calibration_group_hash = ""
        try:
            state_payload = json.loads(Path(summary.state_path).read_text(encoding="utf-8"))
            if isinstance(state_payload, dict):
                calibration_group_hash = str(
                    state_payload.get("CALIBRATION_GROUP_HASH") or ""
                ).strip()
        except Exception:
            calibration_group_hash = ""

        calibration_zip_paths: List[Path] = []
        calibration_export_dir: Optional[Path] = None
        try:
            calibration_config = dict(config or {})
            if calibration_group_hash:
                calibration_config["matador_calibration_group_hash_override"] = calibration_group_hash
            calibration_summary = _session_old_format_exporter().export_from_session_container(
                session_path,
                config=calibration_config,
                archive_folder=archive_folder,
                target_root=temp_root / "calibration_bundle",
            )
            calibration_export_dir = Path(calibration_summary.export_dir)
            calibration_root = Path(calibration_summary.export_dir) / "calibration"
            if calibration_root.exists():
                for distance_dir in sorted(calibration_root.iterdir()):
                    if not distance_dir.is_dir():
                        continue
                    calibration_zip = distance_dir.parent / str(
                        payload_names.get("calibration_zip_name")
                        or distance_dir.with_suffix(".zip").name
                    )
                    cls._zip_directory(
                        distance_dir,
                        calibration_zip,
                        include_root=False,
                    )
                    calibration_zip_paths.append(calibration_zip)
        except Exception:
            logger.warning(
                "Failed to prepare calibration ZIP payloads for %s",
                str(session_path),
                exc_info=True,
            )

        if not persist_old_format:
            return summary, None, zip_path, calibration_zip_paths

        old_format_root = cls._resolve_old_format_archive_root(
            config=config,
            archive_folder=archive_folder,
        )
        old_format_root.mkdir(parents=True, exist_ok=True)
        try:
            if old_format_root.resolve() == Path(archive_folder).resolve():
                raise ValueError(
                    "old-format folder must be separate from the measurements archive"
                )
        except FileNotFoundError:
            pass

        old_format_group = old_format_root / export_dir.name
        for child in [old_format_group] if old_format_group.exists() else []:
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except Exception:
                logger.warning(
                    "Failed to clear previous old-format output: %s",
                    str(child),
                    exc_info=True,
                )

        old_format_group.mkdir(parents=True, exist_ok=True)
        old_format_measurements_root = old_format_group / "measurements"
        old_format_measurements_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            export_dir,
            old_format_measurements_root / export_dir.name,
            dirs_exist_ok=True,
        )
        if calibration_export_dir is not None:
            calibration_root = calibration_export_dir / "calibration"
            if calibration_root.exists():
                shutil.copytree(
                    calibration_root,
                    old_format_group / "calibration",
                    dirs_exist_ok=True,
                )

        return summary, old_format_group, zip_path, calibration_zip_paths
