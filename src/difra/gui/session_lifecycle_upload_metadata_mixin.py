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


class SessionLifecycleUploadMetadataMixin:
    @classmethod
    def _read_matador_session_metadata(
        cls,
        session_path: Path,
        *,
        config: Optional[Dict[str, Any]] = None,
        uploader_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = config or {}
        metadata: Dict[str, Any] = {
            "specimen_id": None,
            "specimen_text": "",
            "study_id": cfg.get("matador_study_id", 100),
            "machine_id": cfg.get("matador_machine_id", 100),
            "distance_mm": None,
            "exposure_time_sec": None,
            "session_date": _normalize_iso_date(cfg.get("matador_session_date")),
            "detector_scope": "PRIMARY",
            "initiated_by": str(
                cfg.get("matador_initiated_by")
                or uploader_id
                or cfg.get("machine_name")
                or cfg.get("setup_name")
                or "difra"
            ),
        }

        try:
            with h5py.File(session_path, "r") as h5f:
                raw_specimen = h5f.attrs.get("specimenId", h5f.attrs.get("sample_id"))
                specimen = h5f.attrs.get(
                    "matadorSpecimenId",
                    h5f.attrs.get(
                        "matador_specimen_id",
                        raw_specimen,
                    ),
                )
                metadata["raw_specimen_text"] = cls._decode_attr(raw_specimen).strip()
                metadata["specimen_text"] = cls._decode_attr(specimen).strip()
                metadata["specimen_id"] = cls._coerce_optional_int(specimen)

                study = h5f.attrs.get("matadorStudyId", metadata["study_id"])
                project = h5f.attrs.get("matadorProjectId")
                machine = h5f.attrs.get("matadorMachineId", metadata["machine_id"])
                if project not in (None, ""):
                    metadata["project_id"] = int(project)
                if study not in (None, ""):
                    metadata["study_id"] = int(study)
                if machine not in (None, ""):
                    metadata["machine_id"] = int(machine)
                session_date = _normalize_iso_date(h5f.attrs.get("acquisition_date"))
                if session_date:
                    metadata["session_date"] = session_date

                distance_cm = h5f.attrs.get("distance_cm", h5f.attrs.get("distanceCm"))
                if distance_cm not in (None, ""):
                    metadata["distance_mm"] = int(round(float(distance_cm) * 10.0))

                exposure_values: List[float] = []
                measurements_group = h5f.get("/entry/measurements")
                aliases = set()
                if measurements_group is not None:
                    for point_group in measurements_group.values():
                        for measurement_group in point_group.values():
                            for detector_key, detector_group in measurement_group.items():
                                aliases.add(str(detector_key).strip().upper())
                                try:
                                    integration_ms = detector_group.attrs.get(
                                        "integration_time_ms"
                                    )
                                except Exception:
                                    integration_ms = None
                                if integration_ms not in (None, ""):
                                    exposure_values.append(
                                        round(float(integration_ms) / 1000.0, 6)
                                    )
                if exposure_values:
                    metadata["exposure_time_sec"] = Counter(exposure_values).most_common(1)[0][0]

                aliases.discard("")
                if len(aliases) > 1:
                    metadata["detector_scope"] = "ALL"
                elif aliases == {"SECONDARY"}:
                    metadata["detector_scope"] = "SECONDARY"
                elif aliases:
                    metadata["detector_scope"] = "PRIMARY"
        except Exception:
            logger.warning(
                "Failed to resolve Matador session metadata from H5 for %s; using config fallbacks",
                str(session_path),
                exc_info=True,
            )

        if metadata["distance_mm"] is None:
            fallback_distance_cm = cfg.get("default_session_distance_cm") or cfg.get(
                "default_technical_distance_cm"
            )
            if fallback_distance_cm not in (None, ""):
                metadata["distance_mm"] = int(round(float(fallback_distance_cm) * 10.0))
        if metadata["exposure_time_sec"] is None:
            fallback_exposure = cfg.get("default_exposure_time_sec")
            if fallback_exposure not in (None, ""):
                metadata["exposure_time_sec"] = float(fallback_exposure)
        if metadata["distance_mm"] is None:
            metadata["distance_mm"] = 170
        if metadata["exposure_time_sec"] is None:
            metadata["exposure_time_sec"] = 0.5
        return metadata
    @staticmethod
    def _requires_strict_matador_contract(upload_api: Any) -> bool:
        return upload_api.__class__.__name__ == "RealMatadorUploadApi"
    @classmethod
    def _matador_specimen_candidate_ids(cls, *values: Any) -> List[int]:
        candidates: List[int] = []
        for value in values:
            text = cls._decode_attr(value).strip()
            if not text:
                continue
            tokens = [text]
            if "__" in text:
                left, right = text.split("__", 1)
                tokens = [left.strip(), right.strip()]
            for token in tokens:
                match = re.match(r"^\s*([+-]?\d+)", token)
                if not match:
                    continue
                candidate = cls._coerce_optional_int(match.group(1))
                if candidate is not None and candidate not in candidates:
                    candidates.append(int(candidate))
        return candidates
    @classmethod
    def _lookup_matador_specimen(cls, upload_api: Any, specimen_id: int) -> Dict[str, Any]:
        getter = getattr(upload_api, "get_specimen", None)
        if callable(getter):
            return dict(getter(int(specimen_id)) or {})
        request_json = getattr(upload_api, "_request_json", None)
        if callable(request_json):
            return dict(
                request_json(
                    method="GET",
                    path=f"/api/specimen/{int(specimen_id)}",
                )
                or {}
            )
        raise RuntimeError("Matador specimen lookup is not available")
    @classmethod
    def _resolve_matador_specimen_id_for_upload(
        cls,
        *,
        upload_api: Any,
        session_metadata: Dict[str, Any],
    ) -> tuple[Optional[int], str]:
        target_study_id = cls._coerce_optional_int(session_metadata.get("study_id"))
        target_project_id = cls._coerce_optional_int(session_metadata.get("project_id"))
        candidates = cls._matador_specimen_candidate_ids(
            session_metadata.get("specimen_text"),
            session_metadata.get("raw_specimen_text"),
        )
        if not candidates:
            return None, "No numeric specimen ID candidates found in container metadata."

        valid: List[tuple[int, str]] = []
        existing_wrong: List[str] = []
        missing: List[int] = []
        for candidate in candidates:
            try:
                payload = cls._lookup_matador_specimen(upload_api, candidate)
            except Exception:
                missing.append(candidate)
                continue

            study = payload.get("study") if isinstance(payload.get("study"), dict) else {}
            payload_study_id = cls._coerce_optional_int(
                payload.get("studyId") or study.get("id")
            )
            project = (
                payload.get("project") if isinstance(payload.get("project"), dict) else {}
            )
            payload_project_id = cls._coerce_optional_int(
                payload.get("projectId") or project.get("id")
            )
            study_ok = target_study_id is None or payload_study_id == target_study_id
            project_ok = (
                target_project_id is None
                or payload_project_id is None
                or payload_project_id == target_project_id
            )
            detail = (
                f"{candidate}: study={payload_study_id or 'unknown'}, "
                f"project={payload_project_id or 'unknown'}"
            )
            if study_ok and project_ok:
                valid.append((candidate, detail))
            else:
                existing_wrong.append(detail)

        if len(valid) == 1:
            resolved = int(valid[0][0])
            return (
                resolved,
                f"Resolved Matador specimen ID {resolved} by /api/specimen pre-check.",
            )
        if len(valid) > 1:
            return (
                None,
                "Ambiguous Matador specimen ID: multiple candidates match "
                f"study={target_study_id or 'unknown'} / project={target_project_id or 'unknown'}: "
                + ", ".join(str(item[0]) for item in valid),
            )
        if existing_wrong:
            return (
                None,
                "Specimen candidate(s) exist in Matador but not in this study/project "
                f"(expected study={target_study_id or 'unknown'}, project={target_project_id or 'unknown'}): "
                + "; ".join(existing_wrong),
            )
        return (
            None,
            "No specimen ID candidate exists in Matador for this container: "
            + ", ".join(str(item) for item in missing),
        )
