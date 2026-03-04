"""Session Management Mixin for DIFRA Main Window.

Integrates SessionManager for HDF5 container-based data storage.
"""

import json
import logging
from pathlib import Path

from PyQt5.QtWidgets import (
    QAction,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from difra.gui.container_api import (
    get_container_manager,
    get_schema,
    get_writer,
)
from difra.gui.session_manager import SessionManager
from difra.gui.operator_manager import OperatorManager, OperatorSelectionDialog

logger = logging.getLogger(__name__)


from difra.gui.main_window_ext.session_workspace_mixin import SessionWorkspaceMixin
from difra.gui.main_window_ext.session_flow_mixin import SessionFlowMixin


class SessionMixin(SessionWorkspaceMixin, SessionFlowMixin):
    """Mixin for session management functionality."""

    @staticmethod
    def _decode_attr(value):
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def _append_compact_log(self, category: str, message: str):
        try:
            if hasattr(self, "_append_measurement_log"):
                self._append_measurement_log(f"[{category}] {message}")
        except Exception:
            pass

    def _append_session_log(self, message: str):
        self._append_compact_log("SESSION", message)

    def _append_technical_log(self, message: str):
        self._append_compact_log("TECH", message)

    def _default_session_distance_cm(self):
        try:
            distances = getattr(self, "_detector_distances", {}) or {}
            if distances:
                return float(next(iter(distances.values())))
        except Exception:
            pass
        return None

    def _current_measurement_output_folder(self) -> Path:
        if (
            hasattr(self, "session_manager")
            and self.session_manager is not None
            and self.session_manager.is_session_active()
        ):
            session_path = getattr(self.session_manager, "session_path", None)
            if session_path:
                try:
                    session_parent = Path(session_path).parent
                    if Path(session_path).exists():
                        return session_parent
                except Exception:
                    pass

        if hasattr(self, "folderLineEdit") and self.folderLineEdit is not None:
            folder_text = str(self.folderLineEdit.text() or "").strip()
            if folder_text:
                return Path(folder_text)

        return self.get_session_folder()

    def _is_measurement_output_folder_locked(self) -> bool:
        if not hasattr(self, "session_manager") or self.session_manager is None:
            return False
        if not self.session_manager.is_session_active():
            return False
        session_path = getattr(self.session_manager, "session_path", None)
        if not session_path:
            return False
        try:
            return Path(session_path).exists()
        except Exception:
            return False

    def _refresh_measurement_output_folder_lock(self):
        locked_folder = ""
        if self._is_measurement_output_folder_locked():
            try:
                locked_folder = str(Path(self.session_manager.session_path).parent)
            except Exception:
                locked_folder = ""

        self._measurement_output_folder_locked_path = locked_folder

        if hasattr(self, "folderLineEdit") and self.folderLineEdit is not None:
            if locked_folder:
                self.folderLineEdit.setText(locked_folder)
            try:
                self.folderLineEdit.setReadOnly(bool(locked_folder))
            except Exception:
                pass
            try:
                self.folderLineEdit.setToolTip(
                    "Locked to the active session container folder."
                    if locked_folder
                    else "Measurement output folder for the current session workflow."
                )
            except Exception:
                pass

        if hasattr(self, "browseBtn") and self.browseBtn is not None:
            self.browseBtn.setEnabled(not bool(locked_folder))
            try:
                self.browseBtn.setToolTip(
                    "Cannot change folder while an active session container exists."
                    if locked_folder
                    else "Browse for measurement output folder."
                )
            except Exception:
                pass

    def _enforce_measurement_output_folder_lock(self, show_message: bool = False) -> bool:
        if not self._is_measurement_output_folder_locked():
            return True

        locked_folder = str(getattr(self, "_measurement_output_folder_locked_path", "") or "").strip()
        if not locked_folder:
            self._refresh_measurement_output_folder_lock()
            locked_folder = str(getattr(self, "_measurement_output_folder_locked_path", "") or "").strip()

        if hasattr(self, "folderLineEdit") and self.folderLineEdit is not None:
            current_folder = str(self.folderLineEdit.text() or "").strip()
            if current_folder != locked_folder:
                self.folderLineEdit.setText(locked_folder)
                if show_message:
                    QMessageBox.information(
                        self,
                        "Measurement Folder Locked",
                        "Measurement output folder is locked to the active session container.\n\n"
                        f"Folder: {locked_folder}",
                    )
        return True
    
    def init_session_manager(self):
        """Initialize SessionManager and add UI actions."""
        logger.info("Initializing SessionManager")
        self._append_session_log("Initializing session manager")
        
        # Initialize operator manager first
        self.operator_manager = OperatorManager()
        
        # Show operator selection dialog on startup
        self.show_operator_selection_dialog()
        
        # Create SessionManager instance with config (including operator)
        config = self.config if hasattr(self, 'config') else {}
        
        # Add current operator to config
        if self.operator_manager.get_current_operator_id():
            config['operator_id'] = self.operator_manager.get_current_operator_id()
        
        self.session_manager = SessionManager(config=config)
        
        # Add session menu actions
        self.add_session_menu_actions()
        
        logger.info("SessionManager initialized")
        self._append_session_log("Session manager ready")
    
    def add_session_menu_actions(self):
        """Add session-related actions to File menu."""
        # Get or create File menu
        menu_bar = self.menuBar()
        file_menu = None
        
        for action in menu_bar.actions():
            if action.text() == "File":
                file_menu = action.menu()
                break
        
        if not file_menu:
            file_menu = menu_bar.addMenu("File")
        
        # Add separator
        file_menu.addSeparator()
        
        # New Technical Container action
        new_technical_action = QAction("Create New Technical Container...", self)
        new_technical_action.triggered.connect(self.on_new_technical_container)
        new_technical_action.setStatusTip("Create or reuse the technical container for the current distances")
        file_menu.addAction(new_technical_action)

        # New Session action
        new_session_action = QAction("Create New Session Container...", self)
        new_session_action.triggered.connect(self.on_new_session)
        new_session_action.setStatusTip("Create a new measurement session container")
        file_menu.addAction(new_session_action)
        
        # Close Session action
        close_session_action = QAction("Close Session", self)
        close_session_action.triggered.connect(self.on_close_session)
        close_session_action.setStatusTip("Close the current session")
        file_menu.addAction(close_session_action)
        
        # Session Info action
        session_info_action = QAction("Session Info", self)
        session_info_action.triggered.connect(self.on_session_info)
        session_info_action.setStatusTip("Show current session information")
        file_menu.addAction(session_info_action)
        
        # Add separator
        file_menu.addSeparator()
        
        # Finalize & Send Session action
        finalize_session_action = QAction("Finalize && Send Session", self)
        finalize_session_action.triggered.connect(self.on_finalize_session)
        finalize_session_action.setStatusTip("Finalize session and prepare for upload")
        file_menu.addAction(finalize_session_action)
        
        # Add separator
        file_menu.addSeparator()
        
        # Restore/Open Session action
        restore_session_action = QAction("Open Existing Session...", self)
        restore_session_action.triggered.connect(self.on_restore_session)
        restore_session_action.setStatusTip("Open an existing session container for analysis")
        file_menu.addAction(restore_session_action)
        
        logger.debug("Session menu actions added")
    
    def show_operator_selection_dialog(self):
        """Show operator selection dialog on startup."""
        dialog = OperatorSelectionDialog(self.operator_manager, self)
        
        if dialog.exec_() == QDialog.Accepted:
            operator_id = dialog.get_selected_operator_id()
            logger.info(f"Operator selected: {operator_id}")
        else:
            # User cancelled - use default or show warning
            logger.warning("Operator selection cancelled")
            QMessageBox.warning(
                self,
                "No Operator Selected",
                "No operator selected. Using default operator.\n\n"
                "You can change this later from File → Operator Settings...",
            )
    
    def on_new_session(self):
        """Handle New Session action."""
        self._append_session_log("New session requested")
        # Check if session already active
        if self.session_manager.is_session_active():
            QMessageBox.warning(
                self,
                "Session Already Open",
                "A session container is already open.\n\n"
                f"Sample ID: {self.session_manager.sample_id}\n"
                f"Container: {Path(self.session_manager.session_path).name}\n\n"
                "Close/finalize and send the current session from the Session controls before creating a new one.",
            )
            self._append_session_log("New session blocked: active session is still open")
            return
        
        # Show dialog to get session parameters
        dialog = NewSessionDialog(
            self.operator_manager,
            self,
            default_distance=self._default_session_distance_cm(),
        )
        
        if dialog.exec_() == QDialog.Accepted:
            params = dialog.get_parameters()
            
            # Get session folder from config or file dialog
            session_folder = self.get_session_folder()
            if not session_folder:
                return
            
            try:
                # Create session with schema-driven parameters
                # All attributes come from params dict or SessionManager defaults
                session_id, session_path = self.session_manager.create_session(
                    folder=session_folder,
                    distance_cm=params['distance_cm'],
                    technical_container_path=getattr(
                        self, "_active_technical_container_path", None
                    ),
                    sample_id=params['sample_id'],
                    operator_id=params.get('operator_id'),
                    # Any other schema attributes can be passed from params
                    **{k: v for k, v in params.items() if k not in ['sample_id', 'operator_id', 'distance_cm']},
                )
                
                QMessageBox.information(
                    self,
                    "Session Created",
                    f"Session created successfully!\n\n"
                    f"Sample ID: {params['sample_id']}\n"
                    f"Study: {params.get('study_name', 'UNSPECIFIED')}\n"
                    f"Project: {params.get('project_id', params.get('study_name', 'UNSPECIFIED'))}\n"
                    f"Container: {session_path.name}",
                )
                
                logger.info(
                    f"Created new session: {session_id} for sample {params['sample_id']}"
                )
                self._append_session_log(
                    f"Created session {session_path.name} for sample {params['sample_id']}"
                )
                
                # Update UI
                self.update_session_status()
                
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Session Creation Failed",
                    f"Failed to create session:\n\n{str(e)}",
                )
                logger.error(f"Failed to create session: {e}", exc_info=True)
                self._append_session_log(f"Session creation failed: {type(e).__name__}")

    def on_new_technical_container(self):
        """Handle Create New Technical Container action."""
        self._append_technical_log("New technical container requested")

        if not hasattr(self, "_create_new_active_technical_container"):
            QMessageBox.warning(
                self,
                "Technical Container",
                "Technical container workflow is not available in this build.",
            )
            return

        distances = getattr(self, "_detector_distances", {}) or {}
        if not distances and hasattr(self, "configure_detector_distances"):
            self.configure_detector_distances()
            distances = getattr(self, "_detector_distances", {}) or {}
            if not distances:
                return

            active_after_config = str(
                getattr(self, "_active_technical_container_path", "") or ""
            ).strip()
            if active_after_config:
                QMessageBox.information(
                    self,
                    "Technical Container Ready",
                    f"Technical container ready:\n{Path(active_after_config).name}",
                )
            return

        try:
            technical_path = self._create_new_active_technical_container(clear_table=False)
            if technical_path is None:
                return

            QMessageBox.information(
                self,
                "Technical Container Ready",
                f"Technical container ready:\n{Path(technical_path).name}",
            )
        except Exception as exc:
            logger.error(
                "Failed to create technical container from session action: %s",
                exc,
                exc_info=True,
            )
            QMessageBox.critical(
                self,
                "Technical Container Failed",
                f"Failed to prepare technical container:\n\n{exc}",
            )
            self._append_technical_log(
                f"Technical container preparation failed: {type(exc).__name__}"
            )
    
    def on_close_session(self):
        """Handle Close Session action."""
        if not self.session_manager.is_session_active():
            QMessageBox.information(
                self,
                "No Active Session",
                "No session is currently active.",
            )
            return
        
        reply = QMessageBox.question(
            self,
            "Close Session?",
            f"Close session '{self.session_manager.sample_id}'?\n\n"
            f"The container has been saved.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            closed_sample = self.session_manager.sample_id
            self.session_manager.close_session()
            QMessageBox.information(
                self,
                "Session Closed",
                "Session closed successfully.",
            )
            logger.info("Session closed by user")
            self._append_session_log(f"Closed session for sample {closed_sample}")
            
            # Update UI
            self.update_session_status()
    
    def on_session_info(self):
        """Handle Session Info action."""
        info = self.session_manager.get_session_info()
        
        if not info['active']:
            QMessageBox.information(
                self,
                "No Active Session",
                "No session is currently active.\n\n"
                "Create a new session from File → New Session...",
            )
            return
        
        # Build info message
        msg = f"Sample ID: {info['sample_id']}\n"
        msg += f"Study: {info.get('study_name', 'UNSPECIFIED')}\n"
        msg += f"Project: {info.get('project_id', info.get('study_name', 'UNSPECIFIED'))}\n"
        msg += f"Session ID: {info['session_id']}\n"
        msg += f"Operator: {info['operator_id']}\n"
        msg += f"Machine: {info['machine_name']}\n"
        msg += f"Beam Energy: {info['beam_energy_kev']} keV\n\n"
        msg += f"Container: {Path(info['session_path']).name}\n\n"
        msg += f"Transfer Status: {str(info.get('transfer_status', 'unsent')).upper()}\n\n"
        msg += "Attenuation Status:\n"
        msg += f"  I₀ recorded: {'✓' if info['i0_recorded'] else '✗'}\n"
        msg += f"  I recorded: {'✓' if info['i_recorded'] else '✗'}\n"
        msg += f"  Complete: {'✓' if info['attenuation_complete'] else '✗'}\n"
        
        QMessageBox.information(
            self,
            "Session Information",
            msg,
        )
    
    def get_session_folder(self) -> Path:
        """Get session (measurements) folder from config.
        
        Reads measurements_folder from global.json config.
        The Zone Measurements panel may override this before a session starts.
        Once a session container exists, the measurements path is locked to that container's folder.
        
        Returns:
            Path to measurements folder from config
        """
        # Get measurements folder from config
        if hasattr(self, 'config') and self.config:
            # Try measurements_folder first (preferred)
            folder = self.config.get('measurements_folder')
            if folder:
                folder_path = Path(folder)
                folder_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Using measurements folder from config: {folder_path}")
                return folder_path
            
            # Fallback to session_folder for backward compatibility
            folder = self.config.get('session_folder')
            if folder:
                folder_path = Path(folder)
                folder_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Using session folder from config: {folder_path}")
                return folder_path
        
        # No config - use default under difra_base_folder
        if hasattr(self, 'config') and self.config:
            base = self.config.get('difra_base_folder')
            if base:
                folder_path = Path(base) / 'measurements'
                folder_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Using default measurements folder: {folder_path}")
                return folder_path
        
        # Last resort: use home directory
        folder_path = Path.home() / 'difra_measurements'
        folder_path.mkdir(parents=True, exist_ok=True)
        logger.warning(f"No config found, using fallback: {folder_path}")
        return folder_path
    
    def update_session_status(self):
        """Update UI to reflect current session status."""
        info = self.session_manager.get_session_info()
        self._refresh_measurement_output_folder_lock()
        
        # Update window title
        if info['active']:
            self.setWindowTitle(f"DIFRA - {info['sample_id']}")
        else:
            self.setWindowTitle("DIFRA")
        
        # Update status bar if present
        if hasattr(self, 'statusBar'):
            if info['active']:
                status_msg = f"Session: {info['sample_id']} [{str(info.get('transfer_status', 'unsent')).upper()}]"
                if info['attenuation_complete']:
                    status_msg += " | Attenuation: Complete"
                elif info['i0_recorded']:
                    status_msg += " | Attenuation: I₀ recorded"
                self.statusBar().showMessage(status_msg)
            else:
                self.statusBar().showMessage("No active session")
        
        # Update Zone Measurements panel Sample ID if present
        if hasattr(self, 'fileNameLineEdit'):
            if info['active']:
                self.fileNameLineEdit.setText(info['sample_id'])
                # Update lock indicator
                if hasattr(self, 'sampleIdLockLabel'):
                    is_locked = info.get('is_locked', False)
                    if is_locked:
                        self.sampleIdLockLabel.setText("🔒 Locked")
                        self.sampleIdLockLabel.setStyleSheet("color: #d32f2f; font-size: 9px;")
                    else:
                        self.sampleIdLockLabel.setText("")
            else:
                self.fileNameLineEdit.setText("")
                if hasattr(self, 'sampleIdLockLabel'):
                    self.sampleIdLockLabel.setText("")
        
        # Update Session tab if present
        if hasattr(self, '_update_session_tab_info'):
            self._update_session_tab_info()

    def on_technical_container_loaded(self, technical_path: str, is_locked: bool = False):
        """Optionally update embedded technical data in active unlocked session."""
        if not hasattr(self, "session_manager"):
            return
        if not self.session_manager.is_session_active():
            return

        if self.session_manager.is_locked():
            QMessageBox.information(
                self,
                "Session Locked",
                "Active session is locked. Technical data cannot be updated.",
            )
            self._append_technical_log("Technical update skipped: active session is locked")
            return

        reply = QMessageBox.question(
            self,
            "Update Session Technical Data?",
            f"Technical container loaded:\n{Path(technical_path).name}\n\n"
            "Do you want to replace embedded technical data in the active session?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            self._append_technical_log("Technical update cancelled by user")
            return

        try:
            self.session_manager.replace_technical_container(Path(technical_path))
            status = "locked" if is_locked else "unlocked"
            QMessageBox.information(
                self,
                "Technical Updated",
                f"Session technical data updated from {status} container:\n"
                f"{Path(technical_path).name}",
            )
            logger.info(
                "Updated session technical data from loaded container: "
                f"session={self.session_manager.session_path} technical={technical_path}"
            )
            self._append_technical_log(
                f"Session updated from technical container: {Path(technical_path).name} ({status})"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Technical Update Failed",
                f"Failed to update session technical data:\n\n{e}",
            )
            logger.error(
                f"Failed technical update from loaded container: {e}",
                exc_info=True,
            )
            self._append_technical_log(
                f"Technical update failed: {type(e).__name__}"
            )

class NewSessionDialog(QDialog):
    """Dialog for creating a new session.
    
    Prompts user for:
    - Sample ID (required)
    - Study (required)
    - Distance in cm (required)
    - Operator (with option to use current or select different)
    
    Beam energy is read from global config.
    """
    
    def __init__(self, operator_manager: OperatorManager, parent=None, default_distance: float = None):
        super().__init__(parent)
        
        self.operator_manager = operator_manager
        self.selected_operator_id = None
        
        self.setWindowTitle("New Session")
        self.setModal(True)
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout(self)
        
        # Form layout for parameters
        form_layout = QFormLayout()
        
        # Sample ID (required)
        self.sample_id_edit = QLineEdit()
        self.sample_id_edit.setPlaceholderText("e.g. SAMPLE_001")
        form_layout.addRow("Sample ID*:", self.sample_id_edit)

        # Study (required)
        self.study_name_edit = QLineEdit()
        self.study_name_edit.setPlaceholderText("e.g. STUDY_2026_A")
        form_layout.addRow("Study*:", self.study_name_edit)

        # Project (defaults to study if omitted)
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("e.g. PROJECT_2026_A")
        form_layout.addRow("Project:", self.project_id_edit)
        
        # Distance (required) - with explicit prompt
        distance_label = QLabel(
            "<b>Distance (cm)*:</b><br>"
            "<span style='color: #555; font-size: 10px;'>"
            "Sample-to-detector distance (must match technical container)"
            "</span>"
        )
        self.distance_edit = QLineEdit()
        if default_distance:
            self.distance_edit.setText(str(default_distance))
        else:
            self.distance_edit.setText("17.0")
        self.distance_edit.setPlaceholderText("e.g. 17.0, 25.0, 50.0")
        form_layout.addRow(distance_label, self.distance_edit)
        
        layout.addLayout(form_layout)
        
        # Operator selection group
        operator_group = QGroupBox("Operator Selection")
        operator_layout = QFormLayout(operator_group)
        
        self.operator_combo = QComboBox()
        self._populate_operator_combo()
        operator_layout.addRow("Operator*:", self.operator_combo)
        
        # Operator details display
        self.operator_details_label = QLabel()
        self.operator_details_label.setWordWrap(True)
        self.operator_details_label.setStyleSheet(
            "color: #555; background-color: #f0f0f0; padding: 5px; border-radius: 3px; font-size: 10px;"
        )
        operator_layout.addRow("Details:", self.operator_details_label)
        self.operator_combo.currentIndexChanged.connect(self._on_operator_changed)
        
        # Add new operator button
        new_operator_btn = QPushButton("Add New Operator...")
        new_operator_btn.clicked.connect(self._on_add_new_operator)
        operator_layout.addRow("", new_operator_btn)
        
        layout.addWidget(operator_group)
        
        # Info label
        info_label = QLabel(
            "* Required fields\n\n"
            "Beam energy: Read from global config\n"
            "<b>Note:</b> Distance must match technical container distance.\n"
            "If Project is left blank, Study will be used."
        )
        info_label.setStyleSheet("color: gray; font-style: italic;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        # Update operator details for initial selection
        self._update_operator_details()
    
    def _populate_operator_combo(self):
        """Populate operator combo box."""
        self.operator_combo.clear()
        
        operators = self.operator_manager.get_all_operators()
        
        if not operators:
            self.operator_combo.addItem("No operators defined", None)
            return
        
        # Add operators
        current_id = self.operator_manager.get_current_operator_id()
        current_index = 0
        
        for i, (op_id, op_info) in enumerate(sorted(operators.items())):
            display_name = self.operator_manager.get_operator_display_name(op_id)
            self.operator_combo.addItem(display_name, op_id)
            
            # Pre-select current operator
            if op_id == current_id:
                current_index = i
        
        if current_id and current_index < self.operator_combo.count():
            self.operator_combo.setCurrentIndex(current_index)
    
    def _update_operator_details(self):
        """Update operator details display."""
        if not hasattr(self, "operator_details_label"):
            return

        operator_id = self.operator_combo.currentData()
        
        if not operator_id:
            self.operator_details_label.setText("No operator selected")
            return
        
        operator = self.operator_manager.get_operator(operator_id)
        if not operator:
            self.operator_details_label.setText("Operator not found")
            return
        
        details = f"{operator['name']} {operator['surname']} | {operator.get('email', 'N/A')}"
        if operator.get('institution'):
            details += f" | {operator['institution']}"
        
        self.operator_details_label.setText(details)
    
    def _on_operator_changed(self):
        """Handle operator selection change."""
        self._update_operator_details()
    
    def _on_add_new_operator(self):
        """Handle add new operator button."""
        from difra.gui.operator_manager import NewOperatorDialog
        
        dialog = NewOperatorDialog(self.operator_manager, self)
        
        if dialog.exec_() == QDialog.Accepted:
            new_operator_id = dialog.get_operator_id()
            
            # Refresh combo box
            self._populate_operator_combo()
            
            # Select the new operator
            for i in range(self.operator_combo.count()):
                if self.operator_combo.itemData(i) == new_operator_id:
                    self.operator_combo.setCurrentIndex(i)
                    break
    
    def validate_and_accept(self):
        """Validate inputs before accepting."""
        if not self.sample_id_edit.text().strip():
            QMessageBox.warning(
                self,
                "Missing Sample ID",
                "Please enter a Sample ID.",
            )
            return

        if not self.study_name_edit.text().strip():
            QMessageBox.warning(
                self,
                "Missing Study",
                "Please enter a Study name.",
            )
            return
        
        if not self.distance_edit.text().strip():
            QMessageBox.warning(
                self,
                "Missing Distance",
                "Please enter a distance value.",
            )
            return
        
        try:
            float(self.distance_edit.text())
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Distance",
                "Distance must be a number.",
            )
            return
        
        # Validate operator selection
        operator_id = self.operator_combo.currentData()
        if not operator_id:
            QMessageBox.warning(
                self,
                "No Operator Selected",
                "Please select an operator or add a new one.",
            )
            return
        
        self.selected_operator_id = operator_id
        self.accept()
    
    def get_parameters(self):
        """Get session parameters from dialog."""
        params = {
            'sample_id': self.sample_id_edit.text().strip(),
            'study_name': self.study_name_edit.text().strip(),
            'project_id': self.project_id_edit.text().strip() or self.study_name_edit.text().strip(),
            'distance_cm': float(self.distance_edit.text()),
            'operator_id': self.selected_operator_id,
        }
        
        return params
