"""Dialog for editing active session container metadata."""

from __future__ import annotations

from typing import Any, Dict

from difra.gui.qt_compat import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)


class SessionContainerInfoDialog(QDialog):
    """Edit active session metadata before send/archive."""

    def __init__(self, *, operator_manager, initial: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.operator_manager = operator_manager
        self.initial = dict(initial or {})

        self.setWindowTitle("Edit Container Information")
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.specimen_id_edit = QLineEdit(str(self.initial.get("specimenId") or ""))
        form.addRow("Specimen ID*:", self.specimen_id_edit)

        self.project_name_edit = QLineEdit(
            str(
                self.initial.get("project_id")
                or self.initial.get("matadorProjectName")
                or ""
            )
        )
        form.addRow("Project*:", self.project_name_edit)

        self.study_name_edit = QLineEdit(str(self.initial.get("study_name") or ""))
        form.addRow("Study / Group*:", self.study_name_edit)

        self.matador_project_id_edit = QLineEdit(
            self._optional_text(self.initial.get("matadorProjectId"))
        )
        form.addRow("Matador Project ID*:", self.matador_project_id_edit)

        self.matador_study_id_edit = QLineEdit(
            self._optional_text(self.initial.get("matadorStudyId"))
        )
        form.addRow("Matador Study ID*:", self.matador_study_id_edit)

        self.matador_machine_id_edit = QLineEdit(
            self._optional_text(self.initial.get("matadorMachineId"))
        )
        form.addRow("Matador Machine ID*:", self.matador_machine_id_edit)

        self.operator_combo = QComboBox()
        self._populate_operator_combo(str(self.initial.get("operator_id") or ""))
        form.addRow("Operator*:", self.operator_combo)

        layout.addLayout(form)

        info = QLabel(
            "Updates only session metadata. Distance and technical snapshot are unchanged."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(info)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _optional_text(value: Any) -> str:
        if value in (None, ""):
            return ""
        return str(value)

    @staticmethod
    def _coerce_required_int(text: str, label: str) -> int:
        value = str(text or "").strip()
        if not value:
            raise ValueError(f"{label} is required.")
        return int(value)

    def _populate_operator_combo(self, selected_operator_id: str) -> None:
        self.operator_combo.clear()
        operators = self.operator_manager.get_all_operators()
        if not operators:
            if selected_operator_id:
                self.operator_combo.addItem(selected_operator_id, selected_operator_id)
                return
            self.operator_combo.addItem("No operators defined", None)
            return

        selected_index = 0
        found_selected = False
        for index, (operator_id, _operator) in enumerate(sorted(operators.items())):
            label = self.operator_manager.get_operator_display_name(operator_id)
            self.operator_combo.addItem(label, operator_id)
            if operator_id == selected_operator_id:
                selected_index = index
                found_selected = True
        if selected_operator_id and not found_selected:
            self.operator_combo.addItem(selected_operator_id, selected_operator_id)
            selected_index = self.operator_combo.count() - 1
        self.operator_combo.setCurrentIndex(selected_index)

    def validate_and_accept(self) -> None:
        if not self.specimen_id_edit.text().strip():
            QMessageBox.warning(self, "Missing Specimen ID", "Please enter Specimen ID.")
            return
        if not self.project_name_edit.text().strip():
            QMessageBox.warning(self, "Missing Project", "Please enter Project.")
            return
        if not self.study_name_edit.text().strip():
            QMessageBox.warning(self, "Missing Study", "Please enter Study / Group.")
            return
        if not self.operator_combo.currentData():
            QMessageBox.warning(self, "Missing Operator", "Please choose Operator.")
            return
        try:
            self._coerce_required_int(
                self.matador_project_id_edit.text(), "Matador Project ID"
            )
            self._coerce_required_int(
                self.matador_study_id_edit.text(), "Matador Study ID"
            )
            self._coerce_required_int(
                self.matador_machine_id_edit.text(), "Matador Machine ID"
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Matador IDs", str(exc))
            return
        self.accept()

    def get_parameters(self) -> Dict[str, Any]:
        project_name = self.project_name_edit.text().strip()
        return {
            "specimen_id": self.specimen_id_edit.text().strip(),
            "study_name": self.study_name_edit.text().strip(),
            "project_id": project_name,
            "operator_id": self.operator_combo.currentData(),
            "matador_project_id": int(self.matador_project_id_edit.text().strip()),
            "matador_project_name": project_name,
            "matador_study_id": int(self.matador_study_id_edit.text().strip()),
            "matador_machine_id": int(self.matador_machine_id_edit.text().strip()),
        }
