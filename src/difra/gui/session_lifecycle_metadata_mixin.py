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


class SessionLifecycleMetadataMixin:
    @staticmethod
    def _safe_token(value: Optional[str], fallback: str = "unknown") -> str:
        token = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in str(value or "")
        ).strip("_")
        return token or fallback
    @staticmethod
    def _resolve_uploader_id(
        explicit_uploader_id: Optional[str] = None,
        lock_user: Optional[str] = None,
    ) -> str:
        for value in (explicit_uploader_id, lock_user):
            text = str(value or "").strip()
            if text:
                return text
        return "unknown"
    @staticmethod
    def _notify_progress(
        progress_callback: Optional[Any],
        *,
        message: str,
        current: Optional[int] = None,
        total: Optional[int] = None,
        kind: str = "",
        container_path: Optional[Path] = None,
    ) -> None:
        if not callable(progress_callback):
            return
        try:
            progress_callback(
                {
                    "message": str(message or "").strip(),
                    "current": current,
                    "total": total,
                    "kind": str(kind or "").strip(),
                    "container_path": str(container_path) if container_path else "",
                }
            )
        except Exception:
            logger.debug("Suppressed session send progress callback exception", exc_info=True)
    @classmethod
    def _read_upload_payload_names(cls, session_path: Path) -> Dict[str, str]:
        session_path = Path(session_path)
        measurement_zip_name = f"{session_path.stem}.zip"
        calibration_zip_name = "calibration.zip"

        try:
            with h5py.File(session_path, "r") as h5f:
                specimen_text = str(
                    h5f.attrs.get("specimenId", h5f.attrs.get("sample_id", "UNKNOWN"))
                    or "UNKNOWN"
                ).strip()
                distance_value = h5f.attrs.get("distance_cm", h5f.attrs.get("distanceCm"))
                try:
                    distance_token = f"{max(1, int(round(float(distance_value))))}cm"
                except Exception:
                    distance_token = "unknown_distance"

                session_id = cls._safe_token(
                    str(h5f.attrs.get("session_id", "") or "").strip(),
                    session_path.stem,
                )
                specimen_token = cls._safe_token(specimen_text, "UNKNOWN")

                state_payload = _session_old_format_exporter()._load_state_payload(h5f)
                calibration_fallback = cls._matador_bundle_hash_fallback_from_h5(
                    h5f,
                    path=session_path,
                )
                calibration_group_hash = _session_old_format_exporter()._resolve_calibration_group_hash(
                    h5f,
                    state_payload=state_payload,
                    fallback=calibration_fallback,
                )
                calibration_token = cls._safe_token(
                    calibration_group_hash,
                    "unknown_calibration",
                )

                measurement_zip_name = (
                    f"measurement_{specimen_token}_{distance_token}_{session_id}.zip"
                )
                calibration_zip_name = (
                    f"calibration_{distance_token}_{calibration_token}.zip"
                )
        except Exception:
            logger.warning(
                "Failed to derive Matador payload filenames for %s; falling back to defaults",
                str(session_path),
                exc_info=True,
            )

        return {
            "measurement_zip_name": measurement_zip_name,
            "calibration_zip_name": calibration_zip_name,
        }
    @classmethod
    def _read_technical_container_id_from_h5(cls, h5f: h5py.File) -> str:
        for snapshot_path in ("/entry/calibration_snapshot", "/entry/technical"):
            snapshot = h5f.get(snapshot_path)
            if snapshot is None:
                continue
            for attr_name in (
                "source_container_id",
                "technical_container_id",
                "container_id",
            ):
                value = cls._decode_attr(snapshot.attrs.get(attr_name, "")).strip()
                if value:
                    return value
        for attr_name in ("technical_container_id", "source_container_id"):
            value = cls._decode_attr(h5f.attrs.get(attr_name, "")).strip()
            if value:
                return value
        return ""
    @classmethod
    def _read_technical_container_id(cls, session_path: Path) -> str:
        try:
            with h5py.File(Path(session_path), "r") as h5f:
                return cls._read_technical_container_id_from_h5(h5f)
        except Exception:
            logger.warning(
                "Failed to read technical container id for %s",
                str(session_path),
                exc_info=True,
            )
            return ""
    @classmethod
    def _matador_bundle_hash_fallback_from_h5(
        cls,
        h5f: h5py.File,
        *,
        path: Path,
    ) -> str:
        specimen_text = cls._decode_attr(
            h5f.attrs.get("specimenId", h5f.attrs.get("sample_id")),
        ).strip()
        patient_text = cls._decode_attr(h5f.attrs.get("patient_id", "")).strip()
        specimen_info = MatadorZipBundleExporter._parse_specimen_patient(
            specimen_text=specimen_text,
            patient_text=patient_text,
        )
        distance_value = h5f.attrs.get("distance_cm", h5f.attrs.get("distanceCm"))
        try:
            distance_token = f"{max(1, int(round(float(distance_value))))}cm"
        except Exception:
            distance_token = "unknown_distance"
        bundle_key = str(specimen_info.get("bundle_key") or "").strip()
        if bundle_key:
            return f"{bundle_key}_{distance_token}"
        return Path(path).stem
    @classmethod
    def _read_calibration_group_hash_from_h5(
        cls,
        session_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
    ) -> str:
        path = Path(session_path)
        if not path.exists():
            return ""
        try:
            with h5py.File(path, "r") as h5f:
                state_payload = _session_old_format_exporter()._load_state_payload(h5f)
                calibration_fallback = cls._matador_bundle_hash_fallback_from_h5(
                    h5f,
                    path=path,
                )
                return _session_old_format_exporter()._resolve_calibration_group_hash(
                    h5f,
                    state_payload=state_payload,
                    config=config,
                    fallback=calibration_fallback,
                )
        except Exception:
            logger.warning(
                "Failed to read calibration group hash for %s",
                str(session_path),
                exc_info=True,
            )
            return ""
    @staticmethod
    def _format_matador_exposure_key(value: Any) -> str:
        try:
            return f"{float(value):.6f}".rstrip("0").rstrip(".")
        except Exception:
            return str(value or "").strip()
    @classmethod
    def _matador_session_bucket_key_from_metadata(
        cls,
        metadata: Dict[str, Any],
    ) -> str:
        return ":".join(
            [
                "session",
                str(metadata.get("session_date") or ""),
                str(metadata.get("study_id") or ""),
                str(metadata.get("machine_id") or ""),
                str(metadata.get("distance_mm") or ""),
                cls._format_matador_exposure_key(metadata.get("exposure_time_sec")),
            ]
        )
    @classmethod
    def _matador_batch_group_key(
        cls,
        session_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
        uploader_id: Optional[str] = None,
    ) -> str:
        path = Path(session_path)
        if not path.exists():
            return ""
        metadata = cls._read_matador_session_metadata(
            path,
            config=config,
            uploader_id=uploader_id,
        )
        return cls._matador_session_bucket_key_from_metadata(metadata)
    @classmethod
    def _matador_batch_order_key(
        cls,
        session_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
        uploader_id: Optional[str] = None,
    ) -> str:
        session_key = cls._matador_batch_group_key(
            session_path,
            config=config,
            uploader_id=uploader_id,
        )
        group_hash = cls._read_calibration_group_hash_from_h5(
            session_path,
            config=config,
        )
        if session_key and group_hash:
            return f"{session_key}:calibration:{group_hash}"
        return session_key
    @classmethod
    def _order_paths_by_matador_group(
        cls,
        paths: Iterable[Path],
        *,
        config: Optional[Dict[str, Any]] = None,
        uploader_id: Optional[str] = None,
    ) -> List[Path]:
        buckets: Dict[str, List[Path]] = {}
        group_order: List[str] = []
        for path in [Path(item) for item in paths]:
            key = cls._matador_batch_order_key(
                path,
                config=config,
                uploader_id=uploader_id,
            ) or (
                f"ungrouped:{cls._safe_token(path.resolve().as_posix())}"
            )
            if key not in buckets:
                buckets[key] = []
                group_order.append(key)
            buckets[key].append(path)
        return [path for key in group_order for path in buckets[key]]
    @classmethod
    def _write_container_attrs(cls, container_path: Path, attrs: Dict[str, Any]) -> bool:
        """Write attrs to a possibly locked HDF5 container (best effort)."""
        path = Path(container_path)
        original_mode: Optional[int] = None
        try:
            try:
                original_mode = path.stat().st_mode
                if not os.access(path, os.W_OK):
                    os.chmod(path, original_mode | 0o200)
            except Exception:
                original_mode = None

            with h5py.File(path, "a") as h5f:
                for key, value in attrs.items():
                    h5f.attrs[str(key)] = value
            return True
        except Exception as exc:
            logger.warning(
                "Failed to write container attrs: path=%s keys=%s error=%s",
                str(path),
                ",".join(sorted(str(key) for key in attrs.keys())),
                exc,
                exc_info=True,
            )
            return False
        finally:
            if original_mode is not None:
                try:
                    os.chmod(path, original_mode)
                except Exception as exc:
                    logger.warning(
                        "Failed to restore container file mode: path=%s error=%s",
                        str(path),
                        exc,
                        exc_info=True,
                    )
    @classmethod
    def edit_archived_session_matador_metadata(
        cls,
        *,
        container_path: Path,
        project_id: Any,
        project_name: Any,
        study_id: Any,
        study_name: Any,
        specimen_id: Any = None,
        edited_by: Optional[str] = None,
        auth_mode: str = "password",
    ) -> Dict[str, Any]:
        """Rewrite archived H5 project/study metadata and persist an audit trail."""
        path = Path(container_path)
        if not path.exists():
            return {
                "success": False,
                "updated": False,
                "message": f"Container not found: {path}",
            }

        resolved_project_id = cls._coerce_optional_int(project_id)
        resolved_study_id = cls._coerce_optional_int(study_id)
        resolved_specimen = cls._decode_attr(specimen_id).strip()
        resolved_project_name = cls._decode_attr(project_name).strip()
        resolved_study_name = cls._decode_attr(study_name).strip()
        resolved_editor = cls._decode_attr(edited_by).strip() or "unknown"
        resolved_auth_mode = cls._decode_attr(auth_mode).strip() or "password"

        if resolved_project_id is None:
            return {
                "success": False,
                "updated": False,
                "message": "Matador project ID is required.",
            }
        if resolved_study_id is None:
            return {
                "success": False,
                "updated": False,
                "message": "Matador study ID is required.",
            }
        if not resolved_project_name:
            return {
                "success": False,
                "updated": False,
                "message": "Matador project name is required.",
            }
        if not resolved_study_name:
            return {
                "success": False,
                "updated": False,
                "message": "Matador study name is required.",
            }

        original_mode: Optional[int] = None
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        try:
            try:
                original_mode = path.stat().st_mode
                if not os.access(path, os.W_OK):
                    os.chmod(path, original_mode | 0o200)
            except Exception:
                original_mode = None

            with h5py.File(path, "a") as h5f:
                previous_specimen = cls._decode_attr(
                    h5f.attrs.get("specimenId", h5f.attrs.get("sample_id", ""))
                ).strip()
                previous_project_name = cls._decode_attr(
                    h5f.attrs.get(
                        "matadorProjectName",
                        h5f.attrs.get("project_id", ""),
                    )
                ).strip()
                previous_project_id = cls._coerce_optional_int(
                    h5f.attrs.get("matadorProjectId")
                )
                previous_study_name = cls._decode_attr(
                    h5f.attrs.get("study_name", "")
                ).strip()
                previous_study_id = cls._coerce_optional_int(
                    h5f.attrs.get("matadorStudyId")
                )

                if resolved_specimen:
                    h5f.attrs["sample_id"] = resolved_specimen
                    h5f.attrs["specimenId"] = resolved_specimen
                    matador_specimen_id = cls._coerce_optional_int(
                        resolved_specimen.split("__", 1)[0]
                    )
                    if matador_specimen_id is None:
                        if "matadorSpecimenId" in h5f.attrs:
                            del h5f.attrs["matadorSpecimenId"]
                    else:
                        h5f.attrs["matadorSpecimenId"] = int(matador_specimen_id)
                h5f.attrs["project_id"] = resolved_project_name
                h5f.attrs["matadorProjectId"] = int(resolved_project_id)
                h5f.attrs["matadorProjectName"] = resolved_project_name
                h5f.attrs["study_name"] = resolved_study_name
                h5f.attrs["matadorStudyId"] = int(resolved_study_id)

                sample_group = h5f.get("/entry/sample")
                if sample_group is not None:
                    if resolved_specimen:
                        sample_group.attrs["sample_id"] = resolved_specimen
                        sample_group.attrs["specimenId"] = resolved_specimen
                    sample_group.attrs["project_id"] = resolved_project_name

                raw_state = h5f.attrs.get("meta_json")
                if raw_state is not None:
                    try:
                        state_payload = json.loads(cls._decode_attr(raw_state))
                    except Exception:
                        state_payload = None
                    if isinstance(state_payload, dict):
                        if resolved_specimen:
                            state_payload["sample_id"] = resolved_specimen
                            state_payload["specimenId"] = resolved_specimen
                            matador_specimen_id = cls._coerce_optional_int(
                                resolved_specimen.split("__", 1)[0]
                            )
                            if matador_specimen_id is None:
                                state_payload.pop("matadorSpecimenId", None)
                            else:
                                state_payload["matadorSpecimenId"] = int(matador_specimen_id)
                        state_payload["project_id"] = resolved_project_name
                        state_payload["matadorProjectId"] = int(resolved_project_id)
                        state_payload["matadorProjectName"] = resolved_project_name
                        state_payload["study_name"] = resolved_study_name
                        state_payload["matadorStudyId"] = int(resolved_study_id)
                        h5f.attrs["meta_json"] = json.dumps(
                            state_payload,
                            ensure_ascii=False,
                        )

                line = (
                    f"{timestamp} | operator={resolved_editor} | auth={resolved_auth_mode} | "
                    f"specimen='{previous_specimen or '-'}'"
                    f" -> '{resolved_specimen or previous_specimen or '-'}' | "
                    f"project='{previous_project_name or '-'}'[{previous_project_id if previous_project_id is not None else '-'}]"
                    f" -> '{resolved_project_name}'[{resolved_project_id}] | "
                    f"study='{previous_study_name or '-'}'[{previous_study_id if previous_study_id is not None else '-'}]"
                    f" -> '{resolved_study_name}'[{resolved_study_id}]"
                )

                previous_log = cls._decode_attr(
                    h5f.attrs.get("archive_metadata_edit_log", "")
                )
                lines = [item for item in previous_log.splitlines() if item.strip()]
                lines.append(line)
                lines = lines[-100:]

                h5f.attrs["archive_metadata_edit_log"] = "\n".join(lines)
                h5f.attrs["archive_metadata_edit_count"] = int(len(lines))
                h5f.attrs["archive_metadata_edit_last_by"] = resolved_editor
                h5f.attrs["archive_metadata_edit_last_at"] = timestamp
                h5f.attrs["archive_metadata_edit_last_auth"] = resolved_auth_mode
                h5f.attrs["archive_metadata_edit_last_summary"] = line

                sample = cls._decode_attr(
                    h5f.attrs.get("specimenId", h5f.attrs.get("sample_id")),
                ).strip() or "unknown"
                operator = cls._decode_attr(
                    h5f.attrs.get("operator_id", h5f.attrs.get("locked_by")),
                ).strip() or "unknown"
                machine = cls._decode_attr(h5f.attrs.get("machine_name")).strip() or "unknown"
                site = cls._decode_attr(h5f.attrs.get("site_id")).strip() or "unknown"
                session_id = cls._decode_attr(h5f.attrs.get("session_id")).strip() or "unknown"
                acquisition_date = cls._decode_attr(
                    h5f.attrs.get("acquisition_date")
                ).strip()
                created_at = cls._decode_attr(
                    h5f.attrs.get("creation_timestamp")
                ).strip()

                summary = "\n".join(
                    [
                        f"Sample ID: {sample}",
                        f"Project ID: {resolved_project_name}",
                        f"Study Name: {resolved_study_name}",
                        f"Operator ID: {operator}",
                        f"Machine: {machine}",
                        f"Site ID: {site}",
                        f"Session ID: {session_id}",
                        f"Acquisition Date: {acquisition_date}",
                        f"Created At: {created_at}",
                    ]
                )
                h5f.attrs["human_summary"] = summary
                if "/entry/human_summary" in h5f:
                    del h5f["/entry/human_summary"]
                if "/entry" in h5f:
                    h5f.create_dataset(
                        "/entry/human_summary",
                        data=summary,
                        dtype=h5py.string_dtype(encoding="utf-8"),
                    )

            return {
                "success": True,
                "updated": True,
                "message": "Archived session metadata updated.",
            }
        except Exception as exc:
            logger.warning(
                "Failed to edit archived session metadata: path=%s error=%s",
                str(path),
                exc,
                exc_info=True,
            )
            return {
                "success": False,
                "updated": False,
                "message": str(exc),
            }
        finally:
            if original_mode is not None:
                try:
                    os.chmod(path, original_mode)
                except Exception as exc:
                    logger.warning(
                        "Failed to restore container file mode: path=%s error=%s",
                        str(path),
                        exc,
                        exc_info=True,
                    )
