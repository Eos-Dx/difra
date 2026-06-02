"""Daily valid session-container plot report email."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import h5py
import numpy as np

import matplotlib

matplotlib.use("Agg")


from .daily_report_config import _as_text, _safe_token

DEFAULT_REPORT_RECIPIENT = "sdenisov@matur.co.uk"
DEFAULT_REPORT_SENDER = "difra-upload@company.co.uk"
DEFAULT_POINTS = 100
DEFAULT_DPI = 200
DEFAULT_KEYCHAIN_SERVICE = "difra_daily_report_smtp_password"
DEFAULT_EMAIL_SETUP_PASSWORD = "Ulster2025!"
DEFAULT_ENCRYPTED_PASSWORD_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "secrets"
    / "daily_report_smtp_password.enc.json"
)
DEFAULT_EMAIL_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "config"
    / "daily_report_email.json"
)
DEFAULT_REPORT_STATE_FILENAME = "daily_report_state.json"
SAXS_RANGE = (1.0, 3.0)
WAXS_RANGE = (2.0, 21.0)
SAXS_DISTANCE_THRESHOLD_CM = 10.0
@dataclass(frozen=True)
class DetectorSeries:
    specimen_id: str
    detector_group: str
    detector_alias: str
    detector_name: str
    detector_side: str
    range_name: str
    q_range: Tuple[float, float]
    range_label: str
    range_assignment: str
    q: np.ndarray
    intensity: np.ndarray
    poni_text: str
    poni_source: str
    poni_sha256: str
    source_data_sha256: str
    source_data_shape: Tuple[int, ...]
    source_data_min: float
    source_data_median: float
    source_data_max: float
    integration_backend: str
    source_container: Path
    source_dataset: str

    @property
    def detector_key(self) -> str:
        return _safe_token(
            "_".join(
                item
                for item in (
                    self.detector_alias,
                    self.detector_group,
                    self.detector_name,
                )
                if item
            ),
            "detector",
        )


def _candidate_containers(roots: Iterable[Path], *, since: Optional[datetime]) -> List[Path]:
    paths: List[Path] = []
    min_mtime = since.timestamp() if since is not None else None
    for root in roots:
        folder = Path(root)
        if not folder.exists():
            continue
        for path in folder.rglob("*.h5"):
            name_upper = path.name.upper()
            if "H5OLD" in name_upper:
                continue
            if not (path.name.endswith(".nxs.h5") or path.name.endswith(".h5")):
                continue
            if min_mtime is not None and path.stat().st_mtime < min_mtime:
                continue
            paths.append(path)
    return sorted(set(paths))


def _container_report_datetime(path: Path) -> Optional[datetime]:
    try:
        with h5py.File(path, "r") as h5f:
            for attr_name in (
                "acquisition_date",
                "creation_timestamp",
                "created_at",
                "timestamp_start",
                "lock_timestamp",
                "archived_timestamp",
            ):
                text = _as_text(h5f.attrs.get(attr_name), "").strip()
                if not text:
                    continue
                for candidate in (
                    text,
                    text.replace("Z", ""),
                    text.replace(" ", "T"),
                ):
                    try:
                        return datetime.fromisoformat(candidate)
                    except Exception:
                        continue
    except Exception:
        return None
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime)
    except Exception:
        return None


def _filter_containers_for_date(container_paths: Iterable[Path], report_date: date) -> List[Path]:
    target = report_date.isoformat()
    selected: List[Path] = []
    for path in container_paths:
        stamp = _container_report_datetime(Path(path))
        if stamp is not None and stamp.date().isoformat() == target:
            selected.append(Path(path))
    return sorted(set(selected))


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
            status = _as_text(measurement_group.attrs.get("measurement_status", "completed")).lower()
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
    if any(item in token for item in ("PRIMARY", "LEFT", "SAXS", "DET_PRIMARY", "DET_SAXS")):
        return "LEFT"
    if any(item in token for item in ("SECONDARY", "RIGHT", "WAXS", "DET_SECONDARY", "DET_WAXS")):
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
    if any(item in token for item in ("SECONDARY", "WAXS", "RIGHT", "DET_SECONDARY", "DET_WAXS")):
        return "WAXS", WAXS_RANGE, "WAXS 2-21 nm^-1", "alias/name matched WAXS/SECONDARY"
    if any(item in token for item in ("PRIMARY", "SAXS", "LEFT", "DET_PRIMARY", "DET_SAXS")):
        return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", "alias/name matched SAXS/PRIMARY"
    return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", "default: alias/name did not identify SAXS or WAXS"


def _detector_sort_key(item: DetectorSeries) -> Tuple[int, str]:
    token = f"{item.detector_group} {item.detector_alias} {item.detector_side}".upper()
    if any(part in token for part in ("PRIMARY", "LEFT", "SAXS")):
        return (0, f"{item.detector_alias} {item.detector_name}")
    if any(part in token for part in ("SECONDARY", "RIGHT", "WAXS")):
        return (1, f"{item.detector_alias} {item.detector_name}")
    return (2, f"{item.detector_alias} {item.detector_name}")


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
        candidates.extend(
            [
                f"/entry/technical/poni/poni_{role}",
            ]
        )
    if role.startswith("det_"):
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


def _report_image_name(specimen_id: str) -> str:
    return f"{_safe_token(specimen_id)}_detectors.png"


def _poni_arcname(item: DetectorSeries) -> str:
    if not item.poni_text.strip():
        return ""
    source_token = _safe_token(
        str(item.source_dataset or "").replace("/entry/measurements/", ""),
        "measurement",
    )
    hash_token = str(item.poni_sha256 or "")[:12] or "nohash"
    return (
        "poni/"
        f"{_safe_token(item.specimen_id)}_"
        f"{_safe_token(item.detector_group)}_"
        f"{_safe_token(item.detector_name)}_"
        f"{_safe_token(item.detector_side)}_"
        f"{source_token}_{hash_token}.poni"
    )


def _write_report_poni_files(series: Iterable[DetectorSeries], output_dir: Path) -> Dict[str, Path]:
    output = Path(output_dir)
    files: Dict[str, Path] = {}
    for item in series:
        arcname = _poni_arcname(item)
        if not arcname or arcname in files:
            continue
        path = output / arcname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item.poni_text, encoding="utf-8")
        files[arcname] = path
    return files


def build_report_manifest_diagnostics(
    series: Iterable[DetectorSeries],
    *,
    poni_files: Dict[str, Path],
) -> Dict[str, Any]:
    grouped: Dict[str, List[DetectorSeries]] = {}
    for item in series:
        grouped.setdefault(item.specimen_id, []).append(item)

    image_entries = []
    series_entries = []
    poni_entries = {}
    for specimen_id, items in sorted(grouped.items()):
        image_file = _report_image_name(specimen_id)
        detector_panels = []
        for detector_key in sorted({item.detector_key for item in items}):
            panel_items = [item for item in items if item.detector_key == detector_key]
            if not panel_items:
                continue
            first = sorted(panel_items, key=_detector_sort_key)[0]
            detector_panels.append(
                {
                    "detectorAlias": first.detector_alias,
                    "detectorName": first.detector_name,
                    "detectorGroup": first.detector_group,
                    "detectorSide": first.detector_side,
                    "rangeName": first.range_name,
                    "qRangeNm^-1": [float(first.q_range[0]), float(first.q_range[1])],
                    "rangeAssignment": first.range_assignment,
                    "seriesCount": len(panel_items),
                }
            )
        image_entries.append(
            {
                "imageFile": image_file,
                "specimenId": specimen_id,
                "layout": "one subplot per detector alias; PRIMARY/LEFT panels are ordered before SECONDARY/RIGHT panels",
                "detectorPanels": detector_panels,
                "seriesCount": len(items),
            }
        )
        for detector_key in sorted({item.detector_key for item in items}):
            panel_items = sorted(
                [item for item in items if item.detector_key == detector_key],
                key=lambda item: item.source_dataset,
            )
            for panel_index, item in enumerate(panel_items, start=1):
                poni_arcname = _poni_arcname(item)
                if poni_arcname:
                    poni_entries[poni_arcname] = {
                        "poniFile": poni_arcname,
                        "poniSource": item.poni_source,
                        "poniSha256": item.poni_sha256,
                        "presentInZip": poni_arcname in poni_files,
                    }
                side = f" {item.detector_side}" if item.detector_side else ""
                series_entries.append(
                    {
                        "imageFile": image_file,
                        "seriesIndex": panel_index,
                        "label": f"{item.detector_alias}{side} #{panel_index}",
                        "specimenId": item.specimen_id,
                        "detectorGroup": item.detector_group,
                        "detectorSide": item.detector_side,
                        "detectorAlias": item.detector_alias,
                        "detectorName": item.detector_name,
                        "rangeName": item.range_name,
                        "rangeAssignment": item.range_assignment,
                        "qRangeNm^-1": [float(item.q_range[0]), float(item.q_range[1])],
                        "sourceContainer": str(item.source_container),
                        "sourceDataset": item.source_dataset,
                        "sourceDataSha256": item.source_data_sha256,
                        "sourceDataShape": list(item.source_data_shape),
                        "sourceDataMin": item.source_data_min,
                        "sourceDataMedian": item.source_data_median,
                        "sourceDataMax": item.source_data_max,
                        "integrationBackend": item.integration_backend,
                        "poniSource": item.poni_source,
                        "poniFile": poni_arcname,
                        "poniSha256": item.poni_sha256,
                    }
                )
    return {
        "images": image_entries,
        "series": series_entries,
        "poniFiles": sorted(poni_entries.values(), key=lambda item: item["poniFile"]),
    }


def integrate_detector_signal(
    data: np.ndarray,
    poni_text: str,
    *,
    npt: int = DEFAULT_POINTS,
    q_range: Optional[Tuple[float, float]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or not poni_text.strip():
        return np.asarray([]), np.asarray([])
    try:
        from difra.gui.technical.analysis_compat import (
            initialize_azimuthal_integrator_poni_text,
        )

        ai = initialize_azimuthal_integrator_poni_text(poni_text)
        kwargs: Dict[str, Any] = {}
        if q_range is not None:
            kwargs["radial_range"] = (float(q_range[0]), float(q_range[1]))
        result = ai.integrate1d(
            arr,
            max(int(npt), 2),
            unit="q_nm^-1",
            error_model="azimuthal",
            **kwargs,
        )
        q = np.asarray(result.radial, dtype=float).reshape(-1)
        intensity = np.asarray(result.intensity, dtype=float).reshape(-1)
        finite = np.isfinite(q) & np.isfinite(intensity)
        return q[finite], intensity[finite]
    except Exception:
        return np.asarray([]), np.asarray([])


def _resample_range(
    q: np.ndarray,
    intensity: np.ndarray,
    q_range: Tuple[float, float],
    *,
    points: int,
) -> Tuple[np.ndarray, np.ndarray]:
    q = np.asarray(q, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    finite = np.isfinite(q) & np.isfinite(intensity)
    q = q[finite]
    intensity = intensity[finite]
    if q.size < 2:
        return np.asarray([]), np.asarray([])
    order = np.argsort(q)
    q = q[order]
    intensity = intensity[order]
    if q[0] > float(q_range[0]) or q[-1] < float(q_range[1]):
        return np.asarray([]), np.asarray([])
    mask = (q >= float(q_range[0])) & (q <= float(q_range[1]))
    if np.count_nonzero(mask) < 2:
        return np.asarray([]), np.asarray([])
    target_q = np.linspace(float(q_range[0]), float(q_range[1]), int(points))
    target_i = np.interp(target_q, q[mask], intensity[mask])
    return target_q, target_i


def _integrated_range_is_complete(
    q: np.ndarray,
    intensity: np.ndarray,
    q_range: Tuple[float, float],
    *,
    points: int,
) -> bool:
    q = np.asarray(q, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    if q.size != int(points) or intensity.size != int(points):
        return False
    if not np.all(np.isfinite(q)) or not np.all(np.isfinite(intensity)):
        return False
    q_min = float(np.nanmin(q))
    q_max = float(np.nanmax(q))
    return q_min >= float(q_range[0]) - 1e-6 and q_max <= float(q_range[1]) + 1e-6


def _integrated_signal_fraction(intensity: np.ndarray) -> float:
    values = np.asarray(intensity, dtype=float).reshape(-1)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0
    scale = float(np.nanmax(np.abs(finite)))
    if scale <= 0.0:
        return 0.0
    threshold = max(scale * 1e-6, 1e-12)
    return float(np.count_nonzero(np.abs(finite) > threshold) / finite.size)


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
                            best: Optional[Tuple[str, str, np.ndarray, np.ndarray, float]] = None
                            for candidate_poni_text, candidate_poni_source in (
                                _daily_report_dependency("_candidate_poni_infos")(h5f, det_group, alias) or [("", "")]
                            ):
                                q, intensity = _daily_report_dependency("integrate_detector_signal")(
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
                                    source_data_shape=tuple(int(item) for item in np.asarray(signal).shape),
                                    source_data_min=signal_min,
                                    source_data_median=signal_median,
                                    source_data_max=signal_max,
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
                        h5f.attrs.get("project_id", h5f.attrs.get("matadorProjectName", "")),
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




def _daily_report_dependency(name: str):
    from difra.gui import daily_valid_container_reporter

    return getattr(daily_valid_container_reporter, name)
