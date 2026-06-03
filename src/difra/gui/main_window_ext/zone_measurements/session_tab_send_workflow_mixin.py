"""Session management tab for Zone Measurements."""

from pathlib import Path
from typing import List

from difra.gui.qt_compat import (
    QApplication,
    QMessageBox,
)

from difra.gui.session_finalize_workflow import SessionFinalizeWorkflow
from difra.gui.session_lifecycle_actions import SessionLifecycleActions
from difra.gui.session_tab_presenter import SessionTabPresenter
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionTabSendWorkflowMixin:
    """Session tab behavior split from SessionTabMixin."""

    def _send_and_archive_sessions(self, container_paths: List[Path]):
        if not container_paths:
            QMessageBox.information(
                self, "No Containers", "No session containers selected."
            )
            return

        schema = self._container_schema()
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
        blocked = []

        for container_path in container_paths:
            if not Path(container_path).exists():
                continue
            info = SessionTabPresenter.read_session_container_metadata(
                Path(container_path),
                schema=schema,
                container_manager=container_manager,
            )
            logger.info(
                "Queued session for Matador upload",
                session_path=str(container_path),
                sample_id=info.get("sample_id"),
            )
            batch_session_ids[str(Path(container_path))] = (
                info.get("session_id") or Path(container_path).stem
            )
            if str(info.get("transfer_status") or "").strip().upper() == "NOT_COMPLETE":
                blocked.append(Path(container_path).name)

        if blocked:
            QMessageBox.warning(
                self,
                "Send Blocked",
                "The following session container(s) are marked NOT_COMPLETE and "
                "cannot be sent to Matador:\n\n" + "\n".join(blocked),
            )
            return

        lock_user = None
        if hasattr(self, "session_manager") and self.session_manager:
            lock_user = getattr(self.session_manager, "operator_id", None)
        uploader_id = None
        if hasattr(self, "operator_manager") and self.operator_manager:
            get_current_operator_id = getattr(
                self.operator_manager, "get_current_operator_id", None
            )
            if callable(get_current_operator_id):
                uploader_id = get_current_operator_id()
        if (
            not uploader_id
            and hasattr(self, "config")
            and isinstance(self.config, dict)
        ):
            uploader_id = self.config.get("operator_id")
        upload_context = self._request_upload_login_context(
            fallback_operator=str(uploader_id or lock_user or "unknown")
        )
        if upload_context is None:
            QMessageBox.information(
                self, "Upload Cancelled", "Upload was cancelled by operator."
            )
            return
        uploader_id = str(
            upload_context.get("uploader_id") or uploader_id or lock_user or "unknown"
        )
        runtime_config = dict(
            self.config
            if hasattr(self, "config") and isinstance(self.config, dict)
            else {}
        )
        runtime_config["operator_id"] = uploader_id
        runtime_config.setdefault("matador_upload_max_parallel", 4)
        runtime_config.setdefault("matador_async_verification_batch_size", 4)
        runtime_config["matador_token"] = str(
            upload_context.get("token") or runtime_config.get("matador_token") or ""
        )
        runtime_config["matador_url"] = str(
            upload_context.get("matador_url") or runtime_config.get("matador_url") or ""
        )
        simulate_upload_failure = False
        simulate_upload_failure = bool(
            runtime_config.get("upload_stub_force_failure", False)
        )
        specimen_overrides = self._collect_matador_specimen_overrides(
            container_paths=container_paths,
            runtime_config=runtime_config,
            uploader_id=uploader_id,
        )
        if specimen_overrides is None:
            QMessageBox.information(
                self, "Upload Cancelled", "Upload was cancelled by operator."
            )
            return

        progress_dialog, progress_label, progress_bar, progress_log, close_button = (
            self._create_matador_send_progress_dialog(len(container_paths))
        )
        progress_dialog.show()

        log_lines: List[str] = []
        per_container_status = {}

        def _progress_update(event):
            if not isinstance(event, dict):
                return
            message = str(event.get("message") or "").strip()
            current = int(event.get("current") or 0)
            total = int(event.get("total") or max(len(container_paths), 1))
            kind = str(event.get("kind") or "").strip()
            container_name = Path(str(event.get("container_path") or "")).name

            if message and hasattr(self, "_append_session_log"):
                self._append_session_log(message)
            if message:
                log_lines.append(message)
                progress_log.appendPlainText(message)

            if kind in {"container_done", "container_failed"} and container_name:
                per_container_status[container_name] = message

            progress_bar.setMaximum(max(total, 1))
            display_value = current
            if kind not in {"container_done", "container_failed"} and current > 0:
                display_value = current - 1
            progress_bar.setValue(max(0, min(display_value, max(total, 1))))
            progress_label.setText(
                message or "Sending session containers to Matador..."
            )
            QApplication.processEvents()

        workflow_result = None
        try:
            workflow_result = (
                SessionLifecycleActions.send_and_archive_session_containers(
                    container_paths=container_paths,
                    container_manager=container_manager,
                    archive_folder=archive_folder,
                    active_session_path=active_session_path,
                    lock_user=lock_user,
                    uploader_id=uploader_id,
                    upload_session_id=None,
                    simulate_upload_failure=simulate_upload_failure,
                    session_ids=batch_session_ids,
                    config=runtime_config,
                    progress_callback=_progress_update,
                    specimen_overrides=specimen_overrides,
                )
            )
        finally:
            QApplication.processEvents()

        progress_bar.setValue(max(len(container_paths), 1))
        if workflow_result.archived_active_session and hasattr(self, "session_manager"):
            self.session_manager.close_session()

        summary = [f"Sent+archived {workflow_result.moved} session container(s)."]
        if workflow_result.upload_session_id:
            summary.append(f"Upload session: {workflow_result.upload_session_id}")
        summary.append(
            "Upload result: "
            f"{workflow_result.upload_success} success / "
            f"{getattr(workflow_result, 'upload_pending', 0)} pending / "
            f"{workflow_result.upload_failed} failed"
        )
        summary.append(
            f"Cleaned measurement artifacts: {workflow_result.cleaned_artifacts}"
        )
        summary.append(f"Old-format exports: {len(workflow_result.old_format_paths)}")
        if workflow_result.old_format_paths:
            summary.append(f"Old-format folder: {workflow_result.old_format_paths[-1]}")
        if workflow_result.failed:
            summary.append("")
            summary.append("Failures:")
            summary.extend(workflow_result.failed[:8])
            if len(workflow_result.failed) > 8:
                summary.append(f"... and {len(workflow_result.failed) - 8} more")
        if workflow_result.old_format_failed:
            summary.append("")
            summary.append("Old-format export failures:")
            summary.extend(workflow_result.old_format_failed[:8])
            if len(workflow_result.old_format_failed) > 8:
                summary.append(
                    f"... and {len(workflow_result.old_format_failed) - 8} more"
                )

        if per_container_status:
            summary.append("")
            summary.append("Per-container result:")
            for container_name in sorted(per_container_status.keys()):
                summary.append(per_container_status[container_name])

        log_path = self._write_matador_send_log(
            runtime_config=runtime_config,
            log_lines=log_lines + summary,
            workflow_result=workflow_result,
        )
        summary.append("")
        summary.append(f"Matador log saved to: {log_path}")

        if workflow_result.upload_failed > 0 and hasattr(self, "_append_session_log"):
            self._append_session_log(f"Matador send log saved: {log_path}")
        report_status = self._send_matador_upload_error_report(
            runtime_config=runtime_config,
            workflow_result=workflow_result,
            log_path=log_path,
            context="send-and-archive",
        )
        if report_status:
            summary.append(report_status)
            if hasattr(self, "_append_session_log"):
                self._append_session_log(report_status)
        if getattr(workflow_result, "upload_pending", 0) > 0:
            self._schedule_matador_pending_verification(
                container_paths=list(workflow_result.archived_paths),
                runtime_config=runtime_config,
            )

        progress_log.appendPlainText("")
        for line in summary:
            progress_log.appendPlainText(line)
        progress_label.setText(
            "Matador send finished with failures."
            if workflow_result.upload_failed > 0
            else "Matador send uploaded files; verification pending."
            if getattr(workflow_result, "upload_pending", 0) > 0
            else "Matador send finished successfully."
        )
        close_button.setEnabled(True)
        QApplication.processEvents()

        self._refresh_session_container_lists()
        if hasattr(self, "update_session_status"):
            self.update_session_status()

    def _send_archived_sessions(self, container_paths: List[Path]):
        if not container_paths:
            QMessageBox.information(
                self, "No Containers", "No archived session containers selected."
            )
            return

        container_manager = self._container_manager()
        blocked = []
        for container_path in container_paths:
            transfer_status = SessionLifecycleActions._current_transfer_status(
                Path(container_path),
                container_manager=container_manager,
            )
            if transfer_status == SessionLifecycleActions.TRANSFER_STATUS_NOT_COMPLETE:
                blocked.append(Path(container_path).name)
        if blocked:
            QMessageBox.warning(
                self,
                "Send Blocked",
                "The following archived container(s) are marked NOT_COMPLETE and "
                "cannot be sent to Matador:\n\n" + "\n".join(blocked),
            )
            return

        lock_user = None
        if hasattr(self, "session_manager") and self.session_manager:
            lock_user = getattr(self.session_manager, "operator_id", None)
        uploader_id = None
        if hasattr(self, "operator_manager") and self.operator_manager:
            get_current_operator_id = getattr(
                self.operator_manager, "get_current_operator_id", None
            )
            if callable(get_current_operator_id):
                uploader_id = get_current_operator_id()
        if (
            not uploader_id
            and hasattr(self, "config")
            and isinstance(self.config, dict)
        ):
            uploader_id = self.config.get("operator_id")

        upload_context = self._request_upload_login_context(
            fallback_operator=str(uploader_id or lock_user or "unknown")
        )
        if upload_context is None:
            QMessageBox.information(
                self, "Upload Cancelled", "Upload was cancelled by operator."
            )
            return

        uploader_id = str(
            upload_context.get("uploader_id") or uploader_id or lock_user or "unknown"
        )
        runtime_config = dict(
            self.config
            if hasattr(self, "config") and isinstance(self.config, dict)
            else {}
        )
        runtime_config["operator_id"] = uploader_id
        runtime_config.setdefault("matador_upload_max_parallel", 4)
        runtime_config.setdefault("matador_async_verification_batch_size", 4)
        runtime_config["matador_token"] = str(
            upload_context.get("token") or runtime_config.get("matador_token") or ""
        )
        runtime_config["matador_url"] = str(
            upload_context.get("matador_url") or runtime_config.get("matador_url") or ""
        )
        simulate_upload_failure = bool(
            runtime_config.get("upload_stub_force_failure", False)
        )
        specimen_overrides = self._collect_matador_specimen_overrides(
            container_paths=container_paths,
            runtime_config=runtime_config,
            uploader_id=uploader_id,
        )
        if specimen_overrides is None:
            QMessageBox.information(
                self, "Upload Cancelled", "Upload was cancelled by operator."
            )
            return

        progress_dialog, progress_label, progress_bar, progress_log, close_button = (
            self._create_matador_send_progress_dialog(len(container_paths))
        )
        progress_dialog.show()

        log_lines: List[str] = []
        per_container_status = {}

        def _progress_update(event):
            if not isinstance(event, dict):
                return
            message = str(event.get("message") or "").strip()
            current = int(event.get("current") or 0)
            total = int(event.get("total") or max(len(container_paths), 1))
            kind = str(event.get("kind") or "").strip()
            container_name = Path(str(event.get("container_path") or "")).name

            if message and hasattr(self, "_append_session_log"):
                self._append_session_log(message)
            if message:
                log_lines.append(message)
                progress_log.appendPlainText(message)

            if kind in {"container_done", "container_failed"} and container_name:
                per_container_status[container_name] = message

            progress_bar.setMaximum(max(total, 1))
            display_value = current
            if kind not in {"container_done", "container_failed"} and current > 0:
                display_value = current - 1
            progress_bar.setValue(max(0, min(display_value, max(total, 1))))
            progress_label.setText(
                message or "Sending archived session containers to Matador..."
            )
            QApplication.processEvents()

        workflow_result = SessionLifecycleActions.reupload_archived_session_containers(
            container_paths=container_paths,
            container_manager=container_manager,
            uploader_id=uploader_id,
            lock_user=lock_user,
            simulate_upload_failure=simulate_upload_failure,
            config=runtime_config,
            progress_callback=_progress_update,
            specimen_overrides=specimen_overrides,
        )

        progress_bar.setValue(max(len(container_paths), 1))
        summary = [
            f"Processed {len(container_paths)} archived session container(s).",
            "Upload result: "
            f"{workflow_result.upload_success} success / "
            f"{getattr(workflow_result, 'upload_pending', 0)} pending / "
            f"{workflow_result.upload_failed} failed",
        ]
        if workflow_result.upload_session_id:
            summary.append(f"Upload session: {workflow_result.upload_session_id}")
        if workflow_result.old_format_paths:
            summary.append(f"Old-format folder: {workflow_result.old_format_paths[-1]}")
        if workflow_result.failed:
            summary.append("")
            summary.append("Failures:")
            summary.extend(workflow_result.failed[:8])
            if len(workflow_result.failed) > 8:
                summary.append(f"... and {len(workflow_result.failed) - 8} more")
        if per_container_status:
            summary.append("")
            summary.append("Per-container result:")
            for container_name in sorted(per_container_status.keys()):
                summary.append(per_container_status[container_name])

        log_path = self._write_matador_send_log(
            runtime_config=runtime_config,
            log_lines=log_lines + summary,
            workflow_result=workflow_result,
        )
        summary.append("")
        summary.append(f"Matador log saved to: {log_path}")
        report_status = self._send_matador_upload_error_report(
            runtime_config=runtime_config,
            workflow_result=workflow_result,
            log_path=log_path,
            context="archived-resend",
        )
        if report_status:
            summary.append(report_status)
            if hasattr(self, "_append_session_log"):
                self._append_session_log(report_status)
        if getattr(workflow_result, "upload_pending", 0) > 0:
            self._schedule_matador_pending_verification(
                container_paths=list(workflow_result.archived_paths),
                runtime_config=runtime_config,
            )

        progress_log.appendPlainText("")
        for line in summary:
            progress_log.appendPlainText(line)
        progress_label.setText(
            "Matador resend finished with failures."
            if workflow_result.upload_failed > 0
            else "Matador resend uploaded files; verification pending."
            if getattr(workflow_result, "upload_pending", 0) > 0
            else "Matador resend finished successfully."
        )
        close_button.setEnabled(True)
        QApplication.processEvents()

        self._refresh_session_container_lists()
        if hasattr(self, "update_session_status"):
            self.update_session_status()

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

    def _on_send_selected_sessions(self):
        self._on_send_pending_session()

    def _on_send_pending_session(self):
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
            "Close and Send",
            (
                f"Close, upload, and archive session container '{container_path.name}'?\n\n"
                "DIFRA will create one ZIP folder with old-format data and one H5 container for this session."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._send_and_archive_sessions([container_path])

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

    def _on_send_all_sessions(self):
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
            "Close && Send All",
            (
                f"Close, upload, and archive ALL {len(all_containers)} queued session container(s)?\n\n"
                "DIFRA will create one ZIP folder with old-format data and one H5 container per session."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._send_and_archive_sessions(all_containers)

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
