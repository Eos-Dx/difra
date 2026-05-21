from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import h5py
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from difra.gui.qt_compat import (
    QAction,
    QtWidgets,
    QHeaderView,
    Qt,
    exec_app,
)

QApplication = QtWidgets.QApplication
QFileDialog = QtWidgets.QFileDialog
QLabel = QtWidgets.QLabel
QMainWindow = QtWidgets.QMainWindow
QMessageBox = QtWidgets.QMessageBox
QPlainTextEdit = QtWidgets.QPlainTextEdit
QPushButton = QtWidgets.QPushButton
QSplitter = QtWidgets.QSplitter
QTableWidget = QtWidgets.QTableWidget
QTableWidgetItem = QtWidgets.QTableWidgetItem
QTabWidget = QtWidgets.QTabWidget
QToolBar = QtWidgets.QToolBar
QTreeWidget = QtWidgets.QTreeWidget
QTreeWidgetItem = QtWidgets.QTreeWidgetItem
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget


DEFAULT_CONTAINER_PATH = Path(
    "/Users/sad/dev/Data/difra/archive/measurements/"
    "session_0cc7c313e48d4480_337533__338200_P206_S01_FL_20260426_nxs_"
    "Lynda_337533__338200_P206_S01_FL_Project_5_-_Grant_1_Extra_Mouse_Samples_"
    "20260426_152324/"
    "session_0cc7c313e48d4480_337533__338200_P206_S01_FL_20260426.nxs.h5"
)

HEADER_RESIZE_TO_CONTENTS = QHeaderView.ResizeToContents
HORIZONTAL = Qt.Horizontal
TEXT_SELECTABLE_BY_MOUSE = Qt.TextSelectableByMouse
USER_ROLE = Qt.UserRole


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
                            detector_id=_as_text(detector_group.attrs.get("detector_id")),
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
            return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
    return []


def _detector_is_secondary(detector: str, alias: str = "", detector_id: str = "") -> bool:
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
                for detector_name, detector_group, dataset in _iter_detector_datasets(item):
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
                i0_path = i0_by_detector.get(detector_name) or i0_by_alias.get(alias.lower())
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


def integrate_profile(
    data: np.ndarray,
    *,
    poni_text: str = "",
    npt: int = 200,
    unit: str = "q_nm^-1",
) -> tuple[np.ndarray, np.ndarray, str]:
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2:
        return np.arange(arr.size), arr.reshape(-1), "flattened"
    if poni_text.strip():
        try:
            from difra.gui.technical.analysis_compat import (
                initialize_azimuthal_integrator_poni_text,
            )

            ai = initialize_azimuthal_integrator_poni_text(poni_text)
            result = ai.integrate1d(arr, max(int(npt), 2), unit=unit, error_model="azimuthal")
            x = np.asarray(result.radial, dtype=float).reshape(-1)
            y = np.asarray(result.intensity, dtype=float).reshape(-1)
            finite = np.isfinite(x) & np.isfinite(y)
            return x[finite], y[finite], "pyFAI q"
        except Exception:
            pass
    yy, xx = np.indices(arr.shape)
    center_y = (arr.shape[0] - 1) / 2.0
    center_x = (arr.shape[1] - 1) / 2.0
    radius = np.sqrt((yy - center_y) ** 2 + (xx - center_x) ** 2)
    bins = np.linspace(0, float(radius.max()), max(int(npt), 2) + 1)
    which = np.digitize(radius.ravel(), bins) - 1
    values = arr.ravel()
    x_values = []
    y_values = []
    for idx in range(len(bins) - 1):
        mask = which == idx
        if not np.any(mask):
            continue
        x_values.append((bins[idx] + bins[idx + 1]) / 2.0)
        y_values.append(float(np.nanmean(values[mask])))
    return np.asarray(x_values), np.asarray(y_values), "radial pixels"


def _detector_npt(record: MeasurementRecord) -> int:
    return 100 if _detector_is_secondary(record.detector, record.alias, record.detector_id) else 200


class SessionContainerViewer(QMainWindow):
    def __init__(self, container_path: Optional[Path] = None):
        super().__init__()
        self.container_path: Optional[Path] = None
        self.measurements: list[MeasurementRecord] = []
        self.images: list[ImageRecord] = []
        self.analytical: list[AnalyticalRecord] = []
        self.absorption: list[AbsorptionRecord] = []
        self.setWindowTitle("DIFRA Session Container Viewer")
        self.resize(1500, 900)
        self._build_ui()
        if container_path:
            self.open_container(Path(container_path))

    def _build_ui(self):
        toolbar = QToolBar("Viewer")
        self.addToolBar(toolbar)
        open_action = QAction("Open", self)
        open_action.triggered.connect(self._choose_container)
        reload_action = QAction("Reload", self)
        reload_action.triggered.connect(self.reload_container)
        toolbar.addAction(open_action)
        toolbar.addAction(reload_action)

        self.path_label = QLabel("")
        self.path_label.setTextInteractionFlags(TEXT_SELECTABLE_BY_MOUSE)
        toolbar.addWidget(self.path_label)

        splitter = QSplitter(HORIZONTAL)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["HDF5 object", "kind", "shape"])
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        splitter.addWidget(self.tree)

        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        self.setCentralWidget(splitter)

        self._build_overview_tab()
        self._build_measurements_tab()
        self._build_absorption_tab()
        self._build_images_tab()
        self._build_analytical_tab()
        self._build_hdf5_tab()

    def _build_overview_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.overview_text = QPlainTextEdit()
        self.overview_text.setReadOnly(True)
        layout.addWidget(self.overview_text)
        self.tabs.addTab(tab, "Overview")

    def _build_measurements_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.measurements_table = QTableWidget()
        self.measurements_table.setColumnCount(10)
        self.measurements_table.setHorizontalHeaderLabels(
            [
                "Point",
                "Measurement",
                "Detector",
                "Alias",
                "Detector ID",
                "Shape",
                "dtype",
                "t ms",
                "frames",
                "Path",
            ]
        )
        self.measurements_table.horizontalHeader().setSectionResizeMode(
            HEADER_RESIZE_TO_CONTENTS
        )
        self.measurements_table.itemSelectionChanged.connect(self._plot_selected_measurement)
        layout.addWidget(self.measurements_table, 2)
        plot_all_btn = QPushButton("Plot All Profiles")
        plot_all_btn.clicked.connect(self._plot_all_profiles)
        layout.addWidget(plot_all_btn)
        self.measurement_fig = Figure(figsize=(8, 5), constrained_layout=True)
        self.measurement_canvas = FigureCanvas(self.measurement_fig)
        layout.addWidget(self.measurement_canvas, 3)
        self.tabs.addTab(tab, "Measurements")

    def _build_absorption_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.absorption_table = QTableWidget()
        self.absorption_table.setColumnCount(10)
        self.absorption_table.setHorizontalHeaderLabels(
            [
                "Point",
                "Analytical",
                "Detector",
                "Alias",
                "Shape",
                "min",
                "max",
                "mean",
                "I0 Path",
                "I Path",
            ]
        )
        self.absorption_table.horizontalHeader().setSectionResizeMode(
            HEADER_RESIZE_TO_CONTENTS
        )
        self.absorption_table.itemSelectionChanged.connect(self._plot_selected_absorption)
        layout.addWidget(self.absorption_table, 2)
        plot_all_btn = QPushButton("Plot All Absorption")
        plot_all_btn.clicked.connect(self._plot_all_absorption)
        layout.addWidget(plot_all_btn)
        self.absorption_fig = Figure(figsize=(8, 5), constrained_layout=True)
        self.absorption_canvas = FigureCanvas(self.absorption_fig)
        layout.addWidget(self.absorption_canvas, 3)
        self.tabs.addTab(tab, "Absorption")

    def _build_images_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.images_table = QTableWidget()
        self.images_table.setColumnCount(4)
        self.images_table.setHorizontalHeaderLabels(["Name", "Shape", "dtype", "Path"])
        self.images_table.horizontalHeader().setSectionResizeMode(HEADER_RESIZE_TO_CONTENTS)
        self.images_table.itemSelectionChanged.connect(self._plot_selected_image)
        layout.addWidget(self.images_table, 1)
        self.image_fig = Figure(figsize=(8, 5), constrained_layout=True)
        self.image_canvas = FigureCanvas(self.image_fig)
        layout.addWidget(self.image_canvas, 3)
        self.tabs.addTab(tab, "Images")

    def _build_analytical_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.analytical_table = QTableWidget()
        self.analytical_table.setColumnCount(6)
        self.analytical_table.setHorizontalHeaderLabels(
            ["Name", "Type", "Role", "Linked points", "Detectors", "Path"]
        )
        self.analytical_table.horizontalHeader().setSectionResizeMode(
            HEADER_RESIZE_TO_CONTENTS
        )
        layout.addWidget(self.analytical_table)
        self.tabs.addTab(tab, "Analytical")

    def _build_hdf5_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.hdf5_title = QLabel("Select HDF5 object")
        self.hdf5_title.setTextInteractionFlags(TEXT_SELECTABLE_BY_MOUSE)
        layout.addWidget(self.hdf5_title)
        self.hdf5_text = QPlainTextEdit()
        self.hdf5_text.setReadOnly(True)
        layout.addWidget(self.hdf5_text, 2)
        self.hdf5_fig = Figure(figsize=(8, 4), constrained_layout=True)
        self.hdf5_canvas = FigureCanvas(self.hdf5_fig)
        layout.addWidget(self.hdf5_canvas, 3)
        self.tabs.addTab(tab, "HDF5 Item")

    def _choose_container(self):
        start = str(self.container_path.parent if self.container_path else DEFAULT_CONTAINER_PATH.parent)
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open DIFRA session container",
            start,
            "NeXus/HDF5 (*.nxs.h5 *.h5);;All files (*)",
        )
        if filename:
            self.open_container(Path(filename))

    def reload_container(self):
        if self.container_path:
            self.open_container(self.container_path)

    def open_container(self, container_path: Path):
        path = Path(container_path)
        if not path.exists():
            QMessageBox.warning(self, "Missing container", str(path))
            return
        self.container_path = path
        self.path_label.setText(f"  {path}")
        self.setWindowTitle(f"DIFRA Session Container Viewer - {path.name}")
        self.measurements = collect_measurements(path)
        self.images = collect_images(path)
        self.analytical = collect_analytical(path)
        self.absorption = collect_absorption_records(path)
        self._populate_overview()
        self._populate_tree()
        self._populate_measurements_table()
        self._populate_absorption_table()
        self._populate_images_table()
        self._populate_analytical_table()
        self._plot_all_profiles()
        self._plot_all_absorption()

    def _populate_overview(self):
        if not self.container_path:
            return
        summary = read_container_summary(self.container_path)
        attrs = summary["attrs"]
        lines = [
            f"Path: {summary['path']}",
            f"Specimen: {attrs.get('specimenId') or attrs.get('sample_id')}",
            f"Study: {attrs.get('study_name')}",
            f"Project: {attrs.get('project_id') or attrs.get('matadorProjectName')}",
            f"Session ID: {attrs.get('session_id')}",
            f"Operator: {attrs.get('operator_id')}",
            f"Status: lock={attrs.get('lock_status')} transfer={attrs.get('transfer_status')} state={attrs.get('session_state')}",
            "",
            f"Point measurements: {summary['measurement_count']}",
            f"Detector measurement datasets: {summary['detector_count']}",
            f"Images: {summary['image_count']}",
            f"Analytical measurements: {summary['analytical_count']}",
            f"Absorption records: {len(self.absorption)}",
            "",
            "Root attributes:",
        ]
        for key in sorted(attrs.keys()):
            lines.append(f"{key}: {attrs[key]}")
        self.overview_text.setPlainText("\n".join(lines))

    def _populate_tree(self):
        self.tree.clear()
        if not self.container_path:
            return
        with h5py.File(self.container_path, "r") as h5f:
            root = QTreeWidgetItem(["/", "file", ""])
            root.setData(0, USER_ROLE, "/")
            self.tree.addTopLevelItem(root)
            self._add_tree_children(root, h5f)
            root.setExpanded(True)
        self.tree.resizeColumnToContents(0)

    def _add_tree_children(self, parent_item: QTreeWidgetItem, group: h5py.Group):
        for name in sorted(group.keys()):
            obj = group[name]
            item = QTreeWidgetItem([name, _object_kind(obj), _format_shape(obj)])
            item.setData(0, USER_ROLE, obj.name)
            parent_item.addChild(item)
            if isinstance(obj, h5py.Group):
                self._add_tree_children(item, obj)

    def _populate_measurements_table(self):
        self.measurements_table.setRowCount(len(self.measurements))
        for row, record in enumerate(self.measurements):
            values = [
                record.point,
                record.measurement,
                record.detector,
                record.alias,
                record.detector_id,
                record.shape,
                record.dtype,
                record.integration_time_ms,
                record.n_frames,
                record.dataset_path,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(USER_ROLE, record.dataset_path)
                self.measurements_table.setItem(row, col, item)

    def _populate_absorption_table(self):
        self.absorption_table.setRowCount(len(self.absorption))
        for row, record in enumerate(self.absorption):
            values = [
                record.point,
                record.analytical,
                record.detector,
                record.alias,
                record.shape,
                record.minimum,
                record.maximum,
                record.mean,
                record.i0_path,
                record.i_path,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(USER_ROLE, row)
                self.absorption_table.setItem(row, col, item)

    def _populate_images_table(self):
        self.images_table.setRowCount(len(self.images))
        for row, record in enumerate(self.images):
            for col, value in enumerate(
                [record.name, record.shape, record.dtype, record.dataset_path]
            ):
                item = QTableWidgetItem(value)
                item.setData(USER_ROLE, record.dataset_path)
                self.images_table.setItem(row, col, item)

    def _populate_analytical_table(self):
        self.analytical_table.setRowCount(len(self.analytical))
        for row, record in enumerate(self.analytical):
            for col, value in enumerate(
                [
                    record.name,
                    record.analysis_type,
                    record.analysis_role,
                    record.linked_points,
                    record.detectors,
                    record.path,
                ]
            ):
                item = QTableWidgetItem(value)
                item.setData(USER_ROLE, record.path)
                self.analytical_table.setItem(row, col, item)

    def _selected_measurement_record(self) -> Optional[MeasurementRecord]:
        rows = sorted({idx.row() for idx in self.measurements_table.selectedIndexes()})
        if not rows:
            return None
        row = rows[0]
        if row < 0 or row >= len(self.measurements):
            return None
        return self.measurements[row]

    def _selected_image_record(self) -> Optional[ImageRecord]:
        rows = sorted({idx.row() for idx in self.images_table.selectedIndexes()})
        if not rows:
            return None
        row = rows[0]
        if row < 0 or row >= len(self.images):
            return None
        return self.images[row]

    def _selected_absorption_record(self) -> Optional[AbsorptionRecord]:
        rows = sorted({idx.row() for idx in self.absorption_table.selectedIndexes()})
        if not rows:
            return None
        row = rows[0]
        if row < 0 or row >= len(self.absorption):
            return None
        return self.absorption[row]

    def _load_absorption_image(self, record: AbsorptionRecord) -> np.ndarray:
        if self.container_path is None:
            return np.asarray([])
        with h5py.File(self.container_path, "r") as h5f:
            return calculate_absorption_image(
                h5f[record.i_path][()],
                h5f[record.i0_path][()],
            )

    def _plot_selected_measurement(self):
        record = self._selected_measurement_record()
        if record is None or self.container_path is None:
            return
        data = load_dataset(self.container_path, record.dataset_path)
        poni_text = resolve_poni_text(
            self.container_path,
            record.dataset_path,
            alias=record.alias,
        )
        x, y, mode = integrate_profile(data, poni_text=poni_text, npt=_detector_npt(record))
        self.measurement_fig.clear()
        ax_img, ax_profile = self.measurement_fig.subplots(1, 2)
        if data.ndim == 2:
            img = np.asarray(data, dtype=float)
            ax_img.imshow(np.log1p(np.maximum(img, 0)), cmap="viridis")
        else:
            ax_img.text(0.5, 0.5, f"shape={data.shape}", ha="center", va="center")
        ax_img.set_title(f"{record.point} {record.detector}")
        ax_img.set_axis_off()
        finite = np.isfinite(x) & np.isfinite(y) & (y > 0)
        if np.count_nonzero(finite) >= 2:
            ax_profile.plot(x[finite], y[finite], linewidth=1.2)
            ax_profile.set_yscale("log")
        ax_profile.set_title(f"Profile ({mode})")
        ax_profile.set_xlabel("q nm^-1" if mode == "pyFAI q" else "radius px")
        ax_profile.set_ylabel("intensity")
        ax_profile.grid(True, alpha=0.25)
        self.measurement_canvas.draw_idle()

    def _plot_all_profiles(self):
        if self.container_path is None:
            return
        self.measurement_fig.clear()
        ax_primary, ax_secondary = self.measurement_fig.subplots(1, 2)
        buckets = [("primary", ax_primary), ("secondary", ax_secondary)]
        for bucket_name, axis in buckets:
            plotted = False
            for record in self.measurements:
                is_secondary = _detector_is_secondary(
                    record.detector,
                    record.alias,
                    record.detector_id,
                )
                if (bucket_name == "secondary") != is_secondary:
                    continue
                data = load_dataset(self.container_path, record.dataset_path)
                poni_text = resolve_poni_text(
                    self.container_path,
                    record.dataset_path,
                    alias=record.alias,
                )
                x, y, mode = integrate_profile(
                    data,
                    poni_text=poni_text,
                    npt=_detector_npt(record),
                )
                finite = np.isfinite(x) & np.isfinite(y) & (y > 0)
                if np.count_nonzero(finite) < 2:
                    continue
                axis.plot(x[finite], y[finite], linewidth=0.9, alpha=0.65)
                plotted = True
            axis.set_title(f"{bucket_name.title()} profiles")
            axis.set_ylabel("intensity")
            axis.set_yscale("log")
            axis.grid(True, alpha=0.25)
            if not plotted:
                axis.text(0.5, 0.5, "No profiles", ha="center", va="center")
        self.measurement_canvas.draw_idle()

    def _plot_selected_absorption(self):
        record = self._selected_absorption_record()
        if record is None or self.container_path is None:
            return
        absorption = self._load_absorption_image(record)
        poni_text = resolve_poni_text(
            self.container_path,
            record.i_path,
            alias=record.alias,
        )
        x, y, mode = integrate_profile(
            absorption,
            poni_text=poni_text,
            npt=100 if _detector_is_secondary(record.detector, record.alias) else 200,
        )
        self.absorption_fig.clear()
        ax_img, ax_profile = self.absorption_fig.subplots(1, 2)
        if absorption.ndim == 2:
            finite_abs = absorption[np.isfinite(absorption)]
            if finite_abs.size:
                vmin, vmax = np.nanpercentile(finite_abs, [2, 98])
            else:
                vmin, vmax = None, None
            ax_img.imshow(absorption, cmap="magma", vmin=vmin, vmax=vmax)
        else:
            ax_img.text(0.5, 0.5, f"shape={absorption.shape}", ha="center", va="center")
        ax_img.set_title(f"{record.point} {record.detector}")
        ax_img.set_axis_off()
        finite = np.isfinite(x) & np.isfinite(y)
        if np.count_nonzero(finite) >= 2:
            ax_profile.plot(x[finite], y[finite], linewidth=1.2)
        ax_profile.set_title(f"Absorption ({mode})")
        ax_profile.set_xlabel("q nm^-1" if mode == "pyFAI q" else "radius px")
        ax_profile.set_ylabel("-ln(I/I0)")
        ax_profile.grid(True, alpha=0.25)
        self.absorption_canvas.draw_idle()

    def _plot_all_absorption(self):
        if self.container_path is None:
            return
        self.absorption_fig.clear()
        ax_primary, ax_secondary = self.absorption_fig.subplots(1, 2)
        for bucket_name, axis in (("primary", ax_primary), ("secondary", ax_secondary)):
            plotted = False
            for record in self.absorption:
                is_secondary = _detector_is_secondary(record.detector, record.alias)
                if (bucket_name == "secondary") != is_secondary:
                    continue
                absorption = self._load_absorption_image(record)
                poni_text = resolve_poni_text(
                    self.container_path,
                    record.i_path,
                    alias=record.alias,
                )
                x, y, mode = integrate_profile(
                    absorption,
                    poni_text=poni_text,
                    npt=100 if is_secondary else 200,
                )
                finite = np.isfinite(x) & np.isfinite(y)
                if np.count_nonzero(finite) < 2:
                    continue
                axis.plot(x[finite], y[finite], linewidth=0.9, alpha=0.65, label=record.point)
                plotted = True
            axis.set_title(f"{bucket_name.title()} absorption")
            axis.set_xlabel("q nm^-1" if plotted else "")
            axis.set_ylabel("-ln(I/I0)")
            axis.grid(True, alpha=0.25)
            if not plotted:
                axis.text(0.5, 0.5, "No absorption", ha="center", va="center")
        self.absorption_canvas.draw_idle()

    def _plot_selected_image(self):
        record = self._selected_image_record()
        if record is None or self.container_path is None:
            return
        data = load_dataset(self.container_path, record.dataset_path)
        self.image_fig.clear()
        axis = self.image_fig.subplots(1, 1)
        arr = np.asarray(data)
        if arr.ndim == 3 and arr.shape[2] in (3, 4):
            if arr.dtype.kind in "ui" and arr.max(initial=0) > 255:
                arr = (255.0 * (arr.astype(float) / max(float(arr.max()), 1.0))).astype(np.uint8)
            axis.imshow(arr)
        elif arr.ndim == 2:
            axis.imshow(arr, cmap="gray")
        else:
            axis.text(0.5, 0.5, f"shape={arr.shape}", ha="center", va="center")
        axis.set_title(record.name)
        axis.set_axis_off()
        self.image_canvas.draw_idle()

    def _on_tree_selection(self):
        if self.container_path is None:
            return
        selected = self.tree.selectedItems()
        if not selected:
            return
        path = str(selected[0].data(0, USER_ROLE) or "/")
        self.tabs.setCurrentWidget(self.tabs.widget(5))
        self._show_hdf5_object(path)

    def _show_hdf5_object(self, object_path: str):
        if self.container_path is None:
            return
        self.hdf5_fig.clear()
        self.hdf5_title.setText(object_path)
        with h5py.File(self.container_path, "r") as h5f:
            obj = h5f if object_path == "/" else h5f[object_path]
            lines = [
                f"Path: {object_path}",
                f"Kind: {_object_kind(obj)}",
                f"Shape: {_format_shape(obj)}",
                f"dtype: {getattr(obj, 'dtype', '')}",
                "",
                "Attributes:",
                _attrs_to_text(obj.attrs) or "<none>",
            ]
            if isinstance(obj, h5py.Dataset):
                sample = _safe_dataset_sample(obj)
                arr = np.asarray(obj[()] if obj.shape == () else sample)
                stats = _numeric_stats(arr)
                if stats:
                    lines.extend(["", f"Stats/sample: {stats}"])
                lines.extend(["", "Sample:", _as_text(sample)])
                self._plot_hdf5_dataset_preview(object_path, obj)
            else:
                lines.extend(["", "Children:", "\n".join(sorted(obj.keys())) or "<none>"])
        self.hdf5_text.setPlainText("\n".join(lines))
        self.hdf5_canvas.draw_idle()

    def _plot_hdf5_dataset_preview(self, object_path: str, dataset: h5py.Dataset):
        axis = self.hdf5_fig.subplots(1, 1)
        try:
            arr = np.asarray(dataset[()])
        except Exception:
            axis.text(0.5, 0.5, "Cannot read dataset", ha="center", va="center")
            return
        if arr.ndim == 2 and np.issubdtype(arr.dtype, np.number):
            axis.imshow(np.log1p(np.maximum(arr.astype(float), 0)), cmap="viridis")
            axis.set_axis_off()
        elif arr.ndim == 3 and arr.shape[2] in (3, 4):
            axis.imshow(arr)
            axis.set_axis_off()
        elif arr.ndim == 1 and np.issubdtype(arr.dtype, np.number):
            axis.plot(arr)
            axis.grid(True, alpha=0.25)
        else:
            axis.text(0.5, 0.5, f"shape={arr.shape}", ha="center", va="center")
        axis.set_title(object_path.rsplit("/", 1)[-1] or object_path)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    container_path = Path(args[0]).expanduser() if args else DEFAULT_CONTAINER_PATH
    app = QApplication.instance() or QApplication(sys.argv[:1])
    viewer = SessionContainerViewer(container_path)
    viewer.show()
    return exec_app(app)


if __name__ == "__main__":
    raise SystemExit(main())
