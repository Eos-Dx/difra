"""Archive and finalize actions for the session tab."""

from pathlib import Path
from typing import List

from difra.gui.qt_compat import QMessageBox
from difra.gui.session_finalize_workflow import SessionFinalizeWorkflow
from difra.gui.session_lifecycle_actions import SessionLifecycleActions
from difra.gui.session_tab_presenter import SessionTabPresenter
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionTabArchiveWorkflowMixin:
    def _archive_sessions(self, container_paths: List[Path]):
        if not container_paths:
            QMessageBox.information(
                self, "No Containers", "No session containers selected."
            )
            return

        container_manager = self._container_manager()
        archive_folder = self._get_session_archive_folder()
        archive_folder.mkdir(parents=True, exist_ok=True)

        active_session_path = None
        if (
            hasattr(self, "session_manager")
            and self.session_manager
            and getattr(self.session_manager, "session_path", None)
        ):
            active_session_path = Path(self.session_manager.session_path)
        batch_session_ids = {}
        for container_path in container_paths:
            if not Path(container_path).exists():
                continue
            batch_session_ids[str(Path(container_path))] = Path(container_path).stem

        lock_user = None
        if hasattr(self, "session_manager") and self.session_manager:
            lock_user = getattr(self.session_manager, "operator_id", None)
        operator_id = None
        if hasattr(self, "operator_manager") and self.operator_manager:
            get_current_operator_id = getattr(
                self.operator_manager, "get_current_operator_id", None
            )
            if callable(get_current_operator_id):
                operator_id = get_current_operator_id()

        workflow_result = SessionLifecycleActions.archive_session_containers(
            container_paths=container_paths,
            container_manager=container_manager,
            archive_folder=archive_folder,
            config=self.config if hasattr(self, "config") else None,
            active_session_path=active_session_path,
            lock_user=lock_user,
            uploader_id=operator_id,
            session_ids=batch_session_ids,
        )

        if workflow_result.archived_active_session and hasattr(self, "session_manager"):
            self.session_manager.close_session()

        summary = [
            f"Archived {workflow_result.moved} session container(s).",
            f"Ready to send later: {workflow_result.archived_complete}",
            f"Marked NOT_COMPLETE: {workflow_result.archived_not_complete}",
            f"Cleaned measurement artifacts: {workflow_result.cleaned_artifacts}",
        ]
        if workflow_result.failed:
            summary.append("")
            summary.append("Details:")
            summary.extend(workflow_result.failed[:8])
        QMessageBox.information(self, "Session Archived", "\n".join(summary))
        self._refresh_session_container_lists()
        if hasattr(self, "update_session_status"):
            self.update_session_status()

    def _on_close_selected_sessions(self):
        self._on_close_pending_session()

    def _on_close_pending_session(self):
        container_path = self._selected_pending_container()
        if container_path is None:
            QMessageBox.warning(
                self,
                "No Container Selected",
                "Select a session container from the queue.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Close",
            (
                f"Close and archive session container '{container_path.name}'?\n\n"
                "Complete containers will be archived as UNSENT.\n"
                "Incomplete containers will be archived as NOT_COMPLETE and blocked from Matador send."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._archive_sessions([container_path])

    def _on_close_all_sessions(self):
        all_containers = self._all_pending_containers()
        if not all_containers:
            QMessageBox.information(
                self,
                "Queue Empty",
                "No session containers found in measurements folder.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Close All",
            (
                f"Close and archive ALL {len(all_containers)} queued session container(s)?\n\n"
                "Complete containers will be archived as UNSENT.\n"
                "Incomplete containers will be archived as NOT_COMPLETE and blocked from Matador send."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._archive_sessions(all_containers)

    def _update_session_tab_info(self):
        """Update active-session info and button states."""
        if not hasattr(self, "session_manager") or not hasattr(
            self, "session_info_label"
        ):
            return

        info = self.session_manager.get_session_info()
        view_state = SessionTabPresenter.build_active_session_view_state(info)
        self.session_info_label.setText(view_state.info_text)

        self._refresh_session_container_lists()
        self._update_preview_session_data_enabled()

    def _on_close_finalize_session(self):
        """Close and finalize the active session container and archive measurement files."""
        if (
            not hasattr(self, "session_manager")
            or not self.session_manager.is_session_active()
        ):
            QMessageBox.warning(
                self, "No Active Session", "No session is currently active."
            )
            return

        info = self.session_manager.get_session_info()
        reply = QMessageBox.question(
            self,
            "Close and Finalize Session?",
            f"Close and finalize session '{info['sample_id']}'?\n\n"
            f"This will:\n"
            f"• Lock the session container (read-only)\n"
            f"• Archive measurement files\n"
            f"• Close the active session\n\n"
            f"This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        try:
            session_path = Path(info["session_path"])
            measurements_folder = session_path.parent

            lock_user = getattr(self.session_manager, "operator_id", None)
            workflow_result = SessionFinalizeWorkflow.finalize_session(
                session_path=session_path,
                measurements_folder=measurements_folder,
                sample_id=info["sample_id"],
                container_manager=self._container_manager(),
                lock_user=lock_user,
                config=self.config if hasattr(self, "config") else None,
                logger=logger,
            )

            self.session_manager.close_session()

            details = [
                f"Session '{info['sample_id']}' has been finalized.",
                "",
                f"Container: {session_path.name}",
                f"Archived files: {workflow_result.archived_count}",
                f"Archive folder: {workflow_result.archive_dest}",
            ]
            if workflow_result.bundle_path:
                details.append(f"ZIP bundle: {workflow_result.bundle_path}")
            if workflow_result.old_format_dir:
                details.append(f"Old-format folder: {workflow_result.old_format_dir}")
            if workflow_result.old_format_error:
                details.append(
                    f"Old-format export warning: {workflow_result.old_format_error}"
                )

            QMessageBox.information(self, "Session Finalized", "\n".join(details))
            logger.info("Session finalized and closed", sample_id=info["sample_id"])

            self._update_session_tab_info()
            if hasattr(self, "update_session_status"):
                self.update_session_status()

        except Exception as exc:
            QMessageBox.critical(
                self,
                "Finalization Failed",
                f"Failed to finalize session:\n\n{str(exc)}",
            )
            logger.error(f"Failed to finalize session: {exc}", exc_info=True)

    def _on_upload_session(self):
        """Matador upload action for currently active session."""
        if (
            not hasattr(self, "session_manager")
            or not self.session_manager.is_session_active()
        ):
            QMessageBox.warning(
                self, "No Active Session", "No session is currently active."
            )
            return

        info = self.session_manager.get_session_info()
        if not info["is_locked"]:
            QMessageBox.warning(
                self,
                "Session Not Finalized",
                "Session must be closed and finalized before uploading.",
            )
            return

        QMessageBox.information(
            self,
            "Upload to Matador",
            f"Matador upload is executed from the Session send queue for '{info['sample_id']}'.\n\n"
            f"Use 'Close and Send' in the queue for archival transfer.",
        )
        logger.info(
            "Matador upload requested from session queue", sample_id=info["sample_id"]
        )
