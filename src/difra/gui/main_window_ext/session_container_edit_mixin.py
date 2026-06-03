import hashlib
import hmac
import logging
from pathlib import Path

from difra.gui.qt_compat import QDialog, QInputDialog, QLineEdit, QMessageBox
from difra.gui.main_window_ext.session_container_info_dialog import (
    SessionContainerInfoDialog,
)

logger = logging.getLogger(__name__)

_CONTAINER_INFORMATION_EDIT_PASSWORD_HASH = (
    "a3a6d0c20599d2b39da055a13bd4fa3ef70054cec584fc4ed1c46c4feab2a747"
)


def _verify_container_information_edit_password(password: str) -> bool:
    provided = hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()
    return hmac.compare_digest(provided, _CONTAINER_INFORMATION_EDIT_PASSWORD_HASH)


class SessionContainerEditMixin:
    """Password-gated active-session metadata correction workflow."""

    def _active_session_container_information(self) -> dict:
        if not self.session_manager.is_session_active():
            return {}

        schema = self.session_manager.schema
        info = {
            "specimenId": self.session_manager.sample_id or "",
            "study_name": self.session_manager.study_name or "",
            "project_id": "",
            "operator_id": self.session_manager.operator_id or "",
            "matadorProjectId": "",
            "matadorProjectName": "",
            "matadorStudyId": "",
            "matadorMachineId": "",
        }
        session_path = Path(self.session_manager.session_path)
        try:
            import h5py

            with h5py.File(session_path, "r") as h5f:
                info["specimenId"] = self._decode_attr(
                    h5f.attrs.get(
                        "specimenId", h5f.attrs.get(schema.ATTR_SAMPLE_ID, "")
                    )
                )
                info["study_name"] = self._decode_attr(
                    h5f.attrs.get(schema.ATTR_STUDY_NAME, "")
                )
                if hasattr(schema, "ATTR_PROJECT_ID"):
                    info["project_id"] = self._decode_attr(
                        h5f.attrs.get(schema.ATTR_PROJECT_ID, "")
                    )
                info["operator_id"] = self._decode_attr(
                    h5f.attrs.get(schema.ATTR_OPERATOR_ID, info["operator_id"])
                )
                for key in (
                    "matadorProjectId",
                    "matadorProjectName",
                    "matadorStudyId",
                    "matadorMachineId",
                ):
                    info[key] = self._decode_attr(h5f.attrs.get(key, ""))
        except Exception as exc:
            logger.warning(
                "Failed to read active session container information: %s",
                exc,
                exc_info=True,
            )
        return info

    def _confirm_container_information_edit_allowed(self) -> tuple[bool, bool]:
        has_measurements = False
        try:
            has_measurements = bool(self.session_manager.has_point_measurements())
        except Exception as exc:
            logger.warning(
                "Failed to check measurement state before metadata edit: %s",
                exc,
                exc_info=True,
            )
            has_measurements = True

        if not has_measurements:
            return True, False

        password, accepted = QInputDialog.getText(
            self,
            "Password Required",
            "Measurements have already started. Enter password to edit container information:",
            QLineEdit.Password,
        )
        if not accepted:
            return False, False
        if _verify_container_information_edit_password(str(password or "")):
            return True, True

        QMessageBox.warning(
            self,
            "Wrong Password",
            "Container information was not changed.",
        )
        self._append_session_log("Container information edit blocked: wrong password")
        return False, False

    def on_edit_session_container_information(self):
        self._append_session_log("Container information edit requested")
        if not self.session_manager.is_session_active():
            QMessageBox.warning(
                self,
                "No Session Open",
                "Open or create a session container first.",
            )
            return

        if self.session_manager.is_locked():
            QMessageBox.warning(
                self,
                "Session Locked",
                "Locked/finalized session containers cannot be edited.",
            )
            self._append_session_log(
                "Container information edit blocked: session locked"
            )
            return

        initial_info = self._active_session_container_information()
        old_specimen_id = str(initial_info.get("specimenId") or "").strip()
        allowed, password_authorized = self._confirm_container_information_edit_allowed()
        if not allowed:
            return

        dialog = SessionContainerInfoDialog(
            operator_manager=self.operator_manager,
            initial=initial_info,
            parent=self,
        )
        if dialog.exec_() != QDialog.Accepted:
            return

        params = dialog.get_parameters()
        updated = self.session_manager.update_container_information(
            **params,
            password_authorized=password_authorized,
        )
        if not updated:
            QMessageBox.warning(
                self,
                "Update Failed",
                "Container information was not updated.",
            )
            self._append_session_log("Container information update failed")
            return

        if hasattr(self, "fileNameLineEdit"):
            self.fileNameLineEdit.setText(params["specimen_id"])
        if hasattr(self, "update_session_status"):
            self.update_session_status()
        QMessageBox.information(
            self,
            "Container Information Updated",
            "Container information updated successfully.",
        )
        new_specimen_id = str(params["specimen_id"] or "").strip()
        if old_specimen_id != new_specimen_id:
            password_note = " under password" if password_authorized else ""
            self._append_session_log(
                "Specimen ID changed"
                f"{password_note}: {old_specimen_id or '<empty>'} -> {new_specimen_id}"
            )
        else:
            self._append_session_log(
                f"Updated container information for {new_specimen_id}"
            )
