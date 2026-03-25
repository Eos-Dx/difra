"""Dialog for creating a new session container."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import (
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

from difra.gui.matador_runtime_context import (
    DEFAULT_MATADOR_URL,
    get_runtime_matador_context,
    set_runtime_matador_context,
)
from difra.gui.matador_upload_api import (
    default_matador_cache_path,
    load_matador_reference_cache,
    refresh_matador_reference_cache,
)
from difra.gui.operator_manager import OperatorManager

_MANUAL_REFERENCE_LABEL = "Manual entry / offline"


class NewSessionDialog(QDialog):
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

        self.setWindowTitle("New Session")
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()

        self.specimen_id_edit = QLineEdit()
        self.specimen_id_edit.setPlaceholderText("e.g. 64101")
        # Keep the legacy attribute name as an alias while the container contract
        # still expects sample_id internally.
        self.sample_id_edit = self.specimen_id_edit
        form_layout.addRow("Specimen ID*:", self.specimen_id_edit)

        self.study_name_edit = QLineEdit()
        self.study_name_edit.setPlaceholderText("e.g. Horizon_Grant1")
        form_layout.addRow("Study*:", self.study_name_edit)

        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("e.g. Horizon")
        form_layout.addRow("Project:", self.project_id_edit)

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
            "If Project is left blank, Study will be used.\n"
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
        layout.addWidget(buttons)

        self._restore_last_session_defaults()
        self._populate_matador_study_combo([])
        self._populate_matador_machine_combo([])
        self._load_cached_matador_references()
        self._update_operator_details()
        self._try_auto_refresh_when_runtime_token_exists()

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

    def _restore_last_operator_selection(self) -> None:
        """Restore the last confirmed operator for the next DIFRA run."""
        last_operator_id = str(
            self.settings.value("matador/last_operator_id", "", type=str) or ""
        ).strip()
        if last_operator_id:
            self.operator_manager.set_current_operator(last_operator_id)

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

    def _set_matador_status(self, message: str) -> None:
        self.matador_status_label.setText(str(message or "").strip())

    def _restore_last_session_defaults(self) -> None:
        """Restore last confirmed Matador/session defaults."""
        study_name = str(
            self.settings.value("matador/last_study_name", "", type=str) or ""
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
        if project_id:
            self.project_id_edit.setText(project_id)
            self._last_auto_project_name = project_id
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
            "matador/last_project_id",
            self.project_id_edit.text().strip(),
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

        self.matador_study_combo.blockSignals(True)
        self.matador_study_combo.setCurrentIndex(0)
        self.matador_study_combo.blockSignals(False)
        self.matador_machine_combo.blockSignals(True)
        self.matador_machine_combo.setCurrentIndex(0)
        self.matador_machine_combo.blockSignals(False)
        self._set_matador_status(
            "Remembered Matador defaults cleared. Refresh or choose new values."
        )

    def _runtime_matador_context(self):
        return get_runtime_matador_context(self.parent())

    def _prompt_for_matador_runtime_context(self) -> dict | None:
        """Ask for a runtime JWT token and Matador URL when refresh needs it."""
        existing = self._runtime_matador_context()

        dialog = QDialog(self)
        dialog.setWindowTitle("Matador API Access")
        dialog.setModal(True)
        layout = QFormLayout(dialog)

        token_edit = QLineEdit(existing.get("token", ""))
        token_edit.setEchoMode(QLineEdit.Password)
        token_edit.setPlaceholderText("Paste JWT token from /difra-api-token")
        layout.addRow("Matador Token:", token_edit)

        url_edit = QLineEdit(existing.get("matador_url") or DEFAULT_MATADOR_URL)
        layout.addRow("Matador URL:", url_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec_() != QDialog.Accepted:
            return None

        token_text = str(token_edit.text() or "").strip()
        url_text = str(url_edit.text() or "").strip()
        if not token_text:
            QMessageBox.warning(self, "Missing Token", "Matador token is required.")
            return None
        if not url_text:
            QMessageBox.warning(self, "Missing URL", "Matador URL is required.")
            return None
        return set_runtime_matador_context(
            self.parent(),
            token=token_text,
            matador_url=url_text,
        )

    def _load_cached_matador_references(self) -> None:
        """Load studies/machines from local cache for offline use."""
        try:
            payload = load_matador_reference_cache(self._matador_cache_path)
        except Exception as exc:
            self._set_matador_status(f"Failed to read Matador cache: {exc}")
            return
        self._apply_matador_reference_payload(payload)

    def _apply_matador_reference_payload(self, payload: dict) -> None:
        studies = payload.get("studies") if isinstance(payload, dict) else []
        machines = payload.get("machines") if isinstance(payload, dict) else []
        self._matador_cache_saved_at = str(payload.get("savedAt") or "").strip()
        self._populate_matador_study_combo(studies if isinstance(studies, list) else [])
        self._populate_matador_machine_combo(
            machines if isinstance(machines, list) else []
        )
        if studies or machines:
            source = "Matador cache loaded"
            if self._matador_cache_saved_at:
                source = f"{source} ({self._matador_cache_saved_at})"
            self._set_matador_status(
                f"{source}. Refresh to update or enter IDs manually if needed."
            )
        else:
            self._set_matador_status(
                "No Matador cache loaded. You can refresh from API or enter IDs manually."
            )

    def _populate_matador_study_combo(self, studies) -> None:
        current_text = self.matador_study_id_edit.text().strip()
        self.matador_study_combo.blockSignals(True)
        self.matador_study_combo.clear()
        self.matador_study_combo.addItem(_MANUAL_REFERENCE_LABEL, None)

        selected_index = 0
        ordered = sorted(
            [item for item in studies if isinstance(item, dict)],
            key=lambda item: (str(item.get("name") or "").lower(), int(item.get("id") or 0)),
        )
        for item in ordered:
            label = str(item.get("name") or "").strip() or f"Study {item.get('id')}"
            project_name = str(item.get("projectName") or "").strip()
            if project_name:
                label = f"{label} ({project_name})"
            self.matador_study_combo.addItem(label, item)
            if current_text and str(item.get("id")) == current_text:
                selected_index = self.matador_study_combo.count() - 1

        self.matador_study_combo.setCurrentIndex(selected_index)
        self.matador_study_combo.blockSignals(False)
        self._on_matador_study_changed()

    def _populate_matador_machine_combo(self, machines) -> None:
        current_text = self.matador_machine_id_edit.text().strip()
        self.matador_machine_combo.blockSignals(True)
        self.matador_machine_combo.clear()
        self.matador_machine_combo.addItem(_MANUAL_REFERENCE_LABEL, None)

        selected_index = 0
        ordered = sorted(
            [item for item in machines if isinstance(item, dict)],
            key=lambda item: (str(item.get("name") or "").lower(), int(item.get("id") or 0)),
        )
        for item in ordered:
            label = str(item.get("name") or "").strip() or f"Machine {item.get('id')}"
            self.matador_machine_combo.addItem(label, item)
            if current_text and str(item.get("id")) == current_text:
                selected_index = self.matador_machine_combo.count() - 1

        self.matador_machine_combo.setCurrentIndex(selected_index)
        self.matador_machine_combo.blockSignals(False)
        self._on_matador_machine_changed()

    def _on_matador_study_changed(self) -> None:
        study = self.matador_study_combo.currentData()
        if not isinstance(study, dict):
            return

        study_id = str(study.get("id") or "").strip()
        if study_id:
            self.matador_study_id_edit.setText(study_id)

        study_name = str(study.get("name") or "").strip()
        current_study_name = self.study_name_edit.text().strip()
        if study_name and (
            not current_study_name or current_study_name == self._last_auto_study_name
        ):
            self.study_name_edit.setText(study_name)
            self._last_auto_study_name = study_name

        project_name = str(study.get("projectName") or "").strip()
        current_project_name = self.project_id_edit.text().strip()
        if project_name and (
            not current_project_name
            or current_project_name == self._last_auto_project_name
        ):
            self.project_id_edit.setText(project_name)
            self._last_auto_project_name = project_name

    def _on_matador_machine_changed(self) -> None:
        machine = self.matador_machine_combo.currentData()
        if not isinstance(machine, dict):
            return
        machine_id = str(machine.get("id") or "").strip()
        if machine_id:
            self.matador_machine_id_edit.setText(machine_id)

    def _refresh_matador_references(self) -> None:
        """Refresh the local Matador studies/machines cache from API."""
        context = self._runtime_matador_context()
        if not context.get("token"):
            context = self._prompt_for_matador_runtime_context()
            if not context:
                return
        try:
            payload = refresh_matador_reference_cache(
                base_url=context.get("matador_url") or DEFAULT_MATADOR_URL,
                token=context.get("token") or "",
                cache_path=self._matador_cache_path,
            )
        except Exception as exc:
            self._set_matador_status(f"Matador refresh failed: {exc}")
            QMessageBox.warning(
                self,
                "Matador Refresh Failed",
                "Could not refresh Studies/Machines from Matador.\n\n"
                f"{exc}\n\n"
                "You can continue with cached values or enter IDs manually.",
            )
            return

        self._apply_matador_reference_payload(payload)

    def _try_auto_refresh_when_runtime_token_exists(self) -> None:
        """Refresh automatically when a runtime token already exists and cache is empty."""
        if self.matador_study_combo.count() > 1 or self.matador_machine_combo.count() > 1:
            return
        context = self._runtime_matador_context()
        if not context.get("token"):
            return
        try:
            payload = refresh_matador_reference_cache(
                base_url=context.get("matador_url") or DEFAULT_MATADOR_URL,
                token=context.get("token") or "",
                cache_path=self._matador_cache_path,
            )
        except Exception as exc:
            self._set_matador_status(f"Matador auto-refresh skipped: {exc}")
            return
        self._apply_matador_reference_payload(payload)

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
        return {
            "sample_id": specimen_id,
            "specimenId": specimen_id,
            "study_name": self.study_name_edit.text().strip(),
            "project_id": self.project_id_edit.text().strip()
            or self.study_name_edit.text().strip(),
            "matadorStudyId": int(self.matador_study_id_edit.text().strip()),
            "matadorMachineId": int(self.matador_machine_id_edit.text().strip()),
            "distance_cm": float(self.distance_edit.text()),
            "operator_id": self.selected_operator_id,
        }
