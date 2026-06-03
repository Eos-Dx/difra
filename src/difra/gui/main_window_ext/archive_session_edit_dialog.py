"""Dialog for correcting archived session Matador metadata."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import h5py
from difra.gui.qt_compat import QEvent, QTimer, Qt
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
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from difra.gui.main_window_ext.archive_session_edit_matador_mixin import (
    ArchiveSessionEditMatadorMixin,
)
from difra.gui.matador_upload_api import default_matador_cache_path


class ArchiveSessionEditDialog(ArchiveSessionEditMatadorMixin, QDialog):
    """Choose replacement Matador project/study for archived sessions."""

    def __init__(
        self,
        *,
        container_paths: List[Path],
        parent=None,
        matador_cache_path: Optional[Path] = None,
    ):
        super().__init__(parent)
        self._container_paths = [Path(path) for path in container_paths]
        self._matador_cache_path = Path(matador_cache_path or default_matador_cache_path())
        self._all_studies: List[Dict[str, Any]] = []
        self._project_choices: List[Dict[str, Any]] = []
        self._selected_project_id: Optional[int] = None
        self._selected_project_name: str = ""
        self._selected_study_id: Optional[int] = None
        self._selected_study_name: str = ""
        self._initial_project_id: Optional[int] = None
        self._initial_project_name: str = ""
        self._initial_study_id: Optional[int] = None
        self._initial_study_name: str = ""
        self._initial_specimen_id: str = ""
        self._references_loaded_from_matador = False

        self._inspect_current_selection()

        self.setWindowTitle("Edit Archived Session Metadata")
        self.setModal(True)
        self.setMinimumWidth(720)

        layout = QVBoxLayout(self)

        header_label = QLabel(
            "This will overwrite selected metadata in "
            f"{len(self._container_paths)} archived session container(s)."
        )
        header_label.setWordWrap(True)
        layout.addWidget(header_label)

        if self._container_paths:
            files_view = QPlainTextEdit()
            files_view.setReadOnly(True)
            files_view.setMaximumHeight(110)
            files_view.setPlainText(
                "\n".join(path.name for path in self._container_paths[:12])
                + (
                    ""
                    if len(self._container_paths) <= 12
                    else f"\n... and {len(self._container_paths) - 12} more"
                )
            )
            layout.addWidget(files_view)

        current_label = QLabel(self._current_selection_summary())
        current_label.setWordWrap(True)
        current_label.setStyleSheet("color: #555;")
        layout.addWidget(current_label)

        matador_group = QGroupBox("Matador Reference Data")
        matador_layout = QFormLayout(matador_group)

        self.specimen_id_edit = QLineEdit()
        self.specimen_id_edit.setText(self._initial_specimen_id)
        self.specimen_id_edit.setPlaceholderText("Leave empty to keep existing Specimen ID")
        self.specimen_id_edit.installEventFilter(self)
        matador_layout.addRow("Specimen ID:", self.specimen_id_edit)

        refresh_row = QHBoxLayout()
        self.refresh_matador_btn = QPushButton("Refresh from Matador")
        self.refresh_matador_btn.clicked.connect(self._refresh_matador_references)
        refresh_row.addWidget(self.refresh_matador_btn)
        refresh_row.addStretch()
        refresh_box = QVBoxLayout()
        refresh_box.addLayout(refresh_row)
        self.matador_status_label = QLabel(
            "Project and Study must be loaded from Matador with a runtime token."
        )
        self.matador_status_label.setWordWrap(True)
        self.matador_status_label.setStyleSheet("color: #555; font-size: 10px;")
        refresh_box.addWidget(self.matador_status_label)

        self.matador_progress_bar = QProgressBar()
        self.matador_progress_bar.setRange(0, 0)
        self.matador_progress_bar.setTextVisible(False)
        self.matador_progress_bar.hide()
        refresh_box.addWidget(self.matador_progress_bar)
        matador_layout.addRow("References:", refresh_box)

        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        matador_layout.addRow("Project:", self.project_combo)

        self.project_id_edit = QLineEdit()
        self.project_id_edit.setReadOnly(True)
        self.project_id_edit.setPlaceholderText("Matador project ID")
        matador_layout.addRow("Project ID:", self.project_id_edit)

        self.study_combo = QComboBox()
        self.study_combo.currentIndexChanged.connect(self._on_study_changed)
        matador_layout.addRow("Study:", self.study_combo)

        self.study_id_edit = QLineEdit()
        self.study_id_edit.setReadOnly(True)
        self.study_id_edit.setPlaceholderText("Matador study ID")
        matador_layout.addRow("Study ID:", self.study_id_edit)

        layout.addWidget(matador_group)

        info_label = QLabel("Specimen ID, Project and Study can be changed under password.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(info_label)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.validate_and_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)
        self._ok_button = self.button_box.button(QDialogButtonBox.Ok)
        for button in self.button_box.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)

        self._populate_project_combo([])
        self._populate_study_combo([])
        self._set_reference_controls_enabled(False)
        self._set_matador_loading_state(
            False,
            "Project and Study must be loaded from Matador with a runtime token.",
        )
        QTimer.singleShot(0, self._ensure_matador_references_loaded)

    def eventFilter(self, obj, event):
        if obj is self.specimen_id_edit and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                event.accept()
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _as_text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @classmethod
    def _coerce_optional_int(cls, value: Any) -> Optional[int]:
        text = cls._as_text(value, "").strip()
        if not text:
            return None
        try:
            return int(text)
        except Exception:
            return None

    def _inspect_current_selection(self) -> None:
        project_ids = set()
        project_names = set()
        study_ids = set()
        study_names = set()
        specimen_ids = set()

        for path in self._container_paths:
            try:
                with h5py.File(path, "r") as h5f:
                    specimen_id = self._as_text(
                        h5f.attrs.get("specimenId", h5f.attrs.get("sample_id")),
                        "",
                    ).strip()
                    project_id = self._coerce_optional_int(h5f.attrs.get("matadorProjectId"))
                    project_name = self._as_text(
                        h5f.attrs.get("matadorProjectName", h5f.attrs.get("project_id")),
                        "",
                    ).strip()
                    study_id = self._coerce_optional_int(h5f.attrs.get("matadorStudyId"))
                    study_name = self._as_text(h5f.attrs.get("study_name"), "").strip()
            except Exception:
                continue

            if specimen_id:
                specimen_ids.add(specimen_id)
            if project_id is not None:
                project_ids.add(project_id)
            if project_name:
                project_names.add(project_name)
            if study_id is not None:
                study_ids.add(study_id)
            if study_name:
                study_names.add(study_name)

        if len(specimen_ids) == 1:
            self._initial_specimen_id = next(iter(specimen_ids))
        if len(project_ids) == 1:
            self._initial_project_id = next(iter(project_ids))
        if len(project_names) == 1:
            self._initial_project_name = next(iter(project_names))
        if len(study_ids) == 1:
            self._initial_study_id = next(iter(study_ids))
        if len(study_names) == 1:
            self._initial_study_name = next(iter(study_names))

    def _current_selection_summary(self) -> str:
        specimen_text = self._initial_specimen_id or "multiple / unknown"
        project_text = self._initial_project_name or "multiple / unknown"
        if self._initial_project_id is not None and self._initial_project_name:
            project_text = f"{self._initial_project_name} [{self._initial_project_id}]"
        elif self._initial_project_id is not None:
            project_text = f"[{self._initial_project_id}]"

        study_text = self._initial_study_name or "multiple / unknown"
        if self._initial_study_id is not None and self._initial_study_name:
            study_text = f"{self._initial_study_name} [{self._initial_study_id}]"
        elif self._initial_study_id is not None:
            study_text = f"[{self._initial_study_id}]"

        return (
            f"Current selection: Specimen {specimen_text}; "
            f"Project {project_text}; Study {study_text}."
        )

    def validate_and_accept(self) -> None:
        if not self._references_loaded_from_matador:
            QMessageBox.warning(
                self,
                "Matador Data Required",
                "Project and Study must be loaded successfully from Matador before editing can continue.",
            )
            return
        if self._selected_project_id is None or not self._selected_project_name:
            QMessageBox.warning(
                self,
                "Missing Project",
                "Please choose a Matador project.",
            )
            return
        if self._selected_study_id is None or not self._selected_study_name:
            QMessageBox.warning(
                self,
                "Missing Study",
                "Please choose a Matador study.",
            )
            return
        self.accept()

    def get_selection(self) -> Dict[str, Any]:
        return {
            "specimen_id": str(self.specimen_id_edit.text() or "").strip(),
            "project_id": self._selected_project_id,
            "project_name": self._selected_project_name,
            "study_id": self._selected_study_id,
            "study_name": self._selected_study_name,
        }
