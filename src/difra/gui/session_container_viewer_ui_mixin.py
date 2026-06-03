"""UI construction helpers for the DIFRA session-container viewer."""

from __future__ import annotations

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from difra.gui.qt_compat import QAction, QHeaderView, Qt, QtWidgets

HEADER_RESIZE_TO_CONTENTS = QHeaderView.ResizeToContents
HORIZONTAL = Qt.Horizontal
TEXT_SELECTABLE_BY_MOUSE = Qt.TextSelectableByMouse

QLabel = QtWidgets.QLabel
QPlainTextEdit = QtWidgets.QPlainTextEdit
QPushButton = QtWidgets.QPushButton
QSplitter = QtWidgets.QSplitter
QTableWidget = QtWidgets.QTableWidget
QTabWidget = QtWidgets.QTabWidget
QToolBar = QtWidgets.QToolBar
QTreeWidget = QtWidgets.QTreeWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QWidget = QtWidgets.QWidget


class SessionContainerViewerUiMixin:
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
