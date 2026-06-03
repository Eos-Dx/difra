"""HDF5 data readers for the session container viewer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np


@dataclass(frozen=True)
class MeasurementRecord:
    point: str
    measurement: str
    detector: str
    alias: str
    detector_id: str
    dataset_path: str
    shape: str
    dtype: str
    integration_time_ms: str
    n_frames: str
    status: str


@dataclass(frozen=True)
class ImageRecord:
    name: str
    dataset_path: str
    shape: str
    dtype: str


@dataclass(frozen=True)
class AnalyticalRecord:
    name: str
    path: str
    analysis_type: str
    analysis_role: str
    linked_points: str
    detectors: str


@dataclass(frozen=True)
class AbsorptionRecord:
    point: str
    analytical: str
    detector: str
    alias: str
    i0_path: str
    i_path: str
    shape: str
    minimum: str
    maximum: str
    mean: str


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.ndarray):
        if value.shape == ():
            return _as_text(value.item())
        if value.size <= 12:
            return repr(value.tolist())
        return f"array shape={value.shape} dtype={value.dtype}"
    return str(value)


def _attrs_to_text(attrs: Any) -> str:
    rows = []
    for key in sorted(attrs.keys()):
        rows.append(f"{key}: {_as_text(attrs.get(key))}")
    return "\n".join(rows)


def _object_kind(obj: Any) -> str:
    if isinstance(obj, h5py.Dataset):
        return "dataset"
    if isinstance(obj, h5py.Group):
        return "group"
    return type(obj).__name__


def _format_shape(obj: Any) -> str:
    shape = getattr(obj, "shape", None)
    if shape is None:
        return ""
    return "x".join(str(part) for part in shape)


def _numeric_stats(arr: np.ndarray) -> str:
    numeric = np.asarray(arr)
    if numeric.size == 0 or not np.issubdtype(numeric.dtype, np.number):
        return ""
    finite = numeric[np.isfinite(numeric)]
    if finite.size == 0:
        return "min=nan max=nan mean=nan"
    return (
        f"min={float(np.min(finite)):.6g} "
        f"max={float(np.max(finite)):.6g} "
        f"mean={float(np.mean(finite)):.6g}"
    )


def _numeric_stat_fields(arr: np.ndarray) -> tuple[str, str, str]:
    numeric = np.asarray(arr, dtype=float)
    finite = numeric[np.isfinite(numeric)]
    if finite.size == 0:
        return "nan", "nan", "nan"
    return (
        f"{float(np.min(finite)):.6g}",
        f"{float(np.max(finite)):.6g}",
        f"{float(np.mean(finite)):.6g}",
    )


def _safe_dataset_sample(dataset: h5py.Dataset, max_rows: int = 60, max_cols: int = 16):
    if dataset.shape == ():
        return dataset[()]
    if len(dataset.shape) == 1:
        return dataset[: min(int(dataset.shape[0]), max_rows)]
    if len(dataset.shape) == 2:
        return dataset[
            : min(int(dataset.shape[0]), max_rows),
            : min(int(dataset.shape[1]), max_cols),
        ]
    if len(dataset.shape) == 3:
        return dataset[
            : min(int(dataset.shape[0]), max_rows),
            : min(int(dataset.shape[1]), max_cols),
            : min(int(dataset.shape[2]), 4),
        ]
    slices = tuple(slice(0, min(int(size), 4)) for size in dataset.shape)
    return dataset[slices]


def read_container_summary(container_path: Path) -> dict[str, Any]:
    path = Path(container_path)
    with h5py.File(path, "r") as h5f:
        root_attrs = {key: _as_text(value) for key, value in h5f.attrs.items()}
        measurement_count = 0
        detector_count = 0
        measurements_group = h5f.get("/entry/measurements")
        if measurements_group is not None:
            for point_group in measurements_group.values():
                for measurement_group in point_group.values():
                    measurement_count += 1
                    detector_count += sum(
                        1
                        for det_group in measurement_group.values()
                        if isinstance(det_group, h5py.Group)
                        and "processed_signal" in det_group
                    )
        image_count = 0
        images_group = h5f.get("/entry/images")
        if images_group is not None:
            for image_group in images_group.values():
                if isinstance(image_group, h5py.Group) and "data" in image_group:
                    image_count += 1
        analytical_group = h5f.get("/entry/analytical_measurements")
        analytical_count = len(analytical_group.keys()) if analytical_group else 0
    return {
        "path": str(path),
        "attrs": root_attrs,
        "measurement_count": measurement_count,
        "detector_count": detector_count,
        "image_count": image_count,
        "analytical_count": analytical_count,
    }


def collect_measurements(container_path: Path) -> list[MeasurementRecord]:
    records: list[MeasurementRecord] = []
    with h5py.File(container_path, "r") as h5f:
        measurements_group = h5f.get("/entry/measurements")
        if measurements_group is None:
            return records
        for point_name in sorted(measurements_group.keys()):
            point_group = measurements_group[point_name]
            for measurement_name in sorted(point_group.keys()):
                measurement_group = point_group[measurement_name]
                status = _as_text(measurement_group.attrs.get("measurement_status"))
                for detector_name in sorted(measurement_group.keys()):
                    detector_group = measurement_group[detector_name]
                    if not isinstance(detector_group, h5py.Group):
                        continue
                    dataset = detector_group.get("processed_signal")
                    if dataset is None:
                        continue
                    records.append(
                        MeasurementRecord(
                            point=point_name,
                            measurement=measurement_name,
                            detector=detector_name,
                            alias=_as_text(
                                detector_group.attrs.get(
                                    "detector_alias",
                                    detector_name.replace("det_", "").upper(),
                                )
                            ),
                            detector_id=_as_text(
                                detector_group.attrs.get("detector_id")
                            ),
                            dataset_path=dataset.name,
                            shape=_format_shape(dataset),
                            dtype=str(dataset.dtype),
                            integration_time_ms=_as_text(
                                detector_group.attrs.get("integration_time_ms")
                            ),
                            n_frames=_as_text(detector_group.attrs.get("n_frames")),
                            status=status,
                        )
                    )
    return records


def collect_images(container_path: Path) -> list[ImageRecord]:
    records: list[ImageRecord] = []
    with h5py.File(container_path, "r") as h5f:
        images_group = h5f.get("/entry/images")
        if images_group is None:
            return records
        for name in sorted(images_group.keys()):
            image_group = images_group[name]
            if not isinstance(image_group, h5py.Group):
                continue
            dataset = image_group.get("data")
            if dataset is None:
                continue
            records.append(
                ImageRecord(
                    name=name,
                    dataset_path=dataset.name,
                    shape=_format_shape(dataset),
                    dtype=str(dataset.dtype),
                )
            )
    return records


def collect_analytical(container_path: Path) -> list[AnalyticalRecord]:
    records: list[AnalyticalRecord] = []
    with h5py.File(container_path, "r") as h5f:
        group = h5f.get("/entry/analytical_measurements")
        if group is None:
            return records
        for name in sorted(group.keys()):
            item = group[name]
            detectors = []
            for child_name, child in item.items():
                if isinstance(child, h5py.Group) and "processed_signal" in child:
                    detectors.append(child_name)
            records.append(
                AnalyticalRecord(
                    name=name,
                    path=item.name,
                    analysis_type=_as_text(item.attrs.get("analysis_type")),
                    analysis_role=_as_text(item.attrs.get("analysis_role")),
                    linked_points=_as_text(item.attrs.get("linked_points")),
                    detectors=", ".join(detectors),
                )
            )
    return records


def _point_ids_from_attrs(attrs: Any) -> list[str]:
    for attr_name in ("point_ids", "linked_points"):
        raw = attrs.get(attr_name)
        if raw is None:
            continue
        if isinstance(raw, np.ndarray):
            return [_as_text(value) for value in raw.reshape(-1) if _as_text(value)]
        text = _as_text(raw).strip()
        if text:
            return [
                part.strip()
                for part in text.replace(";", ",").split(",")
                if part.strip()
            ]
    return []


def _detector_is_secondary(
    detector: str, alias: str = "", detector_id: str = ""
) -> bool:
    token = f"{detector} {alias} {detector_id}".lower()
    return "secondary" in token or "waxs" in token


def calculate_absorption_image(
    with_sample: np.ndarray,
    without_sample: np.ndarray,
) -> np.ndarray:
    i = np.asarray(with_sample, dtype=float)
    i0 = np.asarray(without_sample, dtype=float)
    if i.shape != i0.shape:
        raise ValueError(f"Absorption shape mismatch: I={i.shape}, I0={i0.shape}")
    positive_i0 = i0[np.isfinite(i0) & (i0 > 0)]
    eps = float(np.nanmin(positive_i0)) * 1e-12 if positive_i0.size else 1e-12
    ratio = np.divide(
        np.maximum(i, eps),
        np.maximum(i0, eps),
        out=np.full_like(i, np.nan, dtype=float),
        where=np.isfinite(i) & np.isfinite(i0),
    )
    return -np.log(np.clip(ratio, eps, None))


def _iter_detector_datasets(analytical_item: h5py.Group):
    for detector_name in sorted(analytical_item.keys()):
        detector_group = analytical_item[detector_name]
        if not isinstance(detector_group, h5py.Group):
            continue
        dataset = detector_group.get("processed_signal")
        if dataset is None:
            continue
        yield detector_name, detector_group, dataset


def collect_absorption_records(container_path: Path) -> list[AbsorptionRecord]:
    records: list[AbsorptionRecord] = []
    with h5py.File(container_path, "r") as h5f:
        group = h5f.get("/entry/analytical_measurements")
        if group is None:
            return records
        i0_by_detector: dict[str, str] = {}
        i0_by_alias: dict[str, str] = {}
        i_items: list[tuple[str, h5py.Group]] = []
        for name in sorted(group.keys()):
            item = group[name]
            if not isinstance(item, h5py.Group):
                continue
            analysis_type = _as_text(item.attrs.get("analysis_type")).strip().lower()
            analysis_role = _as_text(item.attrs.get("analysis_role")).strip().lower()
            if not analysis_type.startswith("attenuation"):
                continue
            if analysis_role in {"i0", "without", "without_sample"}:
                for detector_name, detector_group, dataset in _iter_detector_datasets(
                    item
                ):
                    alias = _as_text(detector_group.attrs.get("detector_alias"))
                    i0_by_detector[detector_name] = dataset.name
                    if alias:
                        i0_by_alias[alias.lower()] = dataset.name
            elif analysis_role in {"i", "with", "with_sample"}:
                i_items.append((name, item))

        for analytical_name, item in i_items:
            points = _point_ids_from_attrs(item.attrs) or [analytical_name]
            point = ", ".join(points)
            for detector_name, detector_group, dataset in _iter_detector_datasets(item):
                alias = _as_text(detector_group.attrs.get("detector_alias"))
                i0_path = i0_by_detector.get(detector_name) or i0_by_alias.get(
                    alias.lower()
                )
                if not i0_path:
                    continue
                absorption = calculate_absorption_image(dataset[()], h5f[i0_path][()])
                minimum, maximum, mean = _numeric_stat_fields(absorption)
                records.append(
                    AbsorptionRecord(
                        point=point,
                        analytical=analytical_name,
                        detector=detector_name,
                        alias=alias,
                        i0_path=i0_path,
                        i_path=dataset.name,
                        shape=_format_shape(dataset),
                        minimum=minimum,
                        maximum=maximum,
                        mean=mean,
                    )
                )
    return records


def load_dataset(container_path: Path, dataset_path: str) -> np.ndarray:
    with h5py.File(container_path, "r") as h5f:
        return np.asarray(h5f[dataset_path][()])


def resolve_poni_text(container_path: Path, dataset_path: str, alias: str = "") -> str:
    with h5py.File(container_path, "r") as h5f:
        if dataset_path not in h5f:
            return ""
        dataset = h5f[dataset_path]
        detector_group = dataset.parent
        candidates = []
        for attr_name in ("poni_ref", "poni_path"):
            ref = _as_text(detector_group.attrs.get(attr_name)).strip()
            if ref:
                candidates.append(ref)
        role = str(detector_group.name.rsplit("/", 1)[-1])
        if role.startswith("det_"):
            candidates.extend(
                [
                    f"/entry/technical/poni/poni_{role}",
                    f"/entry/technical/poni/poni_{role[4:]}",
                ]
            )
        token_candidates = {
            alias.strip().lower(),
            role.lower(),
            role.replace("det_", "").lower(),
            _as_text(detector_group.attrs.get("detector_alias")).strip().lower(),
            _as_text(detector_group.attrs.get("detector_id")).strip().lower(),
        }
        poni_group = h5f.get("/entry/technical/poni")
        if poni_group is not None:
            for name in sorted(poni_group.keys()):
                lower_name = name.lower()
                if any(token and token in lower_name for token in token_candidates):
                    candidates.append(f"/entry/technical/poni/{name}")
        seen = set()
        for candidate in candidates:
            if not candidate or candidate in seen or candidate not in h5f:
                continue
            seen.add(candidate)
            value = h5f[candidate][()]
            text = _as_text(value).strip()
            if text:
                return text
    return ""
