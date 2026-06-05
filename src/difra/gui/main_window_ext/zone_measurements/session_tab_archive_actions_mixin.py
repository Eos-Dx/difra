"""Session management tab for Zone Measurements."""

import hashlib
import hmac
import os
from pathlib import Path
import shutil
from typing import Dict, List, Optional

from difra.gui.qt_compat import (
    QDialog,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
)

from difra.gui.session_lifecycle_actions import SessionLifecycleActions
from difra.gui.session_old_format_exporter import SessionOldFormatExporter
from difra.gui.session_tab_presenter import SessionTabPresenter
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionTabArchiveActionsMixin:
    """Session tab behavior split from SessionTabMixin."""

    def _confirm_archive_metadata_edit_password(self) -> bool:
        if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
            return True

        password, accepted = QInputDialog.getText(
            self,
            "Edit Archived Session Metadata",
            "Enter password to edit archived Project/Study metadata:",
            QLineEdit.Password,
        )
        if not accepted:
            return False

        provided_hash = hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()
        if hmac.compare_digest(provided_hash, self.ARCHIVE_METADATA_EDIT_PASSWORD_HASH):
            return True

        QMessageBox.warning(
            self,
            "Wrong Password",
            "Password is incorrect. Archived metadata was not changed.",
        )
        return False

    def _current_operator_id_for_archive_edit(self) -> str:
        operator_manager = getattr(self, "operator_manager", None)
        if operator_manager is not None:
            getter = getattr(operator_manager, "get_current_operator_id", None)
            if callable(getter):
                try:
                    value = str(getter() or "").strip()
                except Exception:
                    value = ""
                if value:
                    return value

        session_manager = getattr(self, "session_manager", None)
        if session_manager is not None:
            value = str(getattr(session_manager, "operator_id", "") or "").strip()
            if value:
                return value

        if hasattr(self, "config") and isinstance(self.config, dict):
            value = str(self.config.get("operator_id") or "").strip()
            if value:
                return value

        return "unknown"

    def _edit_archived_sessions(self, container_paths: List[Path]):
        targets = [Path(path) for path in container_paths if Path(path).exists()]
        if not targets:
            QMessageBox.information(
                self,
                "No Containers",
                "No archived session containers selected.",
            )
            return

        if not self._confirm_archive_metadata_edit_password():
            return

        dialog = _session_tab_dependency("ArchiveSessionEditDialog")(
            container_paths=targets,
            parent=self,
        )
        if dialog.exec_() != QDialog.Accepted:
            return

        selection = dialog.get_selection()
        editor_id = self._current_operator_id_for_archive_edit()
        updated = []
        unchanged = []
        failed = []

        for container_path in targets:
            result = SessionLifecycleActions.edit_archived_session_matador_metadata(
                container_path=container_path,
                specimen_id=selection.get("specimen_id"),
                project_id=selection.get("project_id"),
                project_name=selection.get("project_name"),
                study_id=selection.get("study_id"),
                study_name=selection.get("study_name"),
                edited_by=editor_id,
                auth_mode="password",
            )
            if not result.get("success"):
                failed.append(f"{container_path.name}: {result.get('message')}")
            elif result.get("updated"):
                updated.append(container_path.name)
            else:
                unchanged.append(container_path.name)

        summary = [
            f"Specimen ID: {selection.get('specimen_id') or 'unchanged'}",
            f"Project: {selection.get('project_name')} [{selection.get('project_id')}]",
            f"Study: {selection.get('study_name')} [{selection.get('study_id')}]",
            f"Changed by: {editor_id}",
        ]
        if updated:
            summary.append("")
            summary.append(f"Updated: {len(updated)}")
        if unchanged:
            summary.append(f"Already matched: {len(unchanged)}")
        if failed:
            summary.append(f"Failed: {len(failed)}")
            summary.extend(failed[:6])

        if failed:
            QMessageBox.warning(
                self,
                "Archived Metadata Updated With Errors",
                "\n".join(summary),
            )
        else:
            QMessageBox.information(
                self,
                "Archived Metadata Updated",
                "\n".join(summary),
            )

        self._refresh_session_container_lists()
        if hasattr(self, "update_session_status"):
            self.update_session_status()

    def _open_session_container_path(self, container_path: Path):
        if container_path is None:
            return
        if not container_path.exists():
            QMessageBox.warning(
                self,
                "Container Missing",
                f"Session container not found:\n{container_path}",
            )
            return
        if hasattr(self, "load_session_container_from_path"):
            self.load_session_container_from_path(container_path)
        else:
            QMessageBox.warning(
                self,
                "Load Not Available",
                "Session loading API is not available in this window build.",
            )

    def _generate_old_format_for_container(self, container_path: Path):
        if container_path is None:
            return
        if not container_path.exists():
            QMessageBox.warning(
                self,
                "Container Missing",
                f"Session container not found:\n{container_path}",
            )
            return

        try:
            export_root = SessionOldFormatExporter.resolve_old_format_root(
                config=self.config if hasattr(self, "config") else None,
                archive_folder=self._get_session_archive_folder(),
            )
            if export_root.exists():
                shutil.rmtree(export_root)
            export_root.mkdir(parents=True, exist_ok=True)
            summary = SessionOldFormatExporter.export_from_session_container(
                container_path,
                config=self.config if hasattr(self, "config") else None,
                archive_folder=self._get_session_archive_folder(),
                target_root=export_root,
            )
            QMessageBox.information(
                self,
                "Old Format Generated",
                "\n".join(
                    [
                        f"Container: {container_path.name}",
                        f"Old-format folder: {summary.export_dir}",
                        f"Raw files exported: {summary.raw_file_count}",
                        f"Technical files exported: {summary.technical_file_count}",
                    ]
                ),
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Old Format Export Failed",
                f"Failed to generate old-format folder for:\n{container_path}\n\n{exc}",
            )

    def _request_matador_specimen_override(
        self,
        *,
        container_path: Path,
        specimen_text: str,
    ) -> Optional[int]:
        raw_specimen = str(specimen_text or "").strip()
        if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
            return None
        detail_line = (
            f"The stored specimen '{raw_specimen}' is not a valid Matador integer specimen ID."
            if raw_specimen
            else "No Matador integer specimen ID is stored in this container."
        )
        value, accepted = QInputDialog.getText(
            self,
            "Matador Specimen ID Required",
            "\n".join(
                [
                    f"Container: {container_path.name}",
                    "",
                    detail_line,
                    "Enter the numeric Matador specimen ID to use for this upload:",
                ]
            ),
            text="",
        )
        if not accepted:
            return None
        text = str(value or "").strip()
        if not text or not text.isdigit():
            QMessageBox.warning(
                self,
                "Invalid Specimen ID",
                "Matador specimen ID must be a whole number.",
            )
            return None
        return int(text)

    @staticmethod
    def _real_matador_upload_enabled(runtime_config: Dict[str, object]) -> bool:
        return bool(
            str(runtime_config.get("matador_url") or "").strip()
            and str(runtime_config.get("matador_token") or "").strip()
            and not bool(runtime_config.get("matador_force_stub", False))
        )

    def _collect_matador_specimen_overrides(
        self,
        *,
        container_paths: List[Path],
        runtime_config: Dict[str, object],
        uploader_id: str,
    ) -> Optional[Dict[str, int]]:
        if not self._real_matador_upload_enabled(runtime_config):
            return {}

        specimen_overrides: Dict[str, int] = {}
        for container_path in container_paths:
            metadata = SessionLifecycleActions._read_matador_session_metadata(
                Path(container_path),
                config=runtime_config,
                uploader_id=uploader_id,
            )
            if metadata.get("specimen_id") is not None:
                continue
            override = self._request_matador_specimen_override(
                container_path=Path(container_path),
                specimen_text=str(metadata.get("specimen_text") or ""),
            )
            if override is None:
                return None
            specimen_overrides[str(Path(container_path))] = int(override)
        return specimen_overrides

    def _show_archived_sessions_context_menu(self, pos, *, table=None):
        table = table or getattr(self, "archive_window_table", None)
        if table is None:
            return
        row = table.rowAt(pos.y())
        if row < 0:
            return
        container_path = self._path_from_table_row(table, row, 9)
        if container_path is None:
            return

        info = SessionTabPresenter.read_session_container_metadata(
            Path(container_path),
            schema=self._container_schema(),
            container_manager=self._container_manager(),
        )
        transfer_status = str(info.get("transfer_status") or "").strip().upper()
        menu = QMenu(table)
        load_action = menu.addAction("Load Container")
        edit_action = menu.addAction("Edit Project/Study")
        send_action = menu.addAction(
            "Send To Matador Again" if transfer_status == "SENT" else "Send To Matador"
        )
        if transfer_status == "NOT_COMPLETE":
            send_action.setEnabled(False)
        analyst_report_action = menu.addAction("Send Report to Analysts")
        report_action = menu.addAction("Generate Report...")
        old_format_action = menu.addAction("Generate Old Format")
        selected = menu.exec_(table.viewport().mapToGlobal(pos))
        if selected == load_action:
            self._open_session_container_path(container_path)
        elif selected == edit_action:
            self._edit_archived_sessions(
                self._selected_paths_from_archive_table(
                    table, fallback_path=container_path
                )
            )
        elif selected == send_action:
            self._send_archived_sessions(
                self._selected_paths_from_archive_table(
                    table, fallback_path=container_path
                )
            )
        elif selected == analyst_report_action:
            self._send_selected_archived_report_to_analysts(
                self._selected_paths_from_archive_table(
                    table, fallback_path=container_path
                )
            )
        elif selected == report_action:
            self._generate_selected_archived_report(
                self._selected_paths_from_archive_table(
                    table, fallback_path=container_path
                )
            )
        elif selected == old_format_action:
            self._generate_old_format_for_container(container_path)

    def _generate_selected_archived_report(self, container_paths: List[Path]):
        mode, accepted = QInputDialog.getItem(
            self,
            "Generate Report",
            "Output:",
            ["Full folder", "Overview image only"],
            0,
            False,
        )
        if not accepted:
            return
        if str(mode) == "Overview image only":
            self._generate_selected_archived_report_overview_image(container_paths)
            return
        self._generate_selected_archived_report_folder(container_paths)

    def _generate_selected_archived_report_folder(self, container_paths: List[Path]):
        targets = [Path(path) for path in container_paths if Path(path).exists()]
        if not targets:
            QMessageBox.information(
                self,
                "No Containers",
                "Select archived session container(s) to report.",
            )
            return
        runtime_config = dict(getattr(self, "config", {}) or {})
        base_folder = runtime_config.get("difra_base_folder") or Path.home() / "difra"
        default_dir = Path(base_folder) / "daily_reports" / "manual_report_folders"
        default_dir.mkdir(parents=True, exist_ok=True)
        chosen_dir = _session_tab_dependency("QFileDialog").getExistingDirectory(
            self,
            "Select Report Folder",
            str(default_dir),
        )
        if not chosen_dir:
            return
        output_dir = Path(chosen_dir)
        try:
            result = _session_tab_dependency("build_daily_report_for_containers")(
                config=runtime_config,
                container_paths=targets,
                output_dir=output_dir,
                send_email=False,
                allow_interactive_setup=False,
                create_archive=False,
            )
        except Exception as exc:
            logger.warning(
                "Failed to generate selected report folder",
                exc_info=True,
            )
            QMessageBox.warning(
                self,
                "Report Failed",
                f"Could not generate report folder:\n{exc}",
            )
            return

        summary = [
            f"Selected containers: {len(targets)}",
            f"Valid containers: {result.valid_containers}",
            f"Images: {len(result.images)}",
            f"Folder: {output_dir}",
            f"Manifest: {output_dir / 'manifest.json'}",
        ]
        if result.skipped:
            summary.append("")
            summary.append("Skipped:")
            summary.extend(result.skipped[:8])
            if len(result.skipped) > 8:
                summary.append(f"... and {len(result.skipped) - 8} more")
        QMessageBox.information(
            self,
            "Report Folder Generated",
            "\n".join(summary),
        )

    def _generate_selected_archived_report_overview_image(self, container_paths: List[Path]):
        targets = [Path(path) for path in container_paths if Path(path).exists()]
        if not targets:
            QMessageBox.information(
                self,
                "No Containers",
                "Select archived session container(s) to report.",
            )
            return
        runtime_config = dict(getattr(self, "config", {}) or {})
        base_folder = runtime_config.get("difra_base_folder") or Path.home() / "difra"
        default_dir = Path(base_folder) / "daily_reports" / "manual_report_images"
        default_dir.mkdir(parents=True, exist_ok=True)
        default_path = default_dir / "difra_report_overview.png"
        file_path, _ = _session_tab_dependency("QFileDialog").getSaveFileName(
            self,
            "Save Report Overview Image",
            str(default_path),
            "PNG Images (*.png);;All Files (*)",
        )
        if not file_path:
            return
        output_path = Path(file_path)
        if output_path.suffix.lower() != ".png":
            output_path = output_path.with_suffix(".png")
        try:
            result = _session_tab_dependency("build_report_overview_image_for_containers")(
                config=runtime_config,
                container_paths=targets,
                image_path=output_path,
            )
        except Exception as exc:
            logger.warning(
                "Failed to generate selected report overview image",
                exc_info=True,
            )
            QMessageBox.warning(
                self,
                "Report Failed",
                f"Could not generate overview image:\n{exc}",
            )
            return

        summary = [
            f"Selected containers: {len(targets)}",
            f"Valid containers: {result.valid_containers}",
            f"Image: {output_path}",
        ]
        if result.skipped:
            summary.append("")
            summary.append("Skipped:")
            summary.extend(result.skipped[:8])
            if len(result.skipped) > 8:
                summary.append(f"... and {len(result.skipped) - 8} more")
        QMessageBox.information(
            self,
            "Report Image Generated",
            "\n".join(summary),
        )

    def _send_selected_archived_report_to_analysts(self, container_paths: List[Path]):
        targets = [Path(path) for path in container_paths if Path(path).exists()]
        if not targets:
            QMessageBox.information(
                self,
                "No Containers",
                "Select archived session container(s) to report.",
            )
            return
        runtime_config = dict(getattr(self, "config", {}) or {})
        base_folder = runtime_config.get("difra_base_folder") or Path.home() / "difra"
        output_dir = Path(base_folder) / "daily_reports" / "manual_analyst_reports"
        try:
            result = _session_tab_dependency("build_daily_report_for_containers")(
                config=runtime_config,
                container_paths=targets,
                output_dir=output_dir,
                send_email=True,
                allow_interactive_setup=False,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send selected analyst report",
                exc_info=True,
            )
            QMessageBox.warning(
                self,
                "Report Failed",
                f"Could not send report to analysts:\n{exc}",
            )
            return

        email_result = result.email_result or {}
        summary = [
            f"Selected containers: {len(targets)}",
            f"Valid containers: {result.valid_containers}",
            f"Images: {len(result.images)}",
            f"ZIP: {result.zip_path}",
            f"Email: {email_result.get('message', 'not sent')}",
        ]
        if result.skipped:
            summary.append("")
            summary.append("Skipped:")
            summary.extend(result.skipped[:8])
            if len(result.skipped) > 8:
                summary.append(f"... and {len(result.skipped) - 8} more")
        if email_result.get("sent"):
            QMessageBox.information(
                self,
                "Report Sent to Analysts",
                "\n".join(summary),
            )
        else:
            QMessageBox.warning(
                self,
                "Report Not Sent",
                "\n".join(summary),
            )

    def _on_send_selected_archived_sessions(self):
        container_paths = self._selected_archived_containers()
        if not container_paths:
            QMessageBox.information(
                self,
                "No Containers",
                "Select archived session container(s) to send.",
            )
            return
        self._send_archived_sessions(container_paths)

    def _on_load_session_container_from_dialog(self):
        file_path, _ = _session_tab_dependency("QFileDialog").getOpenFileName(
            self,
            "Open Session Container",
            str(self._get_measurements_folder_for_queue()),
            "NeXus HDF5 Files (*.nxs.h5 *.h5);;All Files (*)",
        )
        if not file_path:
            return
        self._open_session_container_path(Path(file_path))

    def _on_load_selected_session_container(self):
        container_path = self._selected_pending_container()
        if container_path is None:
            self._on_load_session_container_from_dialog()
            return
        self._open_session_container_path(container_path)


def _session_tab_dependency(name: str):
    from difra.gui.main_window_ext.zone_measurements import session_tab_mixin

    return getattr(session_tab_mixin, name)
