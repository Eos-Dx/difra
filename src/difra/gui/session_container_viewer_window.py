"""Qt window for inspecting DIFRA session containers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import h5py
import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from difra.gui.qt_compat import QAction, QHeaderView, Qt, QtWidgets
from difra.gui.session_container_viewer_data import (
    AbsorptionRecord,
    AnalyticalRecord,
    ImageRecord,
    MeasurementRecord,
    _as_text,
    _attrs_to_text,
    _format_shape,
    _numeric_stats,
    _object_kind,
    _safe_dataset_sample,
    calculate_absorption_image,
    collect_absorption_records,
    collect_analytical,
    collect_images,
    collect_measurements,
    load_dataset,
    read_container_summary,
    resolve_poni_text,
)
from difra.gui.session_container_viewer_processing import (
    _detector_is_secondary,
    _detector_npt,
    integrate_profile,
)

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
        self.measurements_table.itemSelectionChanged.connect(
            self._plot_selected_measurement
        )
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
        self.absorption_table.itemSelectionChanged.connect(
            self._plot_selected_absorption
        )
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
        self.images_table.horizontalHeader().setSectionResizeMode(
            HEADER_RESIZE_TO_CONTENTS
        )
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
        start = str(
            self.container_path.parent
            if self.container_path
            else DEFAULT_CONTAINER_PATH.parent
        )
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
        x, y, mode = integrate_profile(
            data, poni_text=poni_text, npt=_detector_npt(record)
        )
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
                axis.plot(
                    x[finite], y[finite], linewidth=0.9, alpha=0.65, label=record.point
                )
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
                arr = (255.0 * (arr.astype(float) / max(float(arr.max()), 1.0))).astype(
                    np.uint8
                )
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
                lines.extend(
                    ["", "Children:", "\n".join(sorted(obj.keys())) or "<none>"]
                )
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
