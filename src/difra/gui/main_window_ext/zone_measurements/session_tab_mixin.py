"""Session management tab for Zone Measurements."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from difra.gui.container_api import get_container_manager, get_schema
from difra.gui.matador_runtime_context import (
    DEFAULT_MATADOR_URL,
    get_runtime_matador_context,
    set_runtime_matador_context,
)
from difra.gui.session_finalize_workflow import SessionFinalizeWorkflow
from difra.gui.session_lifecycle_actions import SessionLifecycleActions
from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.gui.session_old_format_exporter import SessionOldFormatExporter
from difra.gui.session_tab_presenter import SessionTabPresenter
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionTabMixin:
    """Mixin for session management tab in Zone Measurements."""

    def _create_matador_send_progress_dialog(self, total_containers: int):
        dialog = QDialog(self)
        dialog.setWindowTitle("Matador Send Progress")
        dialog.setModal(False)
        dialog.resize(820, 520)

        layout = QVBoxLayout(dialog)

        status_label = QLabel("Preparing Matador send workflow...")
        layout.addWidget(status_label)

        progress_bar = QProgressBar(dialog)
        progress_bar.setMinimum(0)
        progress_bar.setMaximum(max(int(total_containers), 1))
        progress_bar.setValue(0)
        layout.addWidget(progress_bar)

        log_view = QPlainTextEdit(dialog)
        log_view.setReadOnly(True)
        layout.addWidget(log_view, 1)

        close_button = QPushButton("Close", dialog)
        close_button.setEnabled(False)
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)

        setattr(self, "_matador_send_progress_dialog", dialog)
        dialog.finished.connect(
            lambda *_args: setattr(self, "_matador_send_progress_dialog", None)
        )
        return dialog, status_label, progress_bar, log_view, close_button

    def _write_matador_send_log(
        self,
        *,
        runtime_config: dict,
        log_lines: List[str],
        workflow_result,
    ) -> Path:
        logs_root = SessionLifecycleActions.resolve_matador_logs_root(config=runtime_config)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = logs_root / f"matador_send_{timestamp}.log"
        payload = {
            "createdAt": datetime.now().isoformat(timespec="seconds"),
            "uploadSessionId": str(getattr(workflow_result, "upload_session_id", "") or ""),
            "uploadSuccess": int(getattr(workflow_result, "upload_success", 0)),
            "uploadFailed": int(getattr(workflow_result, "upload_failed", 0)),
            "moved": int(getattr(workflow_result, "moved", 0)),
            "archivedPaths": [str(path) for path in getattr(workflow_result, "archived_paths", [])],
            "oldFormatPaths": [str(path) for path in getattr(workflow_result, "old_format_paths", [])],
            "failed": list(getattr(workflow_result, "failed", []) or []),
            "oldFormatFailed": list(getattr(workflow_result, "old_format_failed", []) or []),
            "logLines": list(log_lines or []),
        }
        with open(log_path, "w", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, indent=2, ensure_ascii=False)
        return log_path

    def _container_schema(self):
        return get_schema(self.config if hasattr(self, "config") else None)

    def _container_manager(self):
        return get_container_manager(self.config if hasattr(self, "config") else None)

    def _request_upload_login_context(self, fallback_operator: str):
        """Collect uploader identity and Matador token right before send."""
        runtime_context = get_runtime_matador_context(self)
        default_operator = str(fallback_operator or "unknown")
        default_url = str(runtime_context.get("matador_url") or DEFAULT_MATADOR_URL).strip()
        default_token = str(runtime_context.get("token") or "").strip()

        if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
            return {
                "uploader_id": default_operator,
                "token": default_token,
                "matador_url": default_url,
            }

        dialog = QDialog(self)
        dialog.setWindowTitle("Matador Upload")
        dialog.setModal(True)
        layout = QFormLayout(dialog)

        uploader_edit = QLineEdit(default_operator)
        layout.addRow("Operator:", uploader_edit)

        token_edit = QLineEdit(default_token)
        token_edit.setEchoMode(QLineEdit.Password)
        token_edit.setPlaceholderText("Paste JWT token from /difra-api-token")
        layout.addRow("Matador Token:", token_edit)

        url_edit = QLineEdit(default_url or DEFAULT_MATADOR_URL)
        layout.addRow("Matador URL:", url_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec_() != QDialog.Accepted:
            return None

        uploader_text = str(uploader_edit.text() or "").strip()
        token_text = str(token_edit.text() or "").strip()
        url_text = str(url_edit.text() or "").strip()
        if not uploader_text:
            QMessageBox.warning(self, "Upload Cancelled", "Operator name is required.")
            return None
        if not token_text:
            QMessageBox.warning(self, "Upload Cancelled", "Matador token is required.")
            return None
        if not url_text:
            QMessageBox.warning(self, "Upload Cancelled", "Matador URL is required.")
            return None

        set_runtime_matador_context(
            self,
            token=token_text,
            matador_url=url_text,
        )

        return {
            "uploader_id": uploader_text,
            "token": token_text,
            "matador_url": url_text,
        }

    def create_session_tab(self):
        """Create session management tab with active/pending queue."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info_group = QGroupBox("Active Session Information")
        info_layout = QVBoxLayout(info_group)
        info_btn_row = QHBoxLayout()
        self.new_session_btn = QPushButton("Create Session...")
        on_new_session = getattr(self, "on_new_session", None)
        if callable(on_new_session):
            self.new_session_btn.clicked.connect(on_new_session)
        else:
            self.new_session_btn.setEnabled(False)
        info_btn_row.addWidget(self.new_session_btn)

        self.new_technical_btn = QPushButton("Create Technical...")
        on_new_technical_container = getattr(self, "on_new_technical_container", None)
        if callable(on_new_technical_container):
            self.new_technical_btn.clicked.connect(on_new_technical_container)
        else:
            self.new_technical_btn.setEnabled(False)
        info_btn_row.addWidget(self.new_technical_btn)
        info_btn_row.addStretch()
        info_layout.addLayout(info_btn_row)

        self.session_info_label = QLabel("No active session")
        self.session_info_label.setStyleSheet("padding: 10px;")
        info_layout.addWidget(self.session_info_label)
        layout.addWidget(info_group)

        queue_group = QGroupBox("Session Containers Ready To Close/Send")
        queue_layout = QVBoxLayout(queue_group)

        queue_btn_layout = QHBoxLayout()
        queue_btn_layout.setSpacing(4)
        self.refresh_sessions_btn = QPushButton("Refresh")
        self.refresh_sessions_btn.clicked.connect(self._refresh_session_container_lists)
        queue_btn_layout.addWidget(self.refresh_sessions_btn)

        self.load_session_btn = QPushButton("Load Container")
        self.load_session_btn.clicked.connect(self._on_load_selected_session_container)
        queue_btn_layout.addWidget(self.load_session_btn)

        self.close_session_btn = QPushButton("Close")
        self.close_session_btn.clicked.connect(self._on_close_pending_session)
        queue_btn_layout.addWidget(self.close_session_btn)

        self.send_session_btn = QPushButton("Close and Send")
        self.send_session_btn.clicked.connect(self._on_send_pending_session)
        queue_btn_layout.addWidget(self.send_session_btn)
        queue_btn_layout.addStretch()
        queue_layout.addLayout(queue_btn_layout)

        self.pending_session_summary_label = QLabel("No session container in measurements folder.")
        self.pending_session_summary_label.setWordWrap(True)
        self.pending_session_summary_label.setStyleSheet(
            "padding: 10px; border: 1px solid #d0d0d0; border-radius: 4px;"
        )
        queue_layout.addWidget(self.pending_session_summary_label)

        layout.addWidget(queue_group)

        layout.addStretch()

        if hasattr(self, "tabs"):
            self.tabs.addTab(tab, "Session")
            self.create_archive_tab()

        self._update_session_tab_info()
        self._refresh_session_container_lists()

    def create_archive_tab(self):
        """Create dedicated archive browser tab with filters and sorting."""
        if not hasattr(self, "tabs"):
            return
        if hasattr(self, "archive_path_label"):
            return

        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.archive_path_label = QLabel("")
        self.archive_path_label.setStyleSheet("color: #555; padding: 4px;")
        layout.addWidget(self.archive_path_label)

        filter_row = QHBoxLayout()
        self.archive_date_filter_combo = QComboBox()
        self.archive_date_filter_combo.addItems(
            ["All dates", "Today", "Last 7 days", "Last 30 days"]
        )
        self.archive_date_filter_combo.currentIndexChanged.connect(
            self._apply_archive_filters
        )
        filter_row.addWidget(self.archive_date_filter_combo)

        self.archive_project_filter_edit = QLineEdit()
        self.archive_project_filter_edit.setPlaceholderText("Project filter")
        self.archive_project_filter_edit.textChanged.connect(self._apply_archive_filters)
        filter_row.addWidget(self.archive_project_filter_edit)

        self.archive_operator_filter_edit = QLineEdit()
        self.archive_operator_filter_edit.setPlaceholderText("Operator filter")
        self.archive_operator_filter_edit.textChanged.connect(self._apply_archive_filters)
        filter_row.addWidget(self.archive_operator_filter_edit)

        self.archive_search_edit = QLineEdit()
        self.archive_search_edit.setPlaceholderText("Search file/sample/study...")
        self.archive_search_edit.textChanged.connect(self._apply_archive_filters)
        filter_row.addWidget(self.archive_search_edit)

        self.archive_sort_combo = QComboBox()
        self.archive_sort_combo.addItems(
            [
                "Archived: newest first",
                "Archived: oldest first",
                "Project: A-Z",
                "Operator: A-Z",
            ]
        )
        self.archive_sort_combo.currentIndexChanged.connect(self._apply_archive_filters)
        filter_row.addWidget(self.archive_sort_combo)

        self.refresh_archive_btn = QPushButton("Refresh")
        self.refresh_archive_btn.clicked.connect(self._refresh_session_container_lists)
        filter_row.addWidget(self.refresh_archive_btn)
        layout.addLayout(filter_row)

        self.archived_sessions_table = QTableWidget()
        self.archived_sessions_table.setColumnCount(10)
        self.archived_sessions_table.setHorizontalHeaderLabels(
            [
                "File",
                "Specimen",
                "Project",
                "Study",
                "Operator",
                "Uploaded By",
                "Created",
                "Archived",
                "Status",
                "Path",
            ]
        )
        self.archived_sessions_table.setColumnHidden(9, True)
        self.archived_sessions_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.archived_sessions_table.customContextMenuRequested.connect(
            self._show_archived_sessions_context_menu
        )
        self.archived_sessions_table.setSortingEnabled(True)
        layout.addWidget(self.archived_sessions_table)
        layout.addStretch()

        self.tabs.addTab(tab, "Archive")

    def _get_measurements_folder_for_queue(self) -> Path:
        if hasattr(self, "config") and self.config:
            folder = self.config.get("measurements_folder") or self.config.get(
                "session_folder"
            )
            if folder:
                return Path(folder)

        if hasattr(self, "folderLineEdit"):
            folder = (self.folderLineEdit.text() or "").strip()
            if folder:
                return Path(folder)

        if (
            hasattr(self, "session_manager")
            and self.session_manager
            and getattr(self.session_manager, "session_path", None)
        ):
            return Path(self.session_manager.session_path).parent

        return Path.home() / "difra_measurements"

    def _get_session_archive_folder(self) -> Path:
        measurements_folder = self._get_measurements_folder_for_queue()
        return SessionLifecycleService.resolve_archive_folder(
            config=self.config if hasattr(self, "config") else None,
            measurements_folder=measurements_folder,
        )

    def _refresh_session_container_lists(self):
        if not hasattr(self, "pending_session_summary_label"):
            return

        schema = self._container_schema()
        container_manager = self._container_manager()
        pending_rows = SessionTabPresenter.build_pending_rows(
            self._get_measurements_folder_for_queue(),
            schema=schema,
            container_manager=container_manager,
        )
        archived_rows = SessionTabPresenter.build_archived_rows(
            self._get_session_archive_folder(),
            schema=schema,
            container_manager=container_manager,
        )
        self._pending_rows = list(pending_rows)
        self._update_pending_session_summary(self._pending_rows)
        self._archived_rows_all = list(archived_rows)
        if hasattr(self, "archived_sessions_table"):
            self._apply_archive_filters()

        if hasattr(self, "archive_path_label"):
            archive_folder = self._get_session_archive_folder()
            self.archive_path_label.setText(f"Archive folder: {archive_folder}")

    def _set_pending_session_actions_enabled(self, enabled: bool) -> None:
        for attr_name in ("load_session_btn", "close_session_btn", "send_session_btn"):
            button = getattr(self, attr_name, None)
            if button is not None:
                button.setEnabled(bool(enabled))

    def _update_pending_session_summary(self, pending_rows: List[dict]) -> None:
        rows = list(pending_rows or [])
        self._current_pending_container_path = None

        if not rows:
            self.pending_session_summary_label.setText(
                "No session container in measurements folder."
            )
            self._set_pending_session_actions_enabled(False)
            return

        if len(rows) > 1:
            file_names = [str(row.get("file_name") or "") for row in rows[:3]]
            summary = [
                f"Multiple session containers found in measurements folder ({len(rows)}).",
                "This screen expects exactly one active session container.",
            ]
            if file_names:
                summary.append("")
                summary.extend(file_names)
            self.pending_session_summary_label.setText("\n".join(summary))
            self._set_pending_session_actions_enabled(False)
            return

        row = rows[0]
        raw_path = str(row.get("path") or "").strip()
        self._current_pending_container_path = Path(raw_path) if raw_path else None
        summary = [
            f"File: {row.get('file_name', '')}",
            f"Specimen: {row.get('sample_id', '')}",
            f"Study: {row.get('study_name', '')}",
            f"Operator: {row.get('operator_id', '')}",
            f"Created: {row.get('created', '')}",
            f"Status: {row.get('status', '')}",
        ]
        self.pending_session_summary_label.setText("\n".join(summary))
        self._set_pending_session_actions_enabled(self._current_pending_container_path is not None)

    @staticmethod
    def _parse_archive_datetime(value: str):
        raw = str(value or "").strip()
        if not raw:
            return None
        for fmt in (
            "%Y%m%d_%H%M%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y%m%d",
        ):
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                continue
        return None

    def _row_sort_datetime(self, row):
        archived_dt = self._parse_archive_datetime(row.get("archived", ""))
        if archived_dt is not None:
            return archived_dt
        created_dt = self._parse_archive_datetime(row.get("created", ""))
        if created_dt is not None:
            return created_dt
        return datetime.min

    def _apply_archive_filters(self):
        if not hasattr(self, "archived_sessions_table"):
            return

        rows = list(getattr(self, "_archived_rows_all", []) or [])
        date_mode = (
            self.archive_date_filter_combo.currentText()
            if hasattr(self, "archive_date_filter_combo")
            else "All dates"
        )
        project_filter = (
            str(self.archive_project_filter_edit.text() or "").strip().lower()
            if hasattr(self, "archive_project_filter_edit")
            else ""
        )
        operator_filter = (
            str(self.archive_operator_filter_edit.text() or "").strip().lower()
            if hasattr(self, "archive_operator_filter_edit")
            else ""
        )
        search_filter = (
            str(self.archive_search_edit.text() or "").strip().lower()
            if hasattr(self, "archive_search_edit")
            else ""
        )

        now = datetime.now()
        filtered = []
        for row in rows:
            row_dt = self._row_sort_datetime(row)

            if date_mode == "Today" and row_dt.date() != now.date():
                continue
            if date_mode == "Last 7 days" and row_dt < (now - timedelta(days=7)):
                continue
            if date_mode == "Last 30 days" and row_dt < (now - timedelta(days=30)):
                continue

            project_value = str(row.get("project_id") or row.get("study_name") or "")
            operator_value = str(row.get("operator_id") or "")
            if project_filter and project_filter not in project_value.lower():
                continue
            if operator_filter and operator_filter not in operator_value.lower():
                continue

            if search_filter:
                haystack = " ".join(
                    [
                        str(row.get("file_name", "")),
                        str(row.get("sample_id", "")),
                        str(row.get("project_id", "")),
                        str(row.get("study_name", "")),
                        str(row.get("operator_id", "")),
                        str(row.get("uploaded_by", "")),
                        str(row.get("status", "")),
                    ]
                ).lower()
                if search_filter not in haystack:
                    continue

            filtered.append(row)

        sort_mode = (
            self.archive_sort_combo.currentText()
            if hasattr(self, "archive_sort_combo")
            else "Archived: newest first"
        )
        if sort_mode == "Archived: oldest first":
            filtered.sort(key=self._row_sort_datetime)
        elif sort_mode == "Project: A-Z":
            filtered.sort(key=lambda row: str(row.get("project_id") or row.get("study_name") or "").lower())
        elif sort_mode == "Operator: A-Z":
            filtered.sort(key=lambda row: str(row.get("operator_id") or "").lower())
        else:
            filtered.sort(key=self._row_sort_datetime, reverse=True)

        SessionTabPresenter.populate_archive_table(self.archived_sessions_table, filtered)

    def _selected_pending_container(self) -> Optional[Path]:
        return getattr(self, "_current_pending_container_path", None)

    def _all_pending_containers(self) -> List[Path]:
        return [
            Path(str(row.get("path")))
            for row in list(getattr(self, "_pending_rows", []) or [])
            if str(row.get("path") or "").strip()
        ]

    def _path_from_table_row(self, table: QTableWidget, row: int, path_col: int):
        if row < 0:
            return None
        path_item = table.item(row, path_col)
        if path_item is None:
            return None
        raw = (path_item.text() or "").strip()
        if not raw:
            return None
        return Path(raw)

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
        value, accepted = QInputDialog.getText(
            self,
            "Matador Specimen ID Required",
            "\n".join(
                [
                    f"Container: {container_path.name}",
                    "",
                    f"The stored specimen '{raw_specimen}' is not a valid Matador integer specimen ID.",
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

    def _show_archived_sessions_context_menu(self, pos):
        table = self.archived_sessions_table
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
        send_action = menu.addAction(
            "Send To Matador Again" if transfer_status == "SENT" else "Send To Matador"
        )
        if transfer_status == "NOT_COMPLETE":
            send_action.setEnabled(False)
        old_format_action = menu.addAction("Generate Old Format")
        selected = menu.exec_(table.viewport().mapToGlobal(pos))
        if selected == load_action:
            self._open_session_container_path(container_path)
        elif selected == send_action:
            self._send_archived_sessions([container_path])
        elif selected == old_format_action:
            self._generate_old_format_for_container(container_path)

    def _on_load_session_container_from_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
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
            QMessageBox.warning(
                self,
                "No Container Selected",
                "Select a session container from the queue.",
            )
            return
        self._open_session_container_path(container_path)

    def _send_and_archive_sessions(self, container_paths: List[Path]):
        if not container_paths:
            QMessageBox.information(self, "No Containers", "No session containers selected.")
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
                "cannot be sent to Matador:\n\n"
                + "\n".join(blocked),
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
        if not uploader_id and hasattr(self, "config") and isinstance(self.config, dict):
            uploader_id = self.config.get("operator_id")
        upload_context = self._request_upload_login_context(
            fallback_operator=str(uploader_id or lock_user or "unknown")
        )
        if upload_context is None:
            QMessageBox.information(self, "Upload Cancelled", "Upload was cancelled by operator.")
            return
        uploader_id = str(upload_context.get("uploader_id") or uploader_id or lock_user or "unknown")
        runtime_config = dict(self.config if hasattr(self, "config") and isinstance(self.config, dict) else {})
        runtime_config["matador_token"] = str(upload_context.get("token") or runtime_config.get("matador_token") or "")
        runtime_config["matador_url"] = str(upload_context.get("matador_url") or runtime_config.get("matador_url") or "")
        simulate_upload_failure = False
        simulate_upload_failure = bool(runtime_config.get("upload_stub_force_failure", False))

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
            progress_label.setText(message or "Sending session containers to Matador...")
            QApplication.processEvents()

        workflow_result = None
        try:
            workflow_result = SessionLifecycleActions.send_and_archive_session_containers(
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
            f"{workflow_result.upload_failed} failed"
        )
        summary.append(f"Cleaned measurement artifacts: {workflow_result.cleaned_artifacts}")
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

        progress_log.appendPlainText("")
        for line in summary:
            progress_log.appendPlainText(line)
        progress_label.setText(
            "Matador send finished with failures."
            if workflow_result.upload_failed > 0
            else "Matador send finished successfully."
        )
        close_button.setEnabled(True)
        QApplication.processEvents()

        self._refresh_session_container_lists()
        if hasattr(self, "update_session_status"):
            self.update_session_status()

    def _send_archived_sessions(self, container_paths: List[Path]):
        if not container_paths:
            QMessageBox.information(self, "No Containers", "No archived session containers selected.")
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
                "cannot be sent to Matador:\n\n"
                + "\n".join(blocked),
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
        if not uploader_id and hasattr(self, "config") and isinstance(self.config, dict):
            uploader_id = self.config.get("operator_id")

        upload_context = self._request_upload_login_context(
            fallback_operator=str(uploader_id or lock_user or "unknown")
        )
        if upload_context is None:
            QMessageBox.information(self, "Upload Cancelled", "Upload was cancelled by operator.")
            return

        uploader_id = str(upload_context.get("uploader_id") or uploader_id or lock_user or "unknown")
        runtime_config = dict(self.config if hasattr(self, "config") and isinstance(self.config, dict) else {})
        runtime_config["matador_token"] = str(upload_context.get("token") or runtime_config.get("matador_token") or "")
        runtime_config["matador_url"] = str(upload_context.get("matador_url") or runtime_config.get("matador_url") or "")
        simulate_upload_failure = bool(runtime_config.get("upload_stub_force_failure", False))

        specimen_overrides = {}
        for container_path in container_paths:
            metadata = SessionLifecycleActions._read_matador_session_metadata(
                Path(container_path),
                config=runtime_config,
                uploader_id=uploader_id,
            )
            specimen_text = str(metadata.get("specimen_text") or "").strip()
            if (
                specimen_text
                and metadata.get("specimen_id") is None
                and any(ch.isdigit() for ch in specimen_text)
            ):
                override = self._request_matador_specimen_override(
                    container_path=Path(container_path),
                    specimen_text=specimen_text,
                )
                if override is None:
                    QMessageBox.information(
                        self,
                        "Upload Cancelled",
                        f"Matador resend was cancelled for:\n{Path(container_path).name}",
                    )
                    return
                specimen_overrides[str(Path(container_path))] = int(override)

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
            progress_label.setText(message or "Sending archived session containers to Matador...")
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

        progress_log.appendPlainText("")
        for line in summary:
            progress_log.appendPlainText(line)
        progress_label.setText(
            "Matador resend finished with failures."
            if workflow_result.upload_failed > 0
            else "Matador resend finished successfully."
        )
        close_button.setEnabled(True)
        QApplication.processEvents()

        self._refresh_session_container_lists()
        if hasattr(self, "update_session_status"):
            self.update_session_status()

    def _archive_sessions(self, container_paths: List[Path]):
        if not container_paths:
            QMessageBox.information(self, "No Containers", "No session containers selected.")
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
                self, "Queue Empty", "No session containers found in measurements folder."
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
                self, "Queue Empty", "No session containers found in measurements folder."
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

    def _on_close_finalize_session(self):
        """Close and finalize the active session container and archive measurement files."""
        if not hasattr(self, "session_manager") or not self.session_manager.is_session_active():
            QMessageBox.warning(self, "No Active Session", "No session is currently active.")
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
        if not hasattr(self, "session_manager") or not self.session_manager.is_session_active():
            QMessageBox.warning(self, "No Active Session", "No session is currently active.")
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
        logger.info("Matador upload requested from session queue", sample_id=info["sample_id"])
