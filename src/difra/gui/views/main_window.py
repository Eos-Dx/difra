import logging
import sys
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Get logger for this module
logger = logging.getLogger(__name__)

from difra.gui.main_window_ext.drawing_extension import DrawingMixin
from difra.gui.main_window_ext.rotation_extension import RotationMixin
from difra.gui.main_window_ext.session_mixin import SessionMixin
from difra.gui.main_window_ext.shape_table_extension import ShapeTableMixin
from difra.gui.main_window_ext.state_saver_extension import StateSaverMixin
from difra.gui.main_window_ext.technical_measurements import (
    TechnicalMeasurementsMixin,
)
from difra.gui.main_window_ext.zone_measurements import ZoneMeasurementsMixin
from difra.gui.main_window_ext.zone_points_extension import ZonePointsMixin
from difra.gui.views.image_view import ImageView
from difra.gui.views.main_window_basic import MainWindowBasic
from difra.hardware.hardware_control import HardwareController


class MainWindow(
    SessionMixin,
    RotationMixin,
    ShapeTableMixin,
    DrawingMixin,
    ZonePointsMixin,
    TechnicalMeasurementsMixin,
    ZoneMeasurementsMixin,
    StateSaverMixin,
    MainWindowBasic,
):

    def __init__(self, parent=None):
        logger.info("Initializing MainWindow")
        try:
            super().__init__(parent)
            logger.debug("MainWindow parent class initialized")
            
            self.state = {}
            
            # Enable animated dock widgets and make dividers more visible
            self.setDockOptions(
                QMainWindow.AnimatedDocks |
                QMainWindow.AllowTabbedDocks |
                QMainWindow.AllowNestedDocks
            )
            
            # Style the dock widget separators to be more visible and draggable
            self.setStyleSheet("""
                QMainWindow::separator {
                    background: #3daee9;
                    width: 6px;
                    height: 6px;
                }
                QMainWindow::separator:hover {
                    background: #2196F3;
                }
                QMainWindow::separator:horizontal {
                    width: 6px;
                }
                QMainWindow::separator:vertical {
                    height: 6px;
                }
            """)
            
            logger.debug("Setting up main layout...")
            self.setup_main_layout()
            logger.debug("Main layout created")
            
            self.measurement_widgets = {}
            
            logger.debug("Creating shape table...")
            self.create_shape_table()
            logger.debug("Shape table created")

            # 1. Create the Zone Measurements dock (right bottom)
            logger.debug("Creating technical panel...")
            self.create_technical_panel()  # this defines self.measDock
            logger.debug("Technical panel created")
            try:
                reconcile_technical = getattr(
                    self,
                    "reconcile_startup_technical_containers",
                    None,
                )
                if callable(reconcile_technical):
                    reconcile_technical()
            except Exception as e:
                logger.warning(
                    "Failed to reconcile startup technical containers: %s",
                    e,
                    exc_info=True,
                )

            # 2. Create the Zone Points dock (left bottom)
            logger.debug("Creating zone points widget...")
            self.create_zone_points_widget()  # this defines self.zonePointsDock
            logger.debug("Zone points widget created")

            # 3. Now it's safe to split them
            logger.debug("Splitting dock widgets...")
            self.splitDockWidget(
                self.zonePointsDock, self.zoneMeasurementsDock, Qt.Horizontal
            )
            try:
                self.resizeDocks(
                    [self.zonePointsDock, self.zoneMeasurementsDock],
                    [1100, 320],
                    Qt.Horizontal,
                )
            except Exception:
                pass
            logger.debug("Dock widgets split")

            try:
                logger.debug("Starting archive mirror sync timer...")
                self.setup_archive_mirror_sync()
                logger.debug("Archive mirror sync timer ready")
            except Exception as e:
                logger.warning(f"Failed to start archive mirror sync: {e}", exc_info=True)

            # 4. The rest as before...
            logger.debug("Creating drawing actions...")
            self.create_drawing_actions()
            self.add_drawing_actions_to_tool_bar()
            logger.debug("Drawing actions created")
            
            logger.debug("Creating delete action...")
            self.create_delete_action()
            self.add_delete_action_to_tool_bar()
            logger.debug("Delete action created")
            
            logger.debug("Creating rotation actions...")
            self.create_rotation_actions()
            self.add_rotation_actions_to_tool_bar()
            logger.debug("Rotation actions created")

            detector_tabs_enabled = False
            try:
                detector_tabs_enabled = bool(
                    hasattr(self, "_are_detector_param_tabs_enabled")
                    and self._are_detector_param_tabs_enabled()
                )
            except Exception:
                detector_tabs_enabled = False

            if detector_tabs_enabled:
                logger.debug("Loading default masks and ponis...")
                self.load_default_masks_and_ponis()
                logger.debug("Default masks and ponis loaded")
            else:
                self.masks = {}
                self.ponis = {}
                self.poni_files = {}
                logger.debug("Detector tabs disabled; skipping default masks/ponis preload")
            
            # Set a callback so that when shapes change, the shape table updates.
            self.image_view.shape_updated_callback = self.update_shape_table

            # If DEV mode, auto-open default image
            logger.debug("Checking dev mode...")
            self.check_dev_mode()

            # Enable periodic autosave of state
            try:
                logger.debug("Setting up auto-save (interval=5000ms)...")
                self.setup_auto_save(interval=5000)
                logger.info("Auto-save enabled")
            except Exception as e:
                logger.warning(f"Failed to start autosave: {e}", exc_info=True)
                print(f"Warning: failed to start autosave: {e}")
            
            logger.info("MainWindow initialization complete")
            
        except Exception as e:
            logger.error(f"Error during MainWindow initialization: {e}", exc_info=True)
            raise

    def setup_main_layout(self):
        try:
            logger.debug("Creating central widget...")
            central = QWidget()
            self.setCentralWidget(central)
            self.main_layout = QVBoxLayout(central)
            logger.debug("Central widget and layout created")

            # Create the image view and add it to central widget
            logger.debug("Creating ImageView...")
            self.image_view = ImageView(self)
            logger.debug("ImageView created")
            
            self.main_layout.addWidget(self.image_view)
            logger.debug("ImageView added to layout")

            logger.debug("Creating tabs widget...")
            self.tabs = QTabWidget()
            # self.main_layout.addWidget(self.tabs)
            
            logger.debug(f"Initializing HardwareController with config: {self.config}")
            self.hardware_controller = HardwareController(self.config)
            logger.debug("HardwareController initialized")
            
            # Initialize SessionManager
            logger.debug("Initializing SessionManager...")
            self.init_session_manager()
            logger.debug("SessionManager initialized")
            
        except Exception as e:
            logger.error(f"Error in setup_main_layout: {e}", exc_info=True)
            raise
