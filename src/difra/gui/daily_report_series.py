"""Daily report container validation and detector series collection."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import h5py
import numpy as np

from difra.gui.daily_report_common import (
    DEFAULT_POINTS,
    SAXS_DISTANCE_THRESHOLD_CM,
    SAXS_RANGE,
    WAXS_RANGE,
    _as_text,
    _safe_token,
)
from difra.gui.daily_report_integration import (
    _integrated_range_is_complete,
    _integrated_signal_fraction,
    integrate_detector_signal,
)
from difra.gui.daily_report_models import DetectorSeries


def _is_container_valid(h5f: h5py.File) -> Tuple[bool, str]:
    specimen = _as_text(h5f.attrs.get("specimenId", h5f.attrs.get("sample_id", "")))
    if not specimen:
        return False, "missing specimenId"

    transfer_status = _as_text(h5f.attrs.get("transfer_status", "")).lower()
    if transfer_status in {"not_complete", "failed", "error"}:
        return False, f"transfer_status={transfer_status}"

    container_state = _as_text(h5f.attrs.get("container_state", "")).lower()
    session_state = _as_text(h5f.attrs.get("session_state", "")).lower()
    bad_states = {"error", "failed", "rejected", "rejected_blocked"}
    if container_state in bad_states:
        return False, f"container_state={container_state}"
    if session_state in {"draft", "recovery_required"}:
        return False, f"session_state={session_state}"

    measurements_group = h5f.get("/entry/measurements")
    if measurements_group is None:
        return False, "missing /entry/measurements"

    processed_count = 0
    for point_group in measurements_group.values():
        if not isinstance(point_group, h5py.Group):
            continue
        for measurement_group in point_group.values():
            if not isinstance(measurement_group, h5py.Group):
                continue
            status = _as_text(
                measurement_group.attrs.get("measurement_status", "completed")
            ).lower()
            if status in {"failed", "aborted"}:
                continue
            for det_group in measurement_group.values():
                if isinstance(det_group, h5py.Group) and "processed_signal" in det_group:
                    processed_count += 1
    if processed_count <= 0:
        return False, "no processed_signal measurements"
    return True, ""


def _container_distance_cm(h5f: h5py.File) -> Optional[float]:
    for attr_name in ("distance_cm", "distanceCm", "technical_distance_cm"):
        try:
            value = h5f.attrs.get(attr_name)
            if value not in (None, ""):
                return float(value)
        except Exception:
            continue
    return None


def _detector_group(alias: str, detector_name: str) -> str:
    token = f"{alias} {detector_name}".upper()
    if any(item in token for item in ("PRIMARY", "SAXS", "DET_PRIMARY", "DET_SAXS")):
        return "PRIMARY"
    if any(item in token for item in ("SECONDARY", "WAXS", "DET_SECONDARY", "DET_WAXS")):
        return "SECONDARY"
    return _safe_token(str(alias or detector_name or "DETECTOR").upper(), "DETECTOR")


def _detector_side_label(detector_group: str, alias: str, detector_name: str) -> str:
    token = f"{detector_group} {alias} {detector_name}".upper()
    if any(
        item in token for item in ("PRIMARY", "LEFT", "SAXS", "DET_PRIMARY", "DET_SAXS")
    ):
        return "LEFT"
    if any(
        item in token
        for item in ("SECONDARY", "RIGHT", "WAXS", "DET_SECONDARY", "DET_WAXS")
    ):
        return "RIGHT"
    return ""


def _detector_range_config(
    detector_group: str,
    alias: str,
    detector_name: str,
    distance_cm: Optional[float] = None,
) -> Tuple[str, Tuple[float, float], str, str]:
    if distance_cm is not None:
        try:
            distance = float(distance_cm)
        except Exception:
            distance = None
        if distance is not None and np.isfinite(distance):
            if distance >= SAXS_DISTANCE_THRESHOLD_CM:
                return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", f"distance_cm={distance:g}"
            return "WAXS", WAXS_RANGE, "WAXS 2-21 nm^-1", f"distance_cm={distance:g}"

    token = f"{detector_group} {alias} {detector_name}".upper()
    if any(
        item in token
        for item in ("SECONDARY", "WAXS", "RIGHT", "DET_SECONDARY", "DET_WAXS")
    ):
        return "WAXS", WAXS_RANGE, "WAXS 2-21 nm^-1", "alias/name matched WAXS/SECONDARY"
    if any(
        item in token for item in ("PRIMARY", "SAXS", "LEFT", "DET_PRIMARY", "DET_SAXS")
    ):
        return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", "alias/name matched SAXS/PRIMARY"
    return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", "default: alias/name did not identify SAXS or WAXS"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _sha256_array(value: np.ndarray) -> str:
    arr = np.ascontiguousarray(np.asarray(value))
    digest = hashlib.sha256()
    digest.update(str(arr.dtype).encode("utf-8"))
    digest.update(str(tuple(arr.shape)).encode("utf-8"))
    digest.update(arr.tobytes())
    return digest.hexdigest()


def _array_stats(value: np.ndarray) -> Tuple[float, float, float]:
    arr = np.asarray(value, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return float("nan"), float("nan"), float("nan")
    return (
        float(np.nanmin(finite)),
        float(np.nanmedian(finite)),
        float(np.nanmax(finite)),
    )


def _integration_backend_name() -> str:
    try:
        from difra.gui.technical.analysis_compat import backend_name

        return str(backend_name())
    except Exception:
        return "unknown"


def _candidate_poni_infos(
    h5f: h5py.File,
    det_group: h5py.Group,
    alias: str,
) -> List[Tuple[str, str]]:
    candidates = []
    role = str(det_group.name.rsplit("/", 1)[-1])
    detector_id = _as_text(det_group.attrs.get("detector_id")).strip().lower()
    detector_alias = _as_text(det_group.attrs.get("detector_alias")).strip().lower()
    for attr_name in ("poni_ref", "poni_path"):
        ref = _as_text(det_group.attrs.get(attr_name)).strip()
        if not ref:
            continue
        candidates.append(ref)
        if not ref.startswith("/"):
            candidates.append(f"/entry/technical/poni/{ref}")
            candidates.append(f"/entry/technical/poni/poni_{ref}")
    if detector_id:
        candidates.extend(
            [
                f"/entry/technical/poni/poni_det_{detector_id}",
                f"/entry/technical/poni/poni_{detector_id}",
            ]
        )
    if role.startswith("det_"):
        candidates.append(f"/entry/technical/poni/poni_{role}")
        candidates.append(f"/entry/technical/poni/poni_{role[4:]}")
    tokens = {
        alias.strip().lower(),
        role.lower(),
        role.replace("det_", "").lower(),
        detector_alias,
        detector_id,
    }
    poni_group = h5f.get("/entry/technical/poni")
    if poni_group is not None:
        for name in sorted(poni_group.keys()):
            lower_name = name.lower()
            if any(token and token in lower_name for token in tokens):
                candidates.append(f"/entry/technical/poni/{name}")
    seen = set()
    found: List[Tuple[str, str]] = []
    for candidate in candidates:
        if not candidate or candidate in seen or candidate not in h5f:
            continue
        seen.add(candidate)
        text = _as_text(h5f[candidate][()]).strip()
        if text:
            found.append((text, candidate))
    return found


def _resolve_poni_info(h5f: h5py.File, det_group: h5py.Group, alias: str) -> Tuple[str, str]:
    candidates = _candidate_poni_infos(h5f, det_group, alias)
    if candidates:
        return candidates[0]
    return "", ""


def _resolve_poni_text(h5f: h5py.File, det_group: h5py.Group, alias: str) -> str:
    text, _source = _resolve_poni_info(h5f, det_group, alias)
    return text


def collect_report_series(
    container_paths: Iterable[Path],
    *,
    points: int = DEFAULT_POINTS,
) -> Tuple[List[DetectorSeries], List[str], int]:
    series: List[DetectorSeries] = []
    skipped: List[str] = []
    valid_count = 0
    for container_path in container_paths:
        path = Path(container_path)
        try:
            with h5py.File(path, "r") as h5f:
                is_valid, reason = _is_container_valid(h5f)
                if not is_valid:
                    skipped.append(f"{path.name}: {reason}")
                    continue
                valid_count += 1
                specimen_id = _as_text(
                    h5f.attrs.get("specimenId", h5f.attrs.get("sample_id", "unknown")),
                    "unknown",
                )
                distance_cm = _container_distance_cm(h5f)
                measurements_group = h5f.get("/entry/measurements")
                for point_name in sorted(measurements_group.keys()):
                    point_group = measurements_group[point_name]
                    if not isinstance(point_group, h5py.Group):
                        continue
                    for measurement_name in sorted(point_group.keys()):
                        measurement_group = point_group[measurement_name]
                        if not isinstance(measurement_group, h5py.Group):
                            continue
                        status = _as_text(
                            measurement_group.attrs.get("measurement_status", "completed")
                        ).lower()
                        if status in {"failed", "aborted"}:
                            continue
                        for det_name in sorted(measurement_group.keys()):
                            det_group = measurement_group[det_name]
                            if not isinstance(det_group, h5py.Group):
                                continue
                            if "processed_signal" not in det_group:
                                continue
                            alias = _as_text(
                                det_group.attrs.get(
                                    "detector_alias",
                                    str(det_name).replace("det_", "").upper(),
                                )
                            ).upper()
                            group_name = _detector_group(alias, str(det_name))
                            side = _detector_side_label(group_name, alias, str(det_name))
                            range_name, q_range, range_label, range_reason = (
                                _detector_range_config(
                                    group_name,
                                    alias,
                                    str(det_name),
                                    distance_cm=distance_cm,
                                )
                            )
                            signal_ds = det_group["processed_signal"]
                            signal = signal_ds[()]
                            signal_sha256 = _sha256_array(signal)
                            signal_min, signal_median, signal_max = _array_stats(signal)
                            integration_backend = _integration_backend_name()
                            best: Optional[
                                Tuple[str, str, np.ndarray, np.ndarray, float]
                            ] = None
                            candidates = _candidate_poni_infos(h5f, det_group, alias)
                            for candidate_poni_text, candidate_poni_source in (
                                candidates or [("", "")]
                            ):
                                q, intensity = integrate_detector_signal(
                                    signal,
                                    candidate_poni_text,
                                    npt=points,
                                    q_range=q_range,
                                )
                                if not _integrated_range_is_complete(
                                    q,
                                    intensity,
                                    q_range,
                                    points=points,
                                ):
                                    continue
                                signal_fraction = _integrated_signal_fraction(intensity)
                                candidate = (
                                    candidate_poni_text,
                                    candidate_poni_source,
                                    q,
                                    intensity,
                                    signal_fraction,
                                )
                                if best is None or signal_fraction > best[4]:
                                    best = candidate
                                if signal_fraction >= 0.5:
                                    break
                            if best is None:
                                skipped.append(
                                    f"{path.name}:{det_group.name}: no q data in {q_range[0]}-{q_range[1]} nm^-1"
                                )
                                continue
                            poni_text, poni_source, q, intensity, _signal_fraction = best
                            series.append(
                                DetectorSeries(
                                    specimen_id=specimen_id,
                                    detector_group=group_name,
                                    detector_alias=alias,
                                    detector_name=str(det_name),
                                    detector_side=side,
                                    range_name=range_name,
                                    q_range=q_range,
                                    range_label=range_label,
                                    range_assignment=range_reason,
                                    q=q,
                                    intensity=intensity,
                                    poni_text=poni_text,
                                    poni_source=poni_source,
                                    poni_sha256=_sha256_text(poni_text) if poni_text else "",
                                    source_data_sha256=signal_sha256,
                                    source_data_shape=tuple(
                                        int(item) for item in np.asarray(signal).shape
                                    ),
                                    source_data_min=signal_min,
                                    source_data_median=signal_median,
                                    source_data_max=signal_max,
                                    source_data=np.asarray(signal),
                                    integration_backend=integration_backend,
                                    source_container=path,
                                    source_dataset=signal_ds.name,
                                )
                            )
        except Exception as exc:
            skipped.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return series, skipped, valid_count


def summarize_valid_containers(container_paths: Iterable[Path]) -> Dict[str, Any]:
    project_ids: List[str] = []
    valid_containers = 0
    matador_uploaded = 0
    for container_path in container_paths:
        try:
            with h5py.File(container_path, "r") as h5f:
                is_valid, _reason = _is_container_valid(h5f)
                if not is_valid:
                    continue
                valid_containers += 1
                project_id = _as_text(
                    h5f.attrs.get(
                        "matadorProjectId",
                        h5f.attrs.get(
                            "project_id", h5f.attrs.get("matadorProjectName", "")
                        ),
                    )
                )
                if project_id and project_id not in project_ids:
                    project_ids.append(project_id)
                upload_status = _as_text(h5f.attrs.get("upload_status", "")).lower()
                matador_send_status = _as_text(
                    h5f.attrs.get("matador_send_status", "")
                ).lower()
                transfer_status = _as_text(h5f.attrs.get("transfer_status", "")).lower()
                if (
                    upload_status == "success"
                    or matador_send_status == "successful"
                    or transfer_status == "sent"
                ):
                    matador_uploaded += 1
        except Exception:
            continue
    return {
        "projectIds": project_ids,
        "validContainers": valid_containers,
        "matadorUploaded": matador_uploaded,
    }
