"""Dialog for creating a new session container."""

from __future__ import annotations

from pathlib import Path

from difra.gui.qt_compat import QEvent, QSettings, Qt
from difra.gui.qt_compat import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from difra.gui.matador_upload_api import (
    default_matador_cache_path,
)
from difra.gui.main_window_ext.new_session_dialog_matador_mixin import (
    NewSessionDialogMatadorMixin,
)
from difra.gui.main_window_ext.new_session_dialog_operator_mixin import (
    NewSessionDialogOperatorMixin,
)
from difra.gui.operator_manager import OperatorManager


class NewSessionDialog(
    NewSessionDialogOperatorMixin,
    NewSessionDialogMatadorMixin,
    QDialog,
):
    """Dialog for creating a new session."""

    def __init__(
        self,
        operator_manager: OperatorManager,
        parent=None,
        default_distance: float = None,
        matador_cache_path: Path | None = None,
    ):
        super().__init__(parent)

        self.operator_manager = operator_manager
        self.selected_operator_id = None
        self.settings = QSettings("EOSDx", "DiFRA")
        self._matador_cache_path = Path(matador_cache_path or default_matador_cache_path())
        self._matador_cache_saved_at = ""
        self._last_auto_study_name = ""
        self._last_auto_project_name = ""
        self._selected_matador_project_id = None
        self._selected_matador_project_name = ""

        self.setWindowTitle("New Session")
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.specimen_id_edit = QLineEdit()
        self.specimen_id_edit.setPlaceholderText("e.g. 64101")
        self.specimen_id_edit.installEventFilter(self)
        # Keep the legacy attribute name as an alias while the container contract
        # still expects sample_id internally.
        self.sample_id_edit = self.specimen_id_edit
        form_layout.addRow("Specimen ID*:", self.specimen_id_edit)

        self.study_name_edit = QLineEdit()
        self.study_name_edit.setPlaceholderText("e.g. Horizon_Grant1")
        form_layout.addRow("Study*:", self.study_name_edit)

        self.project_id_edit = QLineEdit()
        self.project_id_edit.hide()

        matador_group = QGroupBox("Matador Reference Data")
        matador_layout = QFormLayout(matador_group)

        refresh_row = QHBoxLayout()
        self.refresh_matador_btn = QPushButton("Refresh from Matador")
        self.refresh_matador_btn.clicked.connect(self._refresh_matador_references)
        refresh_row.addWidget(self.refresh_matador_btn)
        self.clear_matador_defaults_btn = QPushButton("Clear Remembered Defaults")
        self.clear_matador_defaults_btn.clicked.connect(
            self._clear_remembered_matador_defaults
        )
        refresh_row.addWidget(self.clear_matador_defaults_btn)
        refresh_row.addStretch()
        refresh_container = QVBoxLayout()
        refresh_container.addLayout(refresh_row)
        self.matador_status_label = QLabel(
            "No Matador cache loaded. You can refresh from API or enter IDs manually."
        )
        self.matador_status_label.setWordWrap(True)
        self.matador_status_label.setStyleSheet("color: #555; font-size: 10px;")
        refresh_container.addWidget(self.matador_status_label)
        matador_layout.addRow("References:", refresh_container)

        self.matador_study_combo = QComboBox()
        self.matador_study_combo.currentIndexChanged.connect(self._on_matador_study_changed)
        matador_layout.addRow("Matador Study:", self.matador_study_combo)

        self.matador_machine_combo = QComboBox()
        self.matador_machine_combo.currentIndexChanged.connect(
            self._on_matador_machine_changed
        )
        matador_layout.addRow("Matador Machine:", self.matador_machine_combo)

        self.matador_study_id_edit = QLineEdit()
        self.matador_study_id_edit.setPlaceholderText("e.g. 1701")
        matador_layout.addRow("Matador Study ID*:", self.matador_study_id_edit)

        self.matador_machine_id_edit = QLineEdit()
        self.matador_machine_id_edit.setPlaceholderText("e.g. 1751")
        matador_layout.addRow("Matador Machine ID*:", self.matador_machine_id_edit)

        layout.addLayout(form_layout)
        layout.addWidget(matador_group)

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

        operator_group = QGroupBox("Operator Selection")
        operator_layout = QFormLayout(operator_group)

        self.operator_combo = QComboBox()
        self._restore_last_operator_selection()
        self._populate_operator_combo()
        operator_layout.addRow("Operator*:", self.operator_combo)

        self.operator_details_label = QLabel()
        self.operator_details_label.setWordWrap(True)
        self.operator_details_label.setStyleSheet(
            "color: #555; background-color: #f0f0f0; padding: 5px; border-radius: 3px; font-size: 10px;"
        )
        operator_layout.addRow("Details:", self.operator_details_label)
        self.operator_combo.currentIndexChanged.connect(self._on_operator_changed)

        new_operator_btn = QPushButton("Add New Operator...")
        new_operator_btn.clicked.connect(self._on_add_new_operator)
        operator_layout.addRow("", new_operator_btn)

        layout.addWidget(operator_group)

        info_label = QLabel(
            "* Required fields\n\n"
            "Use Refresh to pull Studies/Machines from Matador with the runtime JWT token.\n"
            "If Matador is unavailable, enter Study ID and Machine ID manually.\n"
            "Beam energy: Read from global config.\n"
            "<b>Note:</b> Distance must match technical container distance.\n"
            "Project is read from the selected Matador Study and stored in the session metadata.\n"
            "Specimen ID can be filled by QR scanner input."
        )
        info_label.setStyleSheet("color: gray; font-style: italic;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        for button in buttons.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)
        layout.addWidget(buttons)

        self._restore_last_session_defaults()
        self._populate_matador_study_combo([])
        self._populate_matador_machine_combo([])
        self._load_cached_matador_references()
        self._update_operator_details()
        self._try_auto_refresh_when_runtime_token_exists()

    def eventFilter(self, obj, event):
        if obj is self.specimen_id_edit and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _restore_last_session_defaults(self) -> None:
        """Restore last confirmed Matador/session defaults."""
        study_name = str(
            self.settings.value("matador/last_study_name", "", type=str) or ""
        ).strip()
        project_name = str(
            self.settings.value("matador/last_project_name", "", type=str) or ""
        ).strip()
        project_id = str(
            self.settings.value("matador/last_project_id", "", type=str) or ""
        ).strip()
        matador_study_id = str(
            self.settings.value("matador/last_matador_study_id", "", type=str) or ""
        ).strip()
        matador_machine_id = str(
            self.settings.value("matador/last_matador_machine_id", "", type=str) or ""
        ).strip()
        if study_name:
            self.study_name_edit.setText(study_name)
            self._last_auto_study_name = study_name
        if project_name:
            self.project_id_edit.setText(project_name)
            self._last_auto_project_name = project_name
        elif study_name:
            self.project_id_edit.setText(study_name)
            self._last_auto_project_name = study_name
        if project_id:
            try:
                self._selected_matador_project_id = int(project_id)
            except Exception:
                self._selected_matador_project_id = None
        else:
            self._selected_matador_project_id = None
        self._selected_matador_project_name = self.project_id_edit.text().strip()
        if matador_study_id:
            self.matador_study_id_edit.setText(matador_study_id)
        if matador_machine_id:
            self.matador_machine_id_edit.setText(matador_machine_id)

    def _save_last_session_defaults(self, operator_id: str) -> None:
        """Persist last selected defaults for the next DIFRA launch."""
        self.settings.setValue("matador/last_operator_id", operator_id)
        self.settings.setValue(
            "matador/last_study_name",
            self.study_name_edit.text().strip(),
        )
        self.settings.setValue(
            "matador/last_project_name",
            self.project_id_edit.text().strip() or self.study_name_edit.text().strip(),
        )
        self.settings.setValue(
            "matador/last_project_id",
            (
                ""
                if self._selected_matador_project_id in (None, "")
                else str(self._selected_matador_project_id)
            ),
        )
        self.settings.setValue(
            "matador/last_matador_study_id",
            self.matador_study_id_edit.text().strip(),
        )
        self.settings.setValue(
            "matador/last_matador_machine_id",
            self.matador_machine_id_edit.text().strip(),
        )
        self.settings.sync()

    def _clear_remembered_matador_defaults(self) -> None:
        """Clear persisted Matador defaults without touching the runtime token/cache."""
        keys = [
            "matador/last_operator_id",
            "matador/last_study_name",
            "matador/last_project_name",
            "matador/last_project_id",
            "matador/last_matador_study_id",
            "matador/last_matador_machine_id",
        ]
        for key in keys:
            self.settings.remove(key)
        self.settings.sync()

        self.study_name_edit.clear()
        self.project_id_edit.clear()
        self.matador_study_id_edit.clear()
        self.matador_machine_id_edit.clear()
        self._last_auto_study_name = ""
        self._last_auto_project_name = ""
        self._selected_matador_project_id = None
        self._selected_matador_project_name = ""

        self.matador_study_combo.blockSignals(True)
        self.matador_study_combo.setCurrentIndex(0)
        self.matador_study_combo.blockSignals(False)
        self.matador_machine_combo.blockSignals(True)
        self.matador_machine_combo.setCurrentIndex(0)
        self.matador_machine_combo.blockSignals(False)
        self._set_matador_status(
            "Remembered Matador defaults cleared. Refresh or choose new values."
        )

    def validate_and_accept(self):
        """Validate inputs before accepting."""
        if not self.specimen_id_edit.text().strip():
            QMessageBox.warning(
                self,
                "Missing Specimen ID",
                "Please enter a Specimen ID.",
            )
            return

        if not self.study_name_edit.text().strip():
            QMessageBox.warning(self, "Missing Study", "Please enter a Study name.")
            return

        if not self.matador_study_id_edit.text().strip():
            QMessageBox.warning(
                self,
                "Missing Matador Study ID",
                "Please enter a Matador Study ID.",
            )
            return

        if not self.matador_machine_id_edit.text().strip():
            QMessageBox.warning(
                self,
                "Missing Matador Machine ID",
                "Please enter a Matador Machine ID.",
            )
            return

        try:
            int(self.matador_study_id_edit.text().strip())
            int(self.matador_machine_id_edit.text().strip())
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Matador IDs",
                "Matador Study ID and Machine ID must be integers.",
            )
            return

        if not self.distance_edit.text().strip():
            QMessageBox.warning(
                self, "Missing Distance", "Please enter a distance value."
            )
            return

        try:
            float(self.distance_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid Distance", "Distance must be a number.")
            return

        operator_id = self.operator_combo.currentData()
        if not operator_id:
            QMessageBox.warning(
                self,
                "No Operator Selected",
                "Please select an operator or add a new one.",
            )
            return

        self.selected_operator_id = operator_id
        self.operator_manager.set_current_operator(operator_id)
        self._save_last_session_defaults(operator_id)
        self.accept()

    def get_parameters(self):
        """Get session parameters from dialog."""
        specimen_id = self.specimen_id_edit.text().strip()
        project_name = self.project_id_edit.text().strip() or self.study_name_edit.text().strip()
        return {
            "sample_id": specimen_id,
            "specimenId": specimen_id,
            "study_name": self.study_name_edit.text().strip(),
            "project_id": project_name,
            "matadorProjectId": self._selected_matador_project_id,
            "matadorProjectName": self._selected_matador_project_name or project_name,
            "matadorStudyId": int(self.matador_study_id_edit.text().strip()),
            "matadorMachineId": int(self.matador_machine_id_edit.text().strip()),
            "distance_cm": float(self.distance_edit.text()),
            "operator_id": self.selected_operator_id,
        }
