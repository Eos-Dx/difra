"""Matador reference-loading helpers for archived session edit dialog."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from difra.gui.qt_compat import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    Qt,
)
from difra.gui.matador_runtime_context import (
    get_runtime_matador_context,
    set_runtime_matador_context,
)
from difra.gui.matador_upload_api import refresh_matador_reference_cache

_SELECT_PROJECT_LABEL = "Select project"
_SELECT_STUDY_LABEL = "Select study"


class ArchiveSessionEditMatadorMixin:
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

    def _set_matador_loading_state(self, loading: bool, message: str) -> None:
        self._set_matador_status(message)
        if loading:
            self.matador_progress_bar.show()
            QApplication.setOverrideCursor(Qt.WaitCursor)
        else:
            self.matador_progress_bar.hide()
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        QApplication.processEvents()

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

        self._set_matador_loading_state(
            False,
            "Matador token is required. Enter token and URL to load Project/Study choices.",
        )
        context = self._prompt_for_matador_runtime_context()
        if not context:
            self._set_reference_controls_enabled(False)
            self._set_matador_loading_state(
                False,
                "Matador token and URL are required to load Project/Study choices."
            )
            return

        self._refresh_matador_references()

    def _apply_matador_reference_payload(
        self,
        payload: Dict[str, Any],
        *,
        source_label: str,
    ) -> None:
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
            self._set_matador_loading_state(
                False,
                f"{source}. Choose a project, then choose the replacement study."
            )
            self._references_loaded_from_matador = True
            self._set_reference_controls_enabled(True)
        else:
            self._references_loaded_from_matador = False
            self._set_reference_controls_enabled(False)
            self._set_matador_loading_state(
                False,
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
            self._set_matador_loading_state(
                False,
                "Matador token is required. Enter token and URL to continue.",
            )
            context = self._prompt_for_matador_runtime_context()
            if not context:
                self._references_loaded_from_matador = False
                self._set_reference_controls_enabled(False)
                self._set_matador_loading_state(
                    False,
                    "Matador token and URL are required to load Project/Study choices."
                )
                return False

        self._references_loaded_from_matador = False
        self._set_reference_controls_enabled(False)
        self._set_matador_loading_state(
            True,
            "Connecting to Matador and downloading Project/Study list...",
        )
        try:
            payload = refresh_matador_reference_cache(
                base_url=context.get("matador_url") or "",
                token=context.get("token") or "",
                cache_path=self._matador_cache_path,
            )
        except Exception as exc:
            self._references_loaded_from_matador = False
            self._set_reference_controls_enabled(False)
            self._set_matador_loading_state(False, f"Matador refresh failed: {exc}")
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
