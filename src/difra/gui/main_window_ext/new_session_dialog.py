"""Dialog for creating a new session container."""

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from difra.gui.operator_manager import OperatorManager


class NewSessionDialog(QDialog):
    """Dialog for creating a new session.

    Prompts user for:
    - Sample ID (required)
    - Study (required)
    - Project (optional; falls back to study)
    - Distance in cm (required)
    - Operator (required)
    """

    def __init__(
        self,
        operator_manager: OperatorManager,
        parent=None,
        default_distance: float = None,
    ):
        super().__init__(parent)

        self.operator_manager = operator_manager
        self.selected_operator_id = None

        self.setWindowTitle("New Session")
        self.setModal(True)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        form_layout = QFormLayout()

        self.sample_id_edit = QLineEdit()
        self.sample_id_edit.setPlaceholderText("e.g. SAMPLE_001")
        form_layout.addRow("Sample ID*:", self.sample_id_edit)

        self.study_name_edit = QLineEdit()
        self.study_name_edit.setPlaceholderText("e.g. STUDY_2026_A")
        form_layout.addRow("Study*:", self.study_name_edit)

        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("e.g. PROJECT_2026_A")
        form_layout.addRow("Project:", self.project_id_edit)

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

        operator_group = QGroupBox("Operator Selection")
        operator_layout = QFormLayout(operator_group)

        self.operator_combo = QComboBox()
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
            "Beam energy: Read from global config\n"
            "<b>Note:</b> Distance must match technical container distance.\n"
            "If Project is left blank, Study will be used."
        )
        info_label.setStyleSheet("color: gray; font-style: italic;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_operator_details()

    def _populate_operator_combo(self):
        """Populate operator combo box."""
        self.operator_combo.clear()

        operators = self.operator_manager.get_all_operators()

        if not operators:
            self.operator_combo.addItem("No operators defined", None)
            return

        current_id = self.operator_manager.get_current_operator_id()
        current_index = 0

        for i, (op_id, _op_info) in enumerate(sorted(operators.items())):
            display_name = self.operator_manager.get_operator_display_name(op_id)
            self.operator_combo.addItem(display_name, op_id)
            if op_id == current_id:
                current_index = i

        if current_id and current_index < self.operator_combo.count():
            self.operator_combo.setCurrentIndex(current_index)

    def _update_operator_details(self):
        """Update operator details display."""
        operator_id = self.operator_combo.currentData()

        if not operator_id:
            self.operator_details_label.setText("No operator selected")
            return

        operator = self.operator_manager.get_operator(operator_id)
        if not operator:
            self.operator_details_label.setText("Operator not found")
            return

        details = f"{operator['name']} {operator['surname']} | {operator.get('email', 'N/A')}"
        if operator.get("institution"):
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
            self._populate_operator_combo()
            for i in range(self.operator_combo.count()):
                if self.operator_combo.itemData(i) == new_operator_id:
                    self.operator_combo.setCurrentIndex(i)
                    break

    def validate_and_accept(self):
        """Validate inputs before accepting."""
        if not self.sample_id_edit.text().strip():
            QMessageBox.warning(self, "Missing Sample ID", "Please enter a Sample ID.")
            return

        if not self.study_name_edit.text().strip():
            QMessageBox.warning(self, "Missing Study", "Please enter a Study name.")
            return

        if not self.distance_edit.text().strip():
            QMessageBox.warning(self, "Missing Distance", "Please enter a distance value.")
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
        self.accept()

    def get_parameters(self):
        """Get session parameters from dialog."""
        return {
            "sample_id": self.sample_id_edit.text().strip(),
            "study_name": self.study_name_edit.text().strip(),
            "project_id": self.project_id_edit.text().strip()
            or self.study_name_edit.text().strip(),
            "distance_cm": float(self.distance_edit.text()),
            "operator_id": self.selected_operator_id,
        }
