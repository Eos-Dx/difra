import logging
from pathlib import Path

from difra.gui.main_window_ext.technical.helpers import _get_default_folder

logger = logging.getLogger(__name__)


def _tm():
    from difra.gui.main_window_ext import technical_measurements as tm

    return tm


class TechnicalPanelMixin:
    def create_technical_panel(self):
        tm = _tm()
        self.aux_counter = 0
        super().create_zone_measurements()

        self.continuous_movement_controller = None
        self._initialize_continuous_movement_controller()

        title = "Technical Measurements"
        self.measDock = tm.QDockWidget(title, self)
        self.measDock.setObjectName("TechnicalMeasurementsDock")
        self.measDock.setAllowedAreas(tm.Qt.LeftDockWidgetArea | tm.Qt.RightDockWidgetArea)

        container = tm.QWidget()
        outer = tm.QVBoxLayout(container)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(6)

        try:
            from PyQt5.QtGui import QFont

            control_font = QFont()
            control_font.setPointSize(9)
            container.setFont(control_font)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in panel_mixin.py",
                exc_info=True,
            )

        it_layout = tm.QHBoxLayout()
        it_layout.setSpacing(8)
        it_layout.addWidget(tm.QLabel("Integration Time (s):"))
        self.integrationTimeSpin = tm.QDoubleSpinBox()
        self.integrationTimeSpin.setDecimals(6)
        self.integrationTimeSpin.setRange(1e-6, 1e4)
        self.integrationTimeSpin.setSingleStep(1e-6)
        self.integrationTimeSpin.setValue(1.0)
        self.integrationTimeSpin.setMinimumWidth(120)
        self.integrationTimeSpin.setMaximumWidth(160)
        it_layout.addWidget(self.integrationTimeSpin)

        it_layout.addWidget(tm.QLabel("Frames:"))
        self.captureFramesSpin = tm.QSpinBox()
        self.captureFramesSpin.setRange(1, 1_000_000)
        self.captureFramesSpin.setValue(1)
        self.captureFramesSpin.setMinimumWidth(90)
        self.captureFramesSpin.setMaximumWidth(120)
        self.captureFramesSpin.setToolTip(
            "Capture N frames at the given integration time; frames will be averaged into a single final image"
        )
        it_layout.addWidget(self.captureFramesSpin)
        it_layout.addStretch(1)

        outer.addLayout(it_layout)

        cm_layout = tm.QHBoxLayout()
        cm_layout.setSpacing(8)
        self.moveContinuousCheck = tm.QCheckBox("Move Continuous (AgBH)")
        self.moveContinuousCheck.setToolTip(
            "Enable continuous circular movement during AgBH measurements to smooth out sample inconsistencies"
        )
        cm_layout.addWidget(self.moveContinuousCheck)

        cm_layout.addWidget(tm.QLabel("Radius (mm):"))
        self.movementRadiusSpin = tm.QDoubleSpinBox()
        self.movementRadiusSpin.setRange(0.1, 10.0)
        self.movementRadiusSpin.setSingleStep(0.1)
        self.movementRadiusSpin.setValue(2.0)
        self.movementRadiusSpin.setDecimals(1)
        self.movementRadiusSpin.setMinimumWidth(90)
        self.movementRadiusSpin.setMaximumWidth(120)
        self.movementRadiusSpin.setToolTip(
            "Maximum radius for continuous movement pattern (decreases during measurement)"
        )
        cm_layout.addWidget(self.movementRadiusSpin)
        cm_layout.addStretch(1)

        outer.addLayout(cm_layout)

        fld = tm.QHBoxLayout()
        fld.setContentsMargins(0, 0, 0, 0)
        fld.setSpacing(8)
        self.saveFolderLabel = tm.QLabel("Save Folder:")
        fld.addWidget(self.saveFolderLabel)
        self.folderLE = tm.QLineEdit()
        default_folder = _get_default_folder(self.config if hasattr(self, "config") else None)
        self.folderLE.setText(default_folder)
        self.folderLE.setFixedWidth(300)
        self.folderLE.editingFinished.connect(self._on_technical_folder_changed)

        fld.addWidget(self.folderLE)
        b = tm.QPushButton("Browse…")
        b.setFixedWidth(150)
        b.clicked.connect(self._browse_folder)
        self.folderBrowseBtn = b
        fld.addWidget(b)
        fld.addStretch(1)
        outer.addLayout(fld)

        row = tm.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.auxBtn = tm.QPushButton("Measure")
        self.auxBtn.clicked.connect(self.measure_aux)
        row.addWidget(self.auxBtn)

        self._aux_status = tm.QLabel("")
        row.addWidget(self._aux_status)
        self._aux_timer = tm.QTimer(self)
        self._aux_timer.setInterval(200)
        self._aux_timer.timeout.connect(self._update_aux_status)

        self.auxNameLE = tm.QLineEdit()
        self.auxNameLE.setPlaceholderText("Measurement name (for metadata generation)")
        self.auxNameLE.setToolTip(
            "Enter name for auxiliary measurement - used for metadata file generation"
        )
        self.auxNameLE.setFixedWidth(300)
        row.addWidget(self.auxNameLE)
        row.addStretch(1)
        outer.addLayout(row)

        self.auxTable = tm.QTableWidget()
        self.auxTable.setColumnCount(4)
        self.auxTable.installEventFilter(self)
        self.auxTable.setHorizontalHeaderLabels(["Primary", "File", "Type", "Alias"])

        self.auxTable.verticalHeader().setVisible(False)
        self.auxTable.setAlternatingRowColors(True)
        try:
            from PyQt5.QtGui import QFont
            from PyQt5.QtWidgets import QHeaderView

            header = self.auxTable.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.Fixed)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            header.setSectionResizeMode(2, QHeaderView.Fixed)
            header.setSectionResizeMode(3, QHeaderView.Fixed)
            header.setStretchLastSection(False)

            self.auxTable.setColumnWidth(0, 60)
            self.auxTable.setColumnWidth(1, 220)
            self.auxTable.setColumnWidth(2, 60)
            self.auxTable.setColumnWidth(3, 60)

            font = QFont()
            font.setPointSize(9)
            self.auxTable.setFont(font)

            self.auxTable.verticalHeader().setDefaultSectionSize(24)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in panel_mixin.py",
                exc_info=True,
            )
        self.auxTable.setSelectionBehavior(self.auxTable.SelectRows)
        self.auxTable.setSelectionMode(self.auxTable.ExtendedSelection)
        self.auxTable.cellClicked.connect(self._handle_aux_table_cell_clicked)
        self.auxTable.cellDoubleClicked.connect(
            self._handle_aux_table_cell_double_clicked
        )
        outer.addWidget(self.auxTable)

        actions_layout = tm.QVBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)

        actions_top_row = tm.QHBoxLayout()
        actions_top_row.setContentsMargins(0, 0, 0, 0)
        actions_top_row.setSpacing(4)

        actions_bottom_row = tm.QHBoxLayout()
        actions_bottom_row.setContentsMargins(0, 0, 0, 0)
        actions_bottom_row.setSpacing(4)

        self.new_h5_btn = tm.QPushButton("Create Container...")
        self.new_h5_btn.setToolTip(
            "Primary path: create a fresh technical container for the current workflow"
        )
        self.new_h5_btn.clicked.connect(self.on_new_technical_container)
        actions_top_row.addWidget(self.new_h5_btn)

        self.load_h5_btn = tm.QPushButton("Load Container…")
        self.load_h5_btn.setToolTip(
            "Primary path: load an existing technical HDF5 container and continue from it"
        )
        self.load_h5_btn.clicked.connect(self.load_technical_h5)
        actions_top_row.addWidget(self.load_h5_btn)

        self.load_files_btn = tm.QPushButton("Load Files (Recovery)…")
        self.load_files_btn.setToolTip(
            "Recovery path: rebuild active technical container state from raw technical files"
        )
        self.load_files_btn.clicked.connect(self.load_technical_files)
        actions_top_row.addWidget(self.load_files_btn)

        self.dist_btn = tm.QPushButton("Distances...")
        self.dist_btn.setToolTip("Configure detector distances for technical measurements")
        self.dist_btn.clicked.connect(self.configure_detector_distances)
        actions_top_row.addWidget(self.dist_btn)

        self.update_poni_btn = tm.QPushButton("Update PONI...")
        self.update_poni_btn.setToolTip("Update PONI files for active technical container")
        self.update_poni_btn.clicked.connect(self.update_active_technical_container_poni)
        actions_bottom_row.addWidget(self.update_poni_btn)

        self.lock_h5_btn = tm.QPushButton("Lock Container")
        self.lock_h5_btn.setToolTip("Lock active technical container for production use")
        self.lock_h5_btn.clicked.connect(self.lock_active_technical_container)
        actions_bottom_row.addWidget(self.lock_h5_btn)

        self.archive_h5_btn = tm.QPushButton("Archive Container")
        self.archive_h5_btn.setToolTip(
            "Archive active container (irreversible): lock if needed and move container + related files to archive"
        )
        self.archive_h5_btn.clicked.connect(self.archive_active_technical_container)
        actions_bottom_row.addWidget(self.archive_h5_btn)

        self.pyfai_btn = tm.QPushButton("PyFAI")
        self.pyfai_btn.setToolTip("Run pyfai-calib2 in this folder")
        self.pyfai_btn.clicked.connect(self.run_pyfai)
        actions_bottom_row.addWidget(self.pyfai_btn)

        actions_top_row.addStretch(1)
        actions_bottom_row.addStretch(1)
        actions_layout.addLayout(actions_top_row)
        actions_layout.addLayout(actions_bottom_row)

        outer.addLayout(actions_layout)

        rt_layout = tm.QHBoxLayout()
        rt_layout.setSpacing(8)
        rt_layout.addWidget(tm.QLabel("Frames/⟳:"))
        self.framesSpin = tm.QSpinBox()
        self.framesSpin.setRange(1, 1_000_000)
        self.framesSpin.setValue(1)
        self.framesSpin.setMinimumWidth(90)
        self.framesSpin.setMaximumWidth(120)
        rt_layout.addWidget(self.framesSpin)

        self.rtBtn = tm.QPushButton("Real-time")
        self.rtBtn.setCheckable(True)
        self.rtBtn.setMaximumWidth(200)
        self.rtBtn.clicked.connect(self._toggle_realtime)
        rt_layout.addWidget(self.rtBtn)
        rt_layout.addStretch(1)

        outer.addLayout(rt_layout)

        scroll = tm.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        self.measDock.setWidget(scroll)

        try:
            self.measDock.setMinimumWidth(300)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in panel_mixin.py",
                exc_info=True,
            )

        self.addDockWidget(tm.Qt.LeftDockWidgetArea, self.measDock)

        tm.QTimer.singleShot(0, self._sync_compact_measurement_control_widths)
        self.enable_measurement_controls(False)
        self.hardware_state_changed.connect(self.enable_measurement_controls)
        self.hardware_state_changed.connect(lambda _: self.refresh_aux_table_alias_models())
        self.hardware_state_changed.connect(lambda _: self._initialize_continuous_movement_controller())

    def _sync_compact_measurement_control_widths(self):
        """Align compact input rows with the right edge of the Distances button."""
        try:
            action_spacing = 4
            row_spacing = 8
            target_right = (
                self.new_h5_btn.sizeHint().width()
                + self.load_h5_btn.sizeHint().width()
                + self.load_files_btn.sizeHint().width()
                + self.dist_btn.sizeHint().width()
                + action_spacing * 3
            )

            browse_width = self.dist_btn.sizeHint().width()
            self.folderBrowseBtn.setFixedWidth(browse_width)

            folder_width = max(
                180,
                target_right
                - self.saveFolderLabel.sizeHint().width()
                - browse_width
                - row_spacing * 2,
            )
            self.folderLE.setFixedWidth(folder_width)

            name_width = max(
                180,
                target_right
                - self.auxBtn.sizeHint().width()
                - self._aux_status.minimumSizeHint().width()
                - row_spacing * 2,
            )
            self.auxNameLE.setFixedWidth(name_width)

            # Start collapsed to the content width up to "Distances...".
            dock_width = target_right + 20
            self.resizeDocks([self.measDock], [dock_width], _tm().Qt.Horizontal)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "Suppressed exception in panel_mixin.py",
                exc_info=True,
            )

    def enable_measurement_controls(self, enable: bool):
        status = "enabled" if enable else "disabled"
        self._log_technical_event(f"Technical measurement controls {status}")
        widgets = [
            self.integrationTimeSpin,
            self.captureFramesSpin,
            self.moveContinuousCheck,
            self.movementRadiusSpin,
            self.folderLE,
            self.auxNameLE,
            self.auxTable,
            self.framesSpin,
            self.rtBtn,
        ]
        if hasattr(self, "load_h5_btn"):
            self.load_h5_btn.setEnabled(True)
        if hasattr(self, "load_files_btn"):
            self.load_files_btn.setEnabled(True)
        if hasattr(self, "new_h5_btn"):
            self.new_h5_btn.setEnabled(True)
        if hasattr(self, "lock_h5_btn"):
            self.lock_h5_btn.setEnabled(True)
        if hasattr(self, "archive_h5_btn"):
            self.archive_h5_btn.setEnabled(True)
        if hasattr(self, "update_poni_btn"):
            self.update_poni_btn.setEnabled(True)

        for w in widgets:
            w.setEnabled(enable)

        self._update_distance_dependent_controls()

    def _update_distance_dependent_controls(self):
        has_distances = hasattr(self, "_detector_distances") and bool(self._detector_distances)

        if hasattr(self, "auxBtn"):
            self.auxBtn.setEnabled(has_distances)
        if hasattr(self, "pyfai_btn"):
            self.pyfai_btn.setEnabled(has_distances)
        if hasattr(self, "update_poni_btn"):
            self.update_poni_btn.setEnabled(has_distances)
        if hasattr(self, "lock_h5_btn"):
            self.lock_h5_btn.setEnabled(has_distances)

        if not has_distances and hasattr(self, "auxBtn"):
            self.auxBtn.setStyleSheet("color: gray;")
        elif hasattr(self, "auxBtn"):
            self.auxBtn.setStyleSheet("")

    def _update_window_title_with_distances(self):
        if not hasattr(self, "_detector_distances") or not self._detector_distances:
            return

        detector_configs = self.config.get("detectors", [])
        distance_parts = []
        for detector_id, distance_cm in sorted(self._detector_distances.items()):
            detector_config = next((d for d in detector_configs if d.get("id") == detector_id), None)
            alias = detector_config.get("alias", detector_id) if detector_config else detector_id
            distance_parts.append(f"{alias}: {distance_cm} cm")

        distance_str = ", ".join(distance_parts)
        base_title = "EosDX Scanning Software"
        is_dev = self.config.get("DEV", False)
        dev_suffix = " [DEMO]" if is_dev else ""

        new_title = f"{base_title} ({distance_str}){dev_suffix}"
        self.setWindowTitle(new_title)
        self._log_technical_event(f"Window title updated with distances: {distance_str}")

    def configure_detector_distances(self):
        tm = _tm()
        from difra.gui.detector_distance_config_dialog import DetectorDistanceConfigDialog

        detector_configs = self.config.get("detectors", [])
        if not detector_configs:
            tm.QMessageBox.warning(
                self,
                "No Detectors",
                "No detector configuration found.",
            )
            return

        active_detector_ids = self._get_active_detector_ids()
        active_detector_configs = [d for d in detector_configs if d.get("id") in active_detector_ids]
        if not active_detector_configs:
            tm.QMessageBox.warning(
                self,
                "No Active Detectors",
                "No active detectors configured.",
            )
            return

        current_distances = getattr(self, "_detector_distances", {})
        dialog = DetectorDistanceConfigDialog(
            detector_configs=active_detector_configs,
            current_distances=current_distances,
            parent=self,
        )

        if dialog.exec_() == tm.QDialog.Accepted:
            distances = dialog.get_distances()
            self._detector_distances = distances

            dist_str = ", ".join(
                f"{d.get('alias', d['id'])}: {distances.get(d['id'], 'N/A')} cm"
                for d in active_detector_configs
            )
            self._log_technical_event(f"Configured distances: {dist_str}")
            self._update_window_title_with_distances()
            self._update_distance_dependent_controls()
            if hasattr(self, "_on_detector_distances_updated"):
                try:
                    self._on_detector_distances_updated()
                except Exception as exc:
                    logger.warning("Distance update hook failed: %s", exc, exc_info=True)

            tm.QMessageBox.information(
                self,
                "Distances Configured",
                f"Detector distances set:\n\n{dist_str}",
            )

    def _initialize_continuous_movement_controller(self):
        try:
            from difra.gui.technical.continuous_movement import ContinuousMovementController

            stage_controller = None
            if hasattr(self, "hardware_controller") and self.hardware_controller:
                stage_controller = self.hardware_controller.stage_controller
            elif getattr(self, "stage_controller", None) is not None:
                stage_controller = self.stage_controller
            elif hasattr(self, "hardware_client") and self.hardware_client:
                stage_controller = self.hardware_client.stage_controller

            if stage_controller:
                self.continuous_movement_controller = ContinuousMovementController(
                    stage_controller=stage_controller, parent=self
                )
                self.continuous_movement_controller.movement_error.connect(
                    lambda msg: self._log_technical_event(f"Movement error: {msg}")
                )
                self._log_technical_event("Continuous movement controller initialized")
                logger.info("Continuous movement controller initialized")
            else:
                self._log_technical_event("No stage controller available for continuous movement")
                logger.debug("No stage controller available for continuous movement")
        except ImportError as e:
            logger.warning(f"Failed to import continuous movement controller: {e}")
        except Exception as e:
            logger.error(f"Error initializing continuous movement controller: {e}", exc_info=True)

    def _browse_folder(self):
        tm = _tm()
        if self._is_technical_output_folder_locked():
            locked_folder = self._current_technical_output_folder()
            tm.QMessageBox.information(
                self,
                "Technical Folder Locked",
                "Technical output folder is locked to the active technical container.\n\n"
                f"Folder: {locked_folder}",
            )
            self._refresh_technical_output_folder_lock()
            return
        f = tm.QFileDialog.getExistingDirectory(self, "Select Folder")
        if f:
            self.folderLE.setText(f)
            self._on_technical_folder_changed()

    def _current_technical_output_folder(self) -> str:
        active_path = None
        if hasattr(self, "_active_technical_container_path_obj"):
            try:
                active_path = self._active_technical_container_path_obj()
            except Exception:
                active_path = None
        if active_path is not None and active_path.exists():
            return str(active_path.parent)

        locked = str(getattr(self, "_technical_output_folder_locked_path", "") or "").strip()
        if locked:
            return locked
        return str((self.folderLE.text() or "").strip())

    def _is_technical_output_folder_locked(self) -> bool:
        active_path = None
        if hasattr(self, "_active_technical_container_path_obj"):
            try:
                active_path = self._active_technical_container_path_obj()
            except Exception:
                active_path = None
        return bool(active_path is not None and active_path.exists())

    def _refresh_technical_output_folder_lock(self):
        locked_folder = ""
        active_path = None
        if hasattr(self, "_active_technical_container_path_obj"):
            try:
                active_path = self._active_technical_container_path_obj()
            except Exception:
                active_path = None

        if active_path is not None and active_path.exists():
            locked_folder = str(active_path.parent)

        self._technical_output_folder_locked_path = locked_folder

        if hasattr(self, "folderLE") and self.folderLE is not None:
            if locked_folder:
                self.folderLE.setText(locked_folder)
            try:
                self.folderLE.setReadOnly(bool(locked_folder))
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "Suppressed exception in panel_mixin.py",
                    exc_info=True,
                )
            try:
                self.folderLE.setToolTip(
                    "Locked to the active technical container folder."
                    if locked_folder
                    else "Technical output folder for technical files and container work."
                )
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "Suppressed exception in panel_mixin.py",
                    exc_info=True,
                )

        if hasattr(self, "folderBrowseBtn") and self.folderBrowseBtn is not None:
            self.folderBrowseBtn.setEnabled(not bool(locked_folder))
            try:
                self.folderBrowseBtn.setToolTip(
                    "Cannot change folder while an active technical container exists."
                    if locked_folder
                    else "Browse for technical output folder."
                )
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "Suppressed exception in panel_mixin.py",
                    exc_info=True,
                )

    def _on_technical_folder_changed(self):
        if not hasattr(self, "folderLE") or self.folderLE is None:
            return

        if self._is_technical_output_folder_locked():
            self._refresh_technical_output_folder_lock()
            return

        folder = str((self.folderLE.text() or "").strip())
        if not folder:
            return

        try:
            folder_path = Path(folder)
            folder_path.mkdir(parents=True, exist_ok=True)
            self.folderLE.setText(str(folder_path))
            self._technical_output_folder_locked_path = ""
        except Exception as exc:
            logger.warning("Failed to update technical folder %s: %s", folder, exc)

    def initialize_hardware(self):
        pass
