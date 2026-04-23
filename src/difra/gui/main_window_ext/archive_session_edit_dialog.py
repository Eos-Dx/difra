"""Dialog for correcting archived session Matador project/study metadata."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import h5py
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
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from difra.gui.matador_runtime_context import (
    get_runtime_matador_context,
    set_runtime_matador_context,
)
from difra.gui.matador_upload_api import (
    default_matador_cache_path,
    refresh_matador_reference_cache,
)

_SELECT_PROJECT_LABEL = "Select project"
_SELECT_STUDY_LABEL = "Select study"


class ArchiveSessionEditDialog(QDialog):
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
        self._references_loaded_from_matador = False

        self._inspect_current_selection()

        self.setWindowTitle("Edit Archived Session Metadata")
        self.setModal(True)
        self.setMinimumWidth(720)

        layout = QVBoxLayout(self)

        header_label = QLabel(
            f"This will overwrite Project and Study in {len(self._container_paths)} archived session container(s)."
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

        info_label = QLabel(
            "Only Project and Study will be changed. Specimen ID will remain untouched."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(info_label)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.validate_and_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)
        self._ok_button = self.button_box.button(QDialogButtonBox.Ok)

        self._populate_project_combo([])
        self._populate_study_combo([])
        self._set_reference_controls_enabled(False)
        self._ensure_matador_references_loaded()

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

        for path in self._container_paths:
            try:
                with h5py.File(path, "r") as h5f:
                    project_id = self._coerce_optional_int(h5f.attrs.get("matadorProjectId"))
                    project_name = self._as_text(
                        h5f.attrs.get("matadorProjectName", h5f.attrs.get("project_id")),
                        "",
                    ).strip()
                    study_id = self._coerce_optional_int(h5f.attrs.get("matadorStudyId"))
                    study_name = self._as_text(h5f.attrs.get("study_name"), "").strip()
            except Exception:
                continue

            if project_id is not None:
                project_ids.add(project_id)
            if project_name:
                project_names.add(project_name)
            if study_id is not None:
                study_ids.add(study_id)
            if study_name:
                study_names.add(study_name)

        if len(project_ids) == 1:
            self._initial_project_id = next(iter(project_ids))
        if len(project_names) == 1:
            self._initial_project_name = next(iter(project_names))
        if len(study_ids) == 1:
            self._initial_study_id = next(iter(study_ids))
        if len(study_names) == 1:
            self._initial_study_name = next(iter(study_names))

    def _current_selection_summary(self) -> str:
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

        return f"Current selection: Project {project_text}; Study {study_text}."

    def _runtime_matador_context(self) -> Dict[str, str]:
        return get_runtime_matador_context(self.parent() or self)

    def _set_matador_status(self, message: str) -> None:
        self.matador_status_label.setText(str(message or "").strip())

    def _set_reference_controls_enabled(self, enabled: bool) -> None:
        self.refresh_matador_btn.setEnabled(True)
        self.project_combo.setEnabled(bool(enabled))
        self.study_combo.setEnabled(bool(enabled))
        self.project_id_edit.setEnabled(bool(enabled))
        self.study_id_edit.setEnabled(bool(enabled))
        if self._ok_button is not None:
            self._ok_button.setEnabled(bool(enabled))

    def _prompt_for_matador_runtime_context(self) -> Optional[Dict[str, str]]:
        existing = self._runtime_matador_context()

        if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
            token_text = str(existing.get("token") or "").strip()
            url_text = str(existing.get("matador_url") or "").strip()
            if token_text and url_text:
                return {
                    "token": token_text,
                    "matador_url": url_text,
                }
            return None

        dialog = QDialog(self)
        dialog.setWindowTitle("Matador API Access")
        dialog.setModal(True)
        layout = QFormLayout(dialog)

        token_edit = QLineEdit(existing.get("token", ""))
        token_edit.setEchoMode(QLineEdit.Password)
        token_edit.setPlaceholderText("Paste JWT token from /difra-api-token")
        layout.addRow("Matador Token:", token_edit)

        url_edit = QLineEdit(existing.get("matador_url") or "")
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
            self.parent() or self,
            token=token_text,
            matador_url=url_text,
        )

    def _ensure_matador_references_loaded(self) -> None:
        context = self._runtime_matador_context()
        if context.get("token") and context.get("matador_url"):
            self._refresh_matador_references()
            return

        context = self._prompt_for_matador_runtime_context()
        if not context:
            self._set_reference_controls_enabled(False)
            self._set_matador_status(
                "Matador token and URL are required to load Project/Study choices."
            )
            return

        self._refresh_matador_references()

    def _apply_matador_reference_payload(self, payload: Dict[str, Any], *, source_label: str) -> None:
        studies = payload.get("studies") if isinstance(payload, dict) else []
        self._all_studies = sorted(
            [item for item in studies if isinstance(item, dict)],
            key=lambda item: (
                str(item.get("projectName") or "").lower(),
                str(item.get("name") or "").lower(),
                int(item.get("id") or 0),
            ),
        )

        projects_by_key: Dict[tuple, Dict[str, Any]] = {}
        for study in self._all_studies:
            project_id = self._coerce_optional_int(study.get("projectId"))
            project_name = str(study.get("projectName") or "").strip()
            if not project_name and project_id is None:
                continue
            key = (project_id, project_name.lower())
            projects_by_key[key] = {
                "id": project_id,
                "name": project_name or f"Project {project_id}",
            }
        self._project_choices = sorted(
            projects_by_key.values(),
            key=lambda item: (str(item.get("name") or "").lower(), int(item.get("id") or 0)),
        )

        self._populate_project_combo(self._project_choices)

        if self._all_studies:
            saved_at = str(payload.get("savedAt") or "").strip()
            source = source_label
            if saved_at:
                source = f"{source} ({saved_at})"
            self._set_matador_status(
                f"{source}. Choose a project, then choose the replacement study."
            )
            self._references_loaded_from_matador = True
            self._set_reference_controls_enabled(True)
        else:
            self._references_loaded_from_matador = False
            self._set_reference_controls_enabled(False)
            self._set_matador_status(
                "Matador returned no studies. Refresh again or check the token/URL."
            )

    def _populate_project_combo(self, projects: List[Dict[str, Any]]) -> None:
        selected_name = self._selected_project_name or self._initial_project_name
        selected_id = self._selected_project_id
        if selected_id is None:
            selected_id = self._initial_project_id

        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem(_SELECT_PROJECT_LABEL, None)

        selected_index = 0
        for item in projects:
            label = str(item.get("name") or "").strip() or f"Project {item.get('id')}"
            project_id = self._coerce_optional_int(item.get("id"))
            if project_id is not None:
                label = f"{label} [{project_id}]"
            self.project_combo.addItem(label, item)

            if selected_id is not None and project_id == selected_id:
                selected_index = self.project_combo.count() - 1
            elif selected_index == 0 and selected_name:
                item_name = str(item.get("name") or "").strip()
                if item_name and item_name == selected_name:
                    selected_index = self.project_combo.count() - 1

        self.project_combo.setCurrentIndex(selected_index)
        self.project_combo.blockSignals(False)
        self._on_project_changed()

    def _populate_study_combo(self, studies: List[Dict[str, Any]]) -> None:
        selected_name = self._selected_study_name or self._initial_study_name
        selected_id = self._selected_study_id
        if selected_id is None:
            selected_id = self._initial_study_id

        self.study_combo.blockSignals(True)
        self.study_combo.clear()
        self.study_combo.addItem(_SELECT_STUDY_LABEL, None)

        selected_index = 0
        for item in studies:
            label = str(item.get("name") or "").strip() or f"Study {item.get('id')}"
            study_id = self._coerce_optional_int(item.get("id"))
            if study_id is not None:
                label = f"{label} [{study_id}]"
            self.study_combo.addItem(label, item)

            if selected_id is not None and study_id == selected_id:
                selected_index = self.study_combo.count() - 1
            elif selected_index == 0 and selected_name:
                item_name = str(item.get("name") or "").strip()
                if item_name and item_name == selected_name:
                    selected_index = self.study_combo.count() - 1

        self.study_combo.setCurrentIndex(selected_index)
        self.study_combo.blockSignals(False)
        self._on_study_changed()

    def _on_project_changed(self) -> None:
        project = self.project_combo.currentData()
        if not isinstance(project, dict):
            self._selected_project_id = None
            self._selected_project_name = ""
            self.project_id_edit.clear()
            self._populate_study_combo([])
            return

        self._selected_project_id = self._coerce_optional_int(project.get("id"))
        self._selected_project_name = str(project.get("name") or "").strip()
        self.project_id_edit.setText(
            "" if self._selected_project_id is None else str(self._selected_project_id)
        )

        filtered = []
        for study in self._all_studies:
            study_project_id = self._coerce_optional_int(study.get("projectId"))
            study_project_name = str(study.get("projectName") or "").strip()
            if self._selected_project_id is not None and study_project_id == self._selected_project_id:
                filtered.append(study)
                continue
            if (
                self._selected_project_name
                and study_project_name
                and study_project_name == self._selected_project_name
            ):
                filtered.append(study)
        self._populate_study_combo(filtered)

    def _select_project_for_study(self, study: Dict[str, Any]) -> None:
        project_id = self._coerce_optional_int(study.get("projectId"))
        project_name = str(study.get("projectName") or "").strip()
        for index in range(self.project_combo.count()):
            item = self.project_combo.itemData(index)
            if not isinstance(item, dict):
                continue
            item_id = self._coerce_optional_int(item.get("id"))
            item_name = str(item.get("name") or "").strip()
            if project_id is not None and item_id == project_id:
                self.project_combo.blockSignals(True)
                self.project_combo.setCurrentIndex(index)
                self.project_combo.blockSignals(False)
                self._selected_project_id = item_id
                self._selected_project_name = item_name
                self.project_id_edit.setText(str(item_id))
                return
            if project_name and item_name == project_name:
                self.project_combo.blockSignals(True)
                self.project_combo.setCurrentIndex(index)
                self.project_combo.blockSignals(False)
                self._selected_project_id = item_id
                self._selected_project_name = item_name
                self.project_id_edit.setText("" if item_id is None else str(item_id))
                return

    def _on_study_changed(self) -> None:
        study = self.study_combo.currentData()
        if not isinstance(study, dict):
            self._selected_study_id = None
            self._selected_study_name = ""
            self.study_id_edit.clear()
            return

        self._selected_study_id = self._coerce_optional_int(study.get("id"))
        self._selected_study_name = str(study.get("name") or "").strip()
        self.study_id_edit.setText(
            "" if self._selected_study_id is None else str(self._selected_study_id)
        )
        self._select_project_for_study(study)

    def _refresh_matador_references(self) -> bool:
        context = self._runtime_matador_context()
        if not context.get("token"):
            context = self._prompt_for_matador_runtime_context()
            if not context:
                self._references_loaded_from_matador = False
                self._set_reference_controls_enabled(False)
                self._set_matador_status(
                    "Matador token and URL are required to load Project/Study choices."
                )
                return False

        try:
            payload = refresh_matador_reference_cache(
                base_url=context.get("matador_url") or "",
                token=context.get("token") or "",
                cache_path=self._matador_cache_path,
            )
        except Exception as exc:
            self._references_loaded_from_matador = False
            self._set_reference_controls_enabled(False)
            self._set_matador_status(f"Matador refresh failed: {exc}")
            QMessageBox.warning(
                self,
                "Matador Refresh Failed",
                "Could not refresh projects/studies from Matador.\n\n"
                f"{exc}\n\n"
                "Editing is blocked until Project/Study are loaded successfully from Matador.",
            )
            return False

        self._apply_matador_reference_payload(
            payload,
            source_label="Matador data loaded",
        )
        return True

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
            "project_id": self._selected_project_id,
            "project_name": self._selected_project_name,
            "study_id": self._selected_study_id,
            "study_name": self._selected_study_name,
        }
