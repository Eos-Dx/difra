from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional

import h5py as h5py
import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure as Figure

from difra.gui.qt_compat import (
    QAction as QAction,
    QHeaderView as QHeaderView,
    Qt as Qt,
    QtWidgets as QtWidgets,
    exec_app as exec_app,
)
from difra.gui.session_container_viewer_data import (
    AbsorptionRecord as AbsorptionRecord,
    AnalyticalRecord as AnalyticalRecord,
    ImageRecord as ImageRecord,
    MeasurementRecord as MeasurementRecord,
    _as_text as _as_text,
    _attrs_to_text as _attrs_to_text,
    _detector_is_secondary as _detector_is_secondary,
    _format_shape as _format_shape,
    _iter_detector_datasets as _iter_detector_datasets,
    _numeric_stat_fields as _numeric_stat_fields,
    _numeric_stats as _numeric_stats,
    _object_kind as _object_kind,
    _point_ids_from_attrs as _point_ids_from_attrs,
    _safe_dataset_sample as _safe_dataset_sample,
    calculate_absorption_image as calculate_absorption_image,
    collect_absorption_records as collect_absorption_records,
    collect_analytical as collect_analytical,
    collect_images as collect_images,
    collect_measurements as collect_measurements,
    load_dataset as load_dataset,
    read_container_summary as read_container_summary,
    resolve_poni_text as resolve_poni_text,
)
from difra.gui.session_container_viewer_processing import (
    _detector_npt as _detector_npt,
    integrate_profile as integrate_profile,
)
from difra.gui.session_container_viewer_window import (
    DEFAULT_CONTAINER_PATH as DEFAULT_CONTAINER_PATH,
    HEADER_RESIZE_TO_CONTENTS as HEADER_RESIZE_TO_CONTENTS,
    HORIZONTAL as HORIZONTAL,
    TEXT_SELECTABLE_BY_MOUSE as TEXT_SELECTABLE_BY_MOUSE,
    USER_ROLE as USER_ROLE,
    SessionContainerViewer as SessionContainerViewer,
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

__all__ = [
    "AbsorptionRecord",
    "AnalyticalRecord",
    "DEFAULT_CONTAINER_PATH",
    "Figure",
    "FigureCanvas",
    "HEADER_RESIZE_TO_CONTENTS",
    "HORIZONTAL",
    "ImageRecord",
    "MeasurementRecord",
    "QAction",
    "QApplication",
    "QFileDialog",
    "QHeaderView",
    "QLabel",
    "QMainWindow",
    "QMessageBox",
    "QPlainTextEdit",
    "QPushButton",
    "QSplitter",
    "QTableWidget",
    "QTableWidgetItem",
    "QTabWidget",
    "QToolBar",
    "QTreeWidget",
    "QTreeWidgetItem",
    "QVBoxLayout",
    "QWidget",
    "Qt",
    "QtWidgets",
    "SessionContainerViewer",
    "TEXT_SELECTABLE_BY_MOUSE",
    "USER_ROLE",
    "_as_text",
    "_attrs_to_text",
    "_detector_is_secondary",
    "_detector_npt",
    "_format_shape",
    "_iter_detector_datasets",
    "_numeric_stat_fields",
    "_numeric_stats",
    "_object_kind",
    "_point_ids_from_attrs",
    "_safe_dataset_sample",
    "calculate_absorption_image",
    "collect_absorption_records",
    "collect_analytical",
    "collect_images",
    "collect_measurements",
    "exec_app",
    "h5py",
    "integrate_profile",
    "load_dataset",
    "main",
    "np",
    "read_container_summary",
    "resolve_poni_text",
]


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    container_path = Path(args[0]).expanduser() if args else DEFAULT_CONTAINER_PATH
    app = QApplication.instance() or QApplication(sys.argv[:1])
    viewer = SessionContainerViewer(container_path)
    viewer.show()
    return exec_app(app)


if __name__ == "__main__":
    raise SystemExit(main())
