"""Matador reference-cache helpers for the new-session dialog."""

from __future__ import annotations

from difra.gui.qt_compat import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
)
from difra.gui.matador_runtime_context import (
    get_runtime_matador_context,
    set_runtime_matador_context,
)
from difra.gui.matador_upload_api import (
    load_matador_reference_cache,
    refresh_matador_reference_cache,
)

_MANUAL_REFERENCE_LABEL = "Manual entry / offline"


class NewSessionDialogMatadorMixin:
    def _set_matador_status(self, message: str) -> None:
        self.matador_status_label.setText(str(message or "").strip())

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
            self._selected_matador_project_name = (
                self.project_id_edit.text().strip() or self.study_name_edit.text().strip()
            )
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
        if project_name:
            self.project_id_edit.setText(project_name)
            self._last_auto_project_name = project_name
            self._selected_matador_project_name = project_name
        elif study_name:
            self.project_id_edit.setText(study_name)
            self._last_auto_project_name = study_name
            self._selected_matador_project_name = study_name

        project_id = study.get("projectId")
        if project_id in (None, ""):
            self._selected_matador_project_id = None
        else:
            try:
                self._selected_matador_project_id = int(project_id)
            except Exception:
                self._selected_matador_project_id = None

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
                base_url=context.get("matador_url") or "",
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
                base_url=context.get("matador_url") or "",
                token=context.get("token") or "",
                cache_path=self._matador_cache_path,
            )
        except Exception as exc:
            self._set_matador_status(f"Matador auto-refresh skipped: {exc}")
            return
        self._apply_matador_reference_payload(payload)
