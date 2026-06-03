import logging
from pathlib import Path

from difra.gui.qt_compat import QMessageBox

logger = logging.getLogger(__name__)


class SessionRuntimeStateMixin:
    """Active-session runtime paths, locks, and replacement policy."""

    def _default_session_distance_cm(self):
        active_path = str(
            getattr(self, "_active_technical_container_path", "") or ""
        ).strip()
        if active_path:
            try:
                import h5py

                with h5py.File(active_path, "r") as h5f:
                    distance = h5f.attrs.get("distance_cm")
                    if distance is not None:
                        return float(distance)
            except Exception as exc:
                logger.debug(
                    "Failed to read active technical distance: %s",
                    exc,
                    exc_info=True,
                )
        try:
            distances = getattr(self, "_detector_distances", {}) or {}
            if distances:
                return float(next(iter(distances.values())))
        except Exception as exc:
            logger.debug(
                "Failed to read default session distance: %s",
                exc,
                exc_info=True,
            )
        return None

    def _current_measurement_output_folder(self) -> Path:
        if (
            hasattr(self, "session_manager")
            and self.session_manager is not None
            and self.session_manager.is_session_active()
        ):
            session_path = getattr(self.session_manager, "session_path", None)
            if session_path:
                try:
                    session_parent = Path(session_path).parent
                    if Path(session_path).exists():
                        return session_parent
                except Exception as exc:
                    logger.debug(
                        "Failed to resolve active session parent folder: %s",
                        exc,
                        exc_info=True,
                    )

        if hasattr(self, "folderLineEdit") and self.folderLineEdit is not None:
            folder_text = str(self.folderLineEdit.text() or "").strip()
            if folder_text:
                return Path(folder_text)

        return self.get_session_folder()

    def _is_measurement_output_folder_locked(self) -> bool:
        if not hasattr(self, "session_manager") or self.session_manager is None:
            return False
        if not self.session_manager.is_session_active():
            return False
        session_path = getattr(self.session_manager, "session_path", None)
        if not session_path:
            return False
        try:
            return Path(session_path).exists()
        except Exception as exc:
            logger.debug(
                "Failed to validate active session path existence: %s",
                exc,
                exc_info=True,
            )
            return False

    def _refresh_measurement_output_folder_lock(self):
        locked_folder = ""
        if self._is_measurement_output_folder_locked():
            try:
                locked_folder = str(Path(self.session_manager.session_path).parent)
            except Exception as exc:
                logger.debug(
                    "Failed to resolve locked session folder path: %s",
                    exc,
                    exc_info=True,
                )
                locked_folder = ""

        self._measurement_output_folder_locked_path = locked_folder

        if hasattr(self, "folderLineEdit") and self.folderLineEdit is not None:
            if locked_folder:
                self.folderLineEdit.setText(locked_folder)
            try:
                self.folderLineEdit.setReadOnly(bool(locked_folder))
            except Exception as exc:
                logger.debug(
                    "Failed to toggle folderLineEdit readonly state: %s",
                    exc,
                    exc_info=True,
                )
            try:
                self.folderLineEdit.setToolTip(
                    "Locked to the active session container folder."
                    if locked_folder
                    else "Measurement output folder for the current session workflow."
                )
            except Exception as exc:
                logger.debug(
                    "Failed to update folderLineEdit tooltip: %s",
                    exc,
                    exc_info=True,
                )

        if hasattr(self, "browseBtn") and self.browseBtn is not None:
            self.browseBtn.setEnabled(not bool(locked_folder))
            try:
                self.browseBtn.setToolTip(
                    "Cannot change folder while an active session container exists."
                    if locked_folder
                    else "Browse for measurement output folder."
                )
            except Exception as exc:
                logger.debug(
                    "Failed to update browseBtn tooltip: %s",
                    exc,
                    exc_info=True,
                )

    def _enforce_measurement_output_folder_lock(self, show_message: bool = False) -> bool:
        if not self._is_measurement_output_folder_locked():
            return True

        locked_folder = str(
            getattr(self, "_measurement_output_folder_locked_path", "") or ""
        ).strip()
        if not locked_folder:
            self._refresh_measurement_output_folder_lock()
            locked_folder = str(
                getattr(self, "_measurement_output_folder_locked_path", "") or ""
            ).strip()

        if hasattr(self, "folderLineEdit") and self.folderLineEdit is not None:
            current_folder = str(self.folderLineEdit.text() or "").strip()
            if current_folder != locked_folder:
                self.folderLineEdit.setText(locked_folder)
                if show_message:
                    QMessageBox.information(
                        self,
                        "Measurement Folder Locked",
                        "Measurement output folder is locked to the active session container.\n\n"
                        f"Folder: {locked_folder}",
                    )
        return True

    def _is_active_session_loaded_from_archive(self) -> bool:
        if not hasattr(self, "session_manager") or self.session_manager is None:
            return False
        if not self.session_manager.is_session_active():
            return False

        session_path = getattr(self.session_manager, "session_path", None)
        if not session_path:
            return False

        try:
            from difra.gui.session_lifecycle_service import SessionLifecycleService

            measurements_folder = None
            get_session_folder = getattr(self, "get_session_folder", None)
            if callable(get_session_folder):
                measurements_folder = get_session_folder()

            archive_root = SessionLifecycleService.resolve_archive_folder(
                config=self.config if hasattr(self, "config") else None,
                measurements_folder=measurements_folder,
            )
            archive_root = Path(archive_root).resolve()
            session_resolved = Path(session_path).resolve()
            return (
                archive_root == session_resolved
                or archive_root in session_resolved.parents
            )
        except Exception as exc:
            logger.debug(
                "Failed to determine whether active session came from archive: %s",
                exc,
                exc_info=True,
            )
            return False

    def _can_replace_active_session_for_new_session(self) -> bool:
        if not hasattr(self, "session_manager") or self.session_manager is None:
            return False
        if not self.session_manager.is_session_active():
            return False

        is_locked = getattr(self.session_manager, "is_locked", None)
        if not callable(is_locked):
            return False

        try:
            return bool(
                is_locked()
            ) and SessionRuntimeStateMixin._is_active_session_loaded_from_archive(self)
        except Exception as exc:
            logger.debug(
                "Failed to evaluate active-session replacement policy: %s",
                exc,
                exc_info=True,
            )
            return False
