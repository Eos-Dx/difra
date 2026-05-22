"""Archive project/specimen statistics helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


_LEADING_SPECIMEN_RE = re.compile(r"^\s*(\d+)")
_S3_STUDY_RE = re.compile(r"study-id=(\d+)")


def normalize_specimen_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.upper() == "UNKNOWN":
        return ""
    if "__" in text:
        text = text.rsplit("__", 1)[-1].strip()
        match = _LEADING_SPECIMEN_RE.match(text)
        if match:
            return match.group(1)
        return ""
    match = _LEADING_SPECIMEN_RE.match(text)
    return match.group(1) if match else text


def _project_key(row: Dict[str, Any]) -> str:
    raw_id = str(row.get("matadorProjectId") or "").strip()
    if raw_id:
        return raw_id
    return str(row.get("project_id") or row.get("study_name") or "UNSPECIFIED").strip()


def _project_label(row: Dict[str, Any], key: str) -> str:
    name = str(row.get("project_id") or row.get("study_name") or "").strip()
    project_id = str(row.get("matadorProjectId") or "").strip()
    if project_id and name:
        return f"{name} ({project_id})"
    return name or key


def _is_sent(row: Dict[str, Any]) -> bool:
    transfer = str(row.get("transfer_status") or "").strip().upper()
    return transfer == "SENT"


def _has_measurements(row: Dict[str, Any]) -> bool:
    explicit = row.get("has_measurements")
    if explicit is not None:
        return bool(explicit)
    measurement_points = row.get("measurement_points")
    if measurement_points is not None:
        try:
            return int(measurement_points) > 0
        except Exception:
            pass
    detector_datasets = row.get("detector_datasets")
    if detector_datasets is not None:
        try:
            return int(detector_datasets) > 0
        except Exception:
            pass
    return True


def _local_status(rows: Sequence[Dict[str, Any]]) -> str:
    if not rows:
        return "Unmeasured"
    measured_rows = [row for row in rows if _has_measurements(row)]
    if not measured_rows:
        return "Unmeasured"
    sent = sum(1 for row in measured_rows if _is_sent(row))
    if sent == len(measured_rows):
        return "Sent"
    if sent:
        return "Partial"
    return "Unsent"


@dataclass(frozen=True)
class ArchiveProjectStatistics:
    projects: List[Dict[str, Any]]
    specimens_by_project: Dict[str, List[Dict[str, Any]]]


def build_archive_project_statistics(
    rows: Sequence[Dict[str, Any]],
    *,
    matador_specimens_by_project: Optional[Dict[str, Set[str]]] = None,
    matador_uploaded_by_project: Optional[Dict[str, Set[str]]] = None,
) -> ArchiveProjectStatistics:
    local_by_project: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        specimen_key = normalize_specimen_key(
            row.get("specimenId") or row.get("sample_id")
        )
        if not specimen_key:
            continue
        project_key = _project_key(row)
        project = local_by_project.setdefault(
            project_key,
            {
                "key": project_key,
                "label": _project_label(row, project_key),
                "studyIds": set(),
                "specimens": {},
            },
        )
        study_id = str(row.get("matadorStudyId") or "").strip()
        if study_id:
            project["studyIds"].add(study_id)
        specimen = project["specimens"].setdefault(
            specimen_key,
            {
                "specimenId": specimen_key,
                "displaySpecimenId": str(
                    row.get("specimenId") or row.get("sample_id") or specimen_key
                ),
                "rows": [],
            },
        )
        specimen["rows"].append(dict(row))

    matador_specimens_by_project = matador_specimens_by_project or {}
    matador_uploaded_by_project = matador_uploaded_by_project or {}
    all_project_keys = sorted(
        set(local_by_project)
        | set(matador_specimens_by_project)
        | set(matador_uploaded_by_project),
        key=str,
    )

    projects: List[Dict[str, Any]] = []
    specimens_by_project: Dict[str, List[Dict[str, Any]]] = {}
    for project_key in all_project_keys:
        project = local_by_project.get(
            project_key,
            {
                "key": project_key,
                "label": str(project_key),
                "studyIds": set(),
                "specimens": {},
            },
        )
        local_specimens = dict(project["specimens"])
        matador_specimens = matador_specimens_by_project.get(project_key)
        matador_uploaded = matador_uploaded_by_project.get(project_key)
        archive_measured_keys = {
            key
            for key, item in local_specimens.items()
            if any(_has_measurements(row) for row in list(item.get("rows") or []))
        }
        local_keys = archive_measured_keys
        matador_specimen_keys = set(matador_specimens or set())
        matador_uploaded_keys = set(matador_uploaded or set())
        specimen_keys = sorted(
            local_keys | matador_specimen_keys | matador_uploaded_keys,
            key=lambda value: (not str(value).isdigit(), str(value)),
        )

        detail_rows: List[Dict[str, Any]] = []
        for specimen_key in specimen_keys:
            local = local_specimens.get(specimen_key, {"rows": []})
            local_rows = list(local.get("rows") or [])
            local_status = _local_status(local_rows)
            local_measured = specimen_key in archive_measured_keys
            if matador_specimens is None:
                matador_specimen_state = "Unknown"
            else:
                matador_specimen_state = (
                    "In" if specimen_key in matador_specimen_keys else "Out"
                )
            if matador_uploaded is None:
                matador_measurement_state = "Unknown"
            else:
                matador_measurement_state = (
                    "In" if specimen_key in matador_uploaded_keys else "Out"
                )
            detail_rows.append(
                {
                    "specimenId": specimen_key,
                    "displaySpecimenId": local.get("displaySpecimenId") or specimen_key,
                    "localMeasured": local_measured,
                    "localStatus": local_status,
                    "matadorSpecimen": matador_specimen_state,
                    "matadorMeasurement": matador_measurement_state,
                    "containers": len(local_rows),
                }
            )

        not_uploaded = (
            None
            if matador_uploaded is None
            else len(archive_measured_keys - matador_uploaded_keys)
        )
        projects.append(
            {
                "key": project_key,
                "label": project["label"],
                "archiveMeasured": len(archive_measured_keys),
                "matadorSpecimens": (
                    None if matador_specimens is None else len(matador_specimen_keys)
                ),
                "matadorUploaded": (
                    None if matador_uploaded is None else len(matador_uploaded_keys)
                ),
                "missingInArchive": (
                    None
                    if matador_specimens is None
                    else len(matador_specimen_keys - archive_measured_keys)
                ),
                "notUploaded": not_uploaded,
                "archiveOnly": (
                    None
                    if matador_specimens is None
                    else len(archive_measured_keys - matador_specimen_keys)
                ),
            }
        )
        specimens_by_project[project_key] = detail_rows

    return ArchiveProjectStatistics(
        projects=projects,
        specimens_by_project=specimens_by_project,
    )


def collect_matador_project_sets(
    *,
    api: Any,
    project_keys: Iterable[str],
    studies: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]], List[str]]:
    specimen_sets: Dict[str, Set[str]] = {}
    uploaded_sets: Dict[str, Set[str]] = {}
    errors: List[str] = []
    studies_by_project: Dict[str, List[Dict[str, Any]]] = {}
    study_to_project: Dict[str, str] = {}
    for study in studies:
        project_id = str(study.get("projectId") or "").strip()
        study_id = str(study.get("id") or "").strip()
        if not project_id:
            continue
        studies_by_project.setdefault(project_id, []).append(study)
        if study_id:
            study_to_project[study_id] = project_id

    project_key_set = sorted({str(key) for key in project_keys if str(key).isdigit()})
    try:
        all_specimens = api.list_specimens()
    except Exception as exc:
        all_specimens = []
        errors.append(f"Matador specimen list unavailable ({exc})")

    for item in all_specimens:
        specimen_key = normalize_specimen_key(item.get("specimenId") or item.get("id"))
        project_id = str(item.get("projectId") or "").strip()
        study_id = str(item.get("studyId") or "").strip()
        if not project_id and study_id:
            project_id = study_to_project.get(study_id, "")
        if project_id in project_key_set and specimen_key:
            specimen_sets.setdefault(project_id, set()).add(specimen_key)

    for project_key in project_key_set:
        specimen_sets.setdefault(project_key, set())
        uploaded_sets.setdefault(project_key, set())

    try:
        if hasattr(api, "_request_paged_collection"):
            files = api._request_paged_collection(  # noqa: SLF001
                path="/api/ingest-session-files",
                query={"sort": "id,asc"},
            )
        else:
            files = []
    except Exception as exc:
        files = []
        errors.append(f"Matador upload list unavailable ({exc})")

    if files:
        for item in files:
            if not isinstance(item, dict):
                continue
            specimen_key = normalize_specimen_key(item.get("specimenId"))
            if not specimen_key:
                continue
            upload_status = str(item.get("uploadStatus") or "").upper()
            processing_status = str(item.get("processingStatus") or "").upper()
            if upload_status == "FAILED" or processing_status == "FAILED":
                continue
            s3_key = str(item.get("s3Key") or "")
            match = _S3_STUDY_RE.search(s3_key)
            project_id = study_to_project.get(match.group(1), "") if match else ""
            if project_id in uploaded_sets:
                uploaded_sets[project_id].add(specimen_key)
        return specimen_sets, uploaded_sets, errors

    for project_key in project_key_set:
        try:
            uploaded: Set[str] = set()
            for study in studies_by_project.get(project_key, []):
                study_id = study.get("id")
                if study_id is None:
                    continue
                for session in api.list_ingest_sessions(study_id=int(study_id)):
                    session_id = session.get("id")
                    if session_id is None:
                        continue
                    for file_status in api.list_session_files(int(session_id)):
                        key = normalize_specimen_key(
                            getattr(file_status, "specimen_id", None)
                        )
                        upload_status = str(
                            getattr(file_status, "upload_status", "") or ""
                        ).upper()
                        processing_status = str(
                            getattr(file_status, "processing_status", "") or ""
                        ).upper()
                        if key and upload_status != "FAILED" and processing_status != "FAILED":
                            uploaded.add(key)
            uploaded_sets[project_key] = uploaded
        except Exception as exc:
            errors.append(f"{project_key}: Matador upload list unavailable ({exc})")

    return specimen_sets, uploaded_sets, errors
