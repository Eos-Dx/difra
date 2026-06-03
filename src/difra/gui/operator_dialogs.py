"""Qt dialogs for selecting and editing DIFRA operators."""

from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class OperatorSelectionDialog(QDialog):
    """Dialog for selecting or creating an operator on startup."""

    def __init__(self, operator_manager, parent=None):
        super().__init__(parent)

        self.operator_manager = operator_manager
        self.selected_operator_id: Optional[str] = None

        self.setWindowTitle("Select Operator")
        self.setModal(True)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        welcome_label = QLabel(
            "Welcome to DIFRA!\n\n"
            "Please select your operator profile or create a new one.\n"
            "This information will be stored with your measurements."
        )
        welcome_label.setWordWrap(True)
        layout.addWidget(welcome_label)

        select_group = QGroupBox("Select Existing Operator")
        select_layout = QFormLayout(select_group)

        self.operator_combo = QComboBox()
        self._populate_operator_combo()
        select_layout.addRow("Operator:", self.operator_combo)

        self.operator_details_label = QLabel()
        self.operator_details_label.setWordWrap(True)
        self.operator_details_label.setStyleSheet(
            "color: #555; background-color: #f0f0f0; padding: 8px; border-radius: 4px;"
        )
        select_layout.addRow("Details:", self.operator_details_label)
        self.operator_combo.currentIndexChanged.connect(self._on_operator_selected)

        layout.addWidget(select_group)

        new_operator_btn = QPushButton("Create New Operator...")
        new_operator_btn.clicked.connect(self._on_create_new_operator)
        layout.addWidget(new_operator_btn)

        edit_operator_btn = QPushButton("Modify Selected Operator...")
        edit_operator_btn.clicked.connect(self._on_edit_operator)
        layout.addWidget(edit_operator_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_operator_details()

    def _populate_operator_combo(self):
        """Populate the operator combo box."""
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

        details = f"<b>{operator['name']} {operator['surname']}</b><br>"
        details += f"Email: {operator.get('email', 'N/A')}<br>"

        if operator.get("phone"):
            details += f"Phone: {operator['phone']}<br>"
        if operator.get("institution"):
            details += f"Institution: {operator['institution']}<br>"

        details += f"<br><i>Operator ID: {operator_id}</i>"

        self.operator_details_label.setText(details)

    def _on_operator_selected(self):
        """Handle operator selection change."""
        self._update_operator_details()

    def _on_create_new_operator(self):
        """Handle create new operator button."""
        dialog = NewOperatorDialog(self.operator_manager, self)

        if dialog.exec_() == QDialog.Accepted:
            new_operator_id = dialog.get_operator_id()

            self._populate_operator_combo()

            for i in range(self.operator_combo.count()):
                if self.operator_combo.itemData(i) == new_operator_id:
                    self.operator_combo.setCurrentIndex(i)
                    break

    def _on_edit_operator(self):
        operator_id = self.operator_combo.currentData()
        if not operator_id:
            QMessageBox.warning(self, "No Operator Selected", "Please select an operator to modify.")
            return

        dialog = NewOperatorDialog(
            self.operator_manager,
            self,
            existing_operator_id=str(operator_id),
        )
        if dialog.exec_() == QDialog.Accepted:
            updated_operator_id = dialog.get_operator_id() or str(operator_id)
            self._populate_operator_combo()
            for i in range(self.operator_combo.count()):
                if self.operator_combo.itemData(i) == updated_operator_id:
                    self.operator_combo.setCurrentIndex(i)
                    break

    def _on_accept(self):
        """Validate and accept."""
        operator_id = self.operator_combo.currentData()

        if not operator_id:
            QMessageBox.warning(
                self,
                "No Operator Selected",
                "Please select an operator or create a new one.",
            )
            return

        self.selected_operator_id = operator_id

        self.operator_manager.set_current_operator(operator_id)

        self.accept()

    def get_selected_operator_id(self) -> Optional[str]:
        """Get the selected operator ID.

        Returns:
            Selected operator ID, or None if cancelled
        """
        return self.selected_operator_id


class NewOperatorDialog(QDialog):
    """Dialog for creating a new operator."""

    def __init__(
        self,
        operator_manager,
        parent=None,
        existing_operator_id: Optional[str] = None,
    ):
        super().__init__(parent)

        self.operator_manager = operator_manager
        self.new_operator_id: Optional[str] = None
        self._existing_operator_id: Optional[str] = existing_operator_id

        self.setWindowTitle("Modify Operator" if existing_operator_id else "Create New Operator")
        self.setModal(True)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        form_layout = QFormLayout()

        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("e.g., john_doe, operator_123")
        form_layout.addRow("Operator ID*:", self.id_edit)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g., John")
        form_layout.addRow("First Name*:", self.name_edit)

        self.surname_edit = QLineEdit()
        self.surname_edit.setPlaceholderText("e.g., Doe")
        form_layout.addRow("Last Name*:", self.surname_edit)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("e.g., john.doe@example.com")
        form_layout.addRow("Email*:", self.email_edit)

        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("Optional")
        form_layout.addRow("Phone:", self.phone_edit)

        self.institution_edit = QLineEdit()
        self.institution_edit.setPlaceholderText("Optional")
        form_layout.addRow("Institution:", self.institution_edit)

        layout.addLayout(form_layout)

        info_label = QLabel("* Required fields")
        info_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(info_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self._existing_operator_id:
            self._load_operator_for_edit(self._existing_operator_id)

    def _load_operator_for_edit(self, operator_id: str) -> None:
        operator = self.operator_manager.get_operator(operator_id)
        if not operator:
            return
        self.id_edit.setText(operator_id)
        self.id_edit.setReadOnly(True)
        self.name_edit.setText(str(operator.get("name", "")))
        self.surname_edit.setText(str(operator.get("surname", "")))
        self.email_edit.setText(str(operator.get("email", "")))
        self.phone_edit.setText(str(operator.get("phone", "")))
        self.institution_edit.setText(str(operator.get("institution", "")))

    def _confirm_modify_password(self) -> bool:
        password, ok = QInputDialog.getText(
            self,
            "Password Required",
            "Enter password to modify operator data:",
            QLineEdit.Password,
        )
        if not ok:
            return False
        if not self.operator_manager.verify_modify_password(password):
            QMessageBox.warning(self, "Invalid Password", "Incorrect password.")
            return False
        return True

    def _on_accept(self):
        """Validate and accept."""
        operator_id = self.id_edit.text().strip()
        name = self.name_edit.text().strip()
        surname = self.surname_edit.text().strip()
        email = self.email_edit.text().strip()

        if not operator_id:
            QMessageBox.warning(self, "Missing Field", "Please enter an Operator ID.")
            return

        if not name:
            QMessageBox.warning(self, "Missing Field", "Please enter a First Name.")
            return

        if not surname:
            QMessageBox.warning(self, "Missing Field", "Please enter a Last Name.")
            return

        if not email:
            QMessageBox.warning(self, "Missing Field", "Please enter an Email.")
            return

        if self._existing_operator_id and operator_id != self._existing_operator_id:
            QMessageBox.warning(
                self,
                "Operator ID Locked",
                "Operator ID cannot be changed in modify mode.",
            )
            return

        existing = self.operator_manager.get_operator(operator_id)
        is_modify = existing is not None
        if is_modify and not self._confirm_modify_password():
            return

        try:
            self.operator_manager.add_operator(
                operator_id=operator_id,
                name=name,
                surname=surname,
                email=email,
                phone=self.phone_edit.text().strip(),
                institution=self.institution_edit.text().strip(),
            )

            self.new_operator_id = operator_id

            QMessageBox.information(
                self,
                "Operator Updated" if is_modify else "Operator Created",
                (
                    f"Operator '{name} {surname}' updated successfully!"
                    if is_modify
                    else f"Operator '{name} {surname}' created successfully!"
                ),
            )

            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self,
                "Error Creating Operator",
                f"Failed to create operator:\n\n{str(e)}",
            )
            logger.error(f"Failed to create operator: {e}", exc_info=True)

    def get_operator_id(self) -> Optional[str]:
        """Get the created operator ID.

        Returns:
            New operator ID, or None if cancelled
        """
        return self.new_operator_id
