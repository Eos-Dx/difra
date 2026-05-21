"""Session management tab for Zone Measurements."""

from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Dict, List, Optional

from difra.gui.qt_compat import Qt
from difra.gui.qt_compat import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
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
    QSizePolicy,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from difra.gui.container_api import get_container_manager, get_schema
from difra.gui.main_window_ext.archive_session_edit_dialog import (
    ArchiveSessionEditDialog,
)
from difra.gui.matador_runtime_context import (
    get_runtime_matador_context,
    set_runtime_matador_context,
)
from difra.gui.matador_upload_error_reporter import (
    send_matador_upload_error_report,
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

    ARCHIVE_METADATA_EDIT_PASSWORD_HASH = (
        "64ae5ac9f98ac4a2bb67a66cc913909022d4d0bb7d673fcf76d1999c33debd93"
    )

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
            "uploadPending": int(getattr(workflow_result, "upload_pending", 0)),
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

    def _send_matador_upload_error_report(
        self,
        *,
        runtime_config: dict,
        workflow_result,
        log_path: Path,
        context: str,
    ) -> str:
        if int(getattr(workflow_result, "upload_failed", 0) or 0) <= 0:
            return ""
        try:
            result = send_matador_upload_error_report(
                config=runtime_config,
                workflow_result=workflow_result,
                log_path=Path(log_path),
                context=context,
            )
        except Exception as exc:
            logger.warning("Failed to send Matador upload error email", exc_info=True)
            return f"Matador error email failed: {exc}"
        return str(result.get("message") or "").strip()

    def _schedule_matador_pending_verification(
        self,
        *,
        container_paths: List[Path],
        runtime_config: dict,
    ) -> None:
        paths = [Path(path) for path in (container_paths or []) if Path(path).exists()]
        if not paths:
            return
        if getattr(self, "_matador_pending_verification_running", False):
            return
        interval_sec = max(
            float(runtime_config.get("matador_async_verification_interval_sec", 30.0)),
            5.0,
        )
        batch_size = max(
            int(
                runtime_config.get(
                    "matador_async_verification_batch_size",
                    runtime_config.get("matador_upload_max_parallel", 4),
                )
            ),
            1,
        )
        max_rounds = max(
            int(runtime_config.get("matador_async_verification_max_rounds", 40)),
            1,
        )
        container_manager = self._container_manager()
        setattr(self, "_matador_pending_verification_running", True)

        def _worker():
            try:
                offset = 0
                for round_index in range(1, max_rounds + 1):
                    time.sleep(interval_sec)
                    pending_paths = []
                    for path in paths:
                        try:
                            info = SessionTabPresenter.read_session_container_metadata(
                                path,
                                schema=self._container_schema(),
                                container_manager=container_manager,
                            )
                        except Exception:
                            continue
                        if str(info.get("upload_status") or "") == (
                            SessionLifecycleActions.UPLOAD_STATUS_PENDING_VERIFICATION
                        ):
                            pending_paths.append(path)
                    if not pending_paths:
                        break
                    if offset >= len(pending_paths):
                        offset = 0
                    batch = pending_paths[offset : offset + batch_size]
                    if not batch:
                        batch = pending_paths[:batch_size]
                        offset = 0
                    offset = (offset + batch_size) % max(len(pending_paths), 1)
                    result = SessionLifecycleActions.verify_pending_matador_uploads(
                        batch,
                        container_manager=container_manager,
                        config=runtime_config,
                        operator_id=str(runtime_config.get("operator_id") or "unknown"),
                    )
                    logger.info(
                        "Matador pending verification round %s/%s: checked=%s success=%s pending=%s failed=%s",
                        round_index,
                        max_rounds,
                        len(batch),
                        result.upload_success,
                        result.upload_pending,
                        result.upload_failed,
                    )
            finally:
                setattr(self, "_matador_pending_verification_running", False)

        thread = threading.Thread(
            target=_worker,
            name="matador-pending-verifier",
            daemon=True,
        )
        thread.start()

    def _container_schema(self):
        return get_schema(self.config if hasattr(self, "config") else None)

    def _container_manager(self):
        return get_container_manager(self.config if hasattr(self, "config") else None)

    def _request_upload_login_context(self, fallback_operator: str):
        """Collect uploader identity and Matador token right before send."""
        runtime_context = get_runtime_matador_context(self)
        default_operator = str(fallback_operator or "unknown")
        default_url = str(runtime_context.get("matador_url") or "").strip()
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

        url_edit = QLineEdit(default_url)
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
        primary_btn_row = QHBoxLayout()
        secondary_btn_row = QHBoxLayout()
        self.new_session_btn = QPushButton("Create Session")
        on_new_session = getattr(self, "on_new_session", None)
        if callable(on_new_session):
            self.new_session_btn.clicked.connect(on_new_session)
        else:
            self.new_session_btn.setEnabled(False)
        primary_btn_row.addWidget(self.new_session_btn)

        self.load_session_btn = QPushButton("Load Container")
        self.load_session_btn.clicked.connect(self._on_load_selected_session_container)
        primary_btn_row.addWidget(self.load_session_btn)

        self.preview_session_data_btn = QPushButton("Check data")
        self.preview_session_data_btn.clicked.connect(self._on_preview_session_data)
        primary_btn_row.addWidget(self.preview_session_data_btn)
        primary_btn_row.addStretch()

        self.close_session_btn = QPushButton("Close")
        self.close_session_btn.clicked.connect(self._on_close_pending_session)
        secondary_btn_row.addWidget(self.close_session_btn)

        self.send_session_btn = QPushButton("Close and Send")
        self.send_session_btn.clicked.connect(self._on_send_pending_session)
        secondary_btn_row.addWidget(self.send_session_btn)

        self.refresh_sessions_btn = QPushButton("Refresh")
        self.refresh_sessions_btn.clicked.connect(self._refresh_session_container_lists)
        secondary_btn_row.addWidget(self.refresh_sessions_btn)
        secondary_btn_row.addStretch()
        info_layout.addLayout(primary_btn_row)
        info_layout.addLayout(secondary_btn_row)

        self.session_info_label = QLabel("No active session")
        self.session_info_label.setStyleSheet("padding: 10px;")
        info_layout.addWidget(self.session_info_label)
        layout.addWidget(info_group)

        self._pending_session_summary_text = "No session container in measurements folder."

        layout.addStretch()

        if hasattr(self, "tabs"):
            self.tabs.addTab(tab, "Session")
            self.create_archive_tab()

        self._update_session_tab_info()
        self._refresh_session_container_lists()

    def create_archive_tab(self):
        """Create compact archive launcher tab."""
        if not hasattr(self, "tabs"):
            return
        if hasattr(self, "archive_path_label"):
            return

        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.archive_path_label = QLabel("")
        self.archive_path_label.setStyleSheet("color: #555; padding: 4px;")
        layout.addWidget(self.archive_path_label)

        action_row = QHBoxLayout()
        self.open_archive_window_btn = QPushButton("Archive")
        self.open_archive_window_btn.clicked.connect(self._open_archive_window_from_tab)
        action_row.addWidget(self.open_archive_window_btn)
        action_row.addStretch()
        layout.addLayout(action_row)
        layout.addStretch()

        self.tabs.addTab(tab, "Archive")

    def _open_archive_window_from_tab(self):
        self._refresh_session_container_lists()
        self._show_archive_window()

    def _create_archive_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(10)
        table.setHorizontalHeaderLabels(
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
        table.setColumnHidden(9, True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, source_table=table: self._show_archived_sessions_context_menu(
                pos, table=source_table
            )
        )
        table.setSortingEnabled(True)
        table.setWordWrap(False)
        table.setAlternatingRowColors(True)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        table.setMinimumHeight(420)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        for column, width in enumerate([320, 110, 180, 260, 140, 140, 150, 150, 110, 0]):
            table.setColumnWidth(column, width)
        table.verticalHeader().setDefaultSectionSize(24)
        return table

    def _show_archive_window(self):
        dialog = getattr(self, "_archive_window_dialog", None)
        if dialog is not None and dialog.isVisible():
            self._populate_archive_window_table()
            dialog.raise_()
            dialog.activateWindow()
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Archived Sessions")
        dialog.setModal(False)
        dialog.resize(1500, 900)
        layout = QVBoxLayout(dialog)

        self.archive_window_path_label = QLabel("")
        self.archive_window_path_label.setStyleSheet("color: #555; padding: 4px;")
        layout.addWidget(self.archive_window_path_label)

        filter_row = QHBoxLayout()
        self.archive_window_date_filter_combo = QComboBox()
        self.archive_window_date_filter_combo.addItems(
            ["All dates", "Today", "Last 7 days", "Last 30 days"]
        )
        self.archive_window_date_filter_combo.currentIndexChanged.connect(
            self._populate_archive_window_table
        )
        filter_row.addWidget(self.archive_window_date_filter_combo)

        self.archive_window_status_filter_combo = QComboBox()
        self.archive_window_status_filter_combo.addItems(
            ["All statuses", "Unsent", "Sent", "Not complete"]
        )
        self.archive_window_status_filter_combo.currentIndexChanged.connect(
            self._populate_archive_window_table
        )
        filter_row.addWidget(self.archive_window_status_filter_combo)

        self.archive_window_project_filter_edit = QLineEdit()
        self.archive_window_project_filter_edit.setPlaceholderText("Project filter")
        self.archive_window_project_filter_edit.textChanged.connect(
            self._populate_archive_window_table
        )
        filter_row.addWidget(self.archive_window_project_filter_edit)

        self.archive_window_operator_filter_edit = QLineEdit()
        self.archive_window_operator_filter_edit.setPlaceholderText("Operator filter")
        self.archive_window_operator_filter_edit.textChanged.connect(
            self._populate_archive_window_table
        )
        filter_row.addWidget(self.archive_window_operator_filter_edit)

        self.archive_window_search_edit = QLineEdit()
        self.archive_window_search_edit.setPlaceholderText("Search file/sample/study...")
        self.archive_window_search_edit.textChanged.connect(
            self._populate_archive_window_table
        )
        filter_row.addWidget(self.archive_window_search_edit)

        self.archive_window_sort_combo = QComboBox()
        self.archive_window_sort_combo.addItems(
            [
                "Archived: newest first",
                "Archived: oldest first",
                "Project: A-Z",
                "Operator: A-Z",
            ]
        )
        self.archive_window_sort_combo.currentIndexChanged.connect(
            self._populate_archive_window_table
        )
        filter_row.addWidget(self.archive_window_sort_combo)
        layout.addLayout(filter_row)

        table = self._create_archive_table()
        table.itemSelectionChanged.connect(self._update_archive_action_buttons)
        self.archive_window_table = table
        layout.addWidget(table, 1)

        actions = QHBoxLayout()
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._refresh_session_container_lists)
        actions.addWidget(refresh_button)

        self.send_archived_window_btn = QPushButton("Send Selected")
        self.send_archived_window_btn.clicked.connect(
            lambda: self._send_archived_sessions(
                self._selected_paths_from_archive_table(table)
            )
        )
        self.send_archived_window_btn.setEnabled(False)
        actions.addWidget(self.send_archived_window_btn)
        actions.addStretch()
        layout.addLayout(actions)

        dialog.finished.connect(self._clear_archive_window_refs)
        self._archive_window_dialog = dialog
        self._populate_archive_window_table()
        dialog.show()

    def _clear_archive_window_refs(self):
        self._archive_window_dialog = None
        self.archive_window_table = None
        self.archive_window_path_label = None
        self.archive_window_date_filter_combo = None
        self.archive_window_status_filter_combo = None
        self.archive_window_project_filter_edit = None
        self.archive_window_operator_filter_edit = None
        self.archive_window_search_edit = None
        self.archive_window_sort_combo = None
        self.send_archived_window_btn = None

    def _populate_archive_window_table(self):
        table = getattr(self, "archive_window_table", None)
        if table is None:
            return
        rows = list(getattr(self, "_archived_rows_all", []) or [])
        date_mode = (
            self.archive_window_date_filter_combo.currentText()
            if getattr(self, "archive_window_date_filter_combo", None) is not None
            else "All dates"
        )
        transfer_status_filter = (
            self.archive_window_status_filter_combo.currentText()
            if getattr(self, "archive_window_status_filter_combo", None) is not None
            else "All statuses"
        )
        project_filter = (
            str(self.archive_window_project_filter_edit.text() or "").strip().lower()
            if getattr(self, "archive_window_project_filter_edit", None) is not None
            else ""
        )
        operator_filter = (
            str(self.archive_window_operator_filter_edit.text() or "").strip().lower()
            if getattr(self, "archive_window_operator_filter_edit", None) is not None
            else ""
        )
        search_filter = (
            str(self.archive_window_search_edit.text() or "").strip().lower()
            if getattr(self, "archive_window_search_edit", None) is not None
            else ""
        )
        sort_mode = (
            self.archive_window_sort_combo.currentText()
            if getattr(self, "archive_window_sort_combo", None) is not None
            else "Archived: newest first"
        )
        rows = SessionTabPresenter.filter_archived_rows(
            rows,
            date_mode=date_mode,
            transfer_status_filter=transfer_status_filter,
            project_filter=project_filter,
            operator_filter=operator_filter,
            search_filter=search_filter,
            sort_mode=sort_mode,
            now=datetime.now(),
        )
        SessionTabPresenter.populate_archive_table(table, rows)
        label = getattr(self, "archive_window_path_label", None)
        if label is not None:
            label.setText(f"Archive folder: {self._get_session_archive_folder()}")
        self._update_archive_action_buttons()

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
        if not hasattr(self, "_pending_session_summary_text"):
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
        self._archived_rows_filtered = list(archived_rows)
        self._populate_archive_window_table()
        self._update_archive_action_buttons()

        if hasattr(self, "archive_path_label"):
            archive_folder = self._get_session_archive_folder()
            self.archive_path_label.setText(f"Archive folder: {archive_folder}")

    def _set_pending_session_actions_enabled(self, enabled: bool) -> None:
        load_button = getattr(self, "load_session_btn", None)
        if load_button is not None:
            load_button.setEnabled(True)

        for attr_name in ("close_session_btn", "send_session_btn"):
            button = getattr(self, attr_name, None)
            if button is not None:
                button.setEnabled(bool(enabled))
        self._update_preview_session_data_enabled()

    def _active_session_container_path(self) -> Optional[Path]:
        session_manager = getattr(self, "session_manager", None)
        active_path = getattr(session_manager, "session_path", None)
        if not active_path:
            return None
        path = Path(active_path)
        return path if path.exists() else None

    def _preview_session_container_path(self) -> Optional[Path]:
        return self._selected_pending_container() or self._active_session_container_path()

    def _update_preview_session_data_enabled(self) -> None:
        button = getattr(self, "preview_session_data_btn", None)
        if button is not None:
            button.setEnabled(self._preview_session_container_path() is not None)

    def _update_pending_session_summary(self, pending_rows: List[dict]) -> None:
        rows = list(pending_rows or [])
        self._current_pending_container_path = None

        if not rows:
            self._pending_session_summary_text = "No session container in measurements folder."
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
            self._pending_session_summary_text = "\n".join(summary)
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
        self._pending_session_summary_text = "\n".join(summary)
        self._set_pending_session_actions_enabled(self._current_pending_container_path is not None)

    def _apply_archive_filters(self):
        self._archived_rows_filtered = list(getattr(self, "_archived_rows_all", []) or [])
        self._populate_archive_window_table()
        self._update_archive_action_buttons()

    def _selected_pending_container(self) -> Optional[Path]:
        return getattr(self, "_current_pending_container_path", None)

    @staticmethod
    def _session_preview_text(value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    @classmethod
    def _session_preview_detector_key(
        cls,
        *,
        alias,
        detector_id,
        role_name,
    ) -> Optional[str]:
        tokens = {
            cls._session_preview_text(alias).strip().upper(),
            cls._session_preview_text(detector_id).strip().upper(),
            cls._session_preview_text(role_name).strip().upper(),
        }
        expanded = set(tokens)
        for token in list(tokens):
            if token.startswith("DET_"):
                expanded.add(token[4:])
        if expanded & {"PRIMARY", "SAXS"}:
            return "PRIMARY"
        if expanded & {"SECONDARY", "WAXS"}:
            return "SECONDARY"
        return None

    def _session_container_has_attenuation(self, h5f, schema) -> bool:
        ana_group_path = getattr(
            schema,
            "GROUP_ANALYTICAL_MEASUREMENTS",
            "/analytical_measurements",
        )
        ana_group = h5f.get(ana_group_path)
        if ana_group is None:
            return False

        type_attr = getattr(schema, "ATTR_ANALYSIS_TYPE", "analysis_type")
        role_attr = getattr(schema, "ATTR_ANALYSIS_ROLE", "analysis_role")
        for item_name in sorted(ana_group.keys()):
            item = ana_group[item_name]
            analysis_type = self._session_preview_text(
                item.attrs.get(type_attr, item_name)
            ).strip().lower()
            analysis_role = self._session_preview_text(
                item.attrs.get(role_attr, "")
            ).strip().lower()
            if analysis_type.startswith("attenuation") or analysis_role in {
                "i0",
                "i",
                "without",
                "with",
                "without_sample",
                "with_sample",
            }:
                return True
        return False

    def _collect_session_data_preview(self, container_path: Path) -> dict:
        import h5py

        schema = self._container_schema()
        measurements_path = getattr(schema, "GROUP_MEASUREMENTS", "/entry/measurements")
        dataset_name = getattr(schema, "DATASET_PROCESSED_SIGNAL", "processed_signal")
        alias_attr = getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")
        detector_id_attr = getattr(schema, "ATTR_DETECTOR_ID", "detector_id")

        profiles = {"PRIMARY": [], "SECONDARY": []}
        attenuation_exists = False
        extractor = getattr(self, "_extract_profile_from_measurement", None)

        with h5py.File(container_path, "r") as h5f:
            attenuation_exists = self._session_container_has_attenuation(h5f, schema)
            measurements_group = h5f.get(measurements_path)
            if measurements_group is None:
                return {
                    "profiles": profiles,
                    "attenuation_exists": attenuation_exists,
                }

            for point_name in sorted(measurements_group.keys()):
                point_group = measurements_group[point_name]
                for measurement_name in sorted(point_group.keys()):
                    measurement_group = point_group[measurement_name]
                    for role_name in sorted(measurement_group.keys()):
                        detector_group = measurement_group[role_name]
                        if dataset_name not in detector_group:
                            continue
                        dataset_path = (
                            f"{measurement_group.name}/{role_name}/{dataset_name}"
                        )
                        alias = detector_group.attrs.get(alias_attr, role_name)
                        detector_id = detector_group.attrs.get(detector_id_attr, "")
                        key = self._session_preview_detector_key(
                            alias=alias,
                            detector_id=detector_id,
                            role_name=role_name,
                        )
                        if key not in profiles:
                            continue
                        ref = f"h5ref://{container_path}#{dataset_path}"
                        if callable(extractor):
                            npt = 100 if key == "SECONDARY" else 200
                            try:
                                profile = extractor(ref, alias=key, npt=npt)
                            except TypeError:
                                try:
                                    profile = extractor(ref, key)
                                except TypeError:
                                    profile = extractor(ref)
                        else:
                            profile = None
                        if not profile:
                            continue
                        profiles[key].append(
                            {
                                "label": f"{point_name}/{measurement_name}",
                                "profile": profile,
                            }
                        )

        return {"profiles": profiles, "attenuation_exists": attenuation_exists}

    @staticmethod
    def _plot_session_detector_profiles(axis, detector_key: str, rows: List[dict]):
        import numpy as np

        title = (
            "Primary detector"
            if detector_key == "PRIMARY"
            else "Secondary detector"
        )
        axis.set_title(title)
        axis.set_ylabel("Intensity")
        if not rows:
            axis.text(
                0.5,
                0.5,
                "No measurement profiles",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_axis_off()
            return

        uses_q = False
        plotted = False
        for row in rows:
            raw_profile = row.get("profile") or {}
            profile = (
                raw_profile
                if isinstance(raw_profile, dict)
                else {"intensity": raw_profile}
            )
            intensity = np.asarray(profile.get("intensity"), dtype=float).reshape(-1)
            if intensity.size < 2:
                continue
            q_values = profile.get("q_values")
            if q_values is not None:
                x_values = np.asarray(q_values, dtype=float).reshape(-1)
                uses_q = True
            else:
                x_values = np.arange(intensity.size, dtype=float)
            count = min(int(x_values.size), int(intensity.size))
            if count < 2:
                continue
            x_values = x_values[:count]
            intensity = intensity[:count]
            finite = np.isfinite(x_values) & np.isfinite(intensity) & (intensity > 0)
            if np.count_nonzero(finite) < 2:
                continue
            axis.plot(
                x_values[finite],
                intensity[finite],
                linewidth=0.9,
                alpha=0.65,
            )
            plotted = True

        if not plotted:
            axis.text(
                0.5,
                0.5,
                "No valid positive profiles",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_axis_off()
            return

        axis.set_yscale("log")
        axis.set_xlabel("q (nm^-1)" if uses_q else "Index")
        axis.grid(True, alpha=0.25)

    @staticmethod
    def _plot_session_attenuation_placeholder(axis, detector_key: str, exists: bool):
        axis.set_title(f"{detector_key.title()} attenuation")
        axis.set_xlabel("Measurement index")
        axis.set_ylabel("Absorption")
        axis.grid(True, alpha=0.25)
        if exists:
            axis.text(
                0.5,
                0.5,
                "Calculation placeholder",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
        else:
            axis.text(
                0.5,
                0.5,
                "No attenuation measurements",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )

    def _show_session_data_preview_dialog(self, container_path: Path, payload: dict):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure

        dialog = QDialog(self)
        dialog.setWindowTitle("See results: session data")
        dialog.setModal(False)
        dialog.resize(1280, 760)

        layout = QVBoxLayout(dialog)
        summary = QLabel(str(container_path))
        summary.setStyleSheet("color: #555; padding: 4px;")
        layout.addWidget(summary)

        fig = Figure(figsize=(12.5, 7.0), constrained_layout=True)
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas, 1)

        axes = fig.subplots(
            2,
            2,
            gridspec_kw={"height_ratios": [4, 1.25]},
        )
        profiles = payload.get("profiles") or {}
        self._plot_session_detector_profiles(
            axes[0][0],
            "PRIMARY",
            list(profiles.get("PRIMARY") or []),
        )
        self._plot_session_detector_profiles(
            axes[0][1],
            "SECONDARY",
            list(profiles.get("SECONDARY") or []),
        )
        attenuation_exists = bool(payload.get("attenuation_exists"))
        self._plot_session_attenuation_placeholder(
            axes[1][0],
            "PRIMARY",
            attenuation_exists,
        )
        self._plot_session_attenuation_placeholder(
            axes[1][1],
            "SECONDARY",
            attenuation_exists,
        )
        canvas.draw_idle()

        close_button = QPushButton("Close", dialog)
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)

        self._session_data_preview_dialog = dialog
        dialog.finished.connect(
            lambda *_args: setattr(self, "_session_data_preview_dialog", None)
        )
        dialog.show()
        return dialog

    def _on_preview_session_data(self):
        container_path = self._preview_session_container_path()
        if container_path is None or not Path(container_path).exists():
            QMessageBox.warning(
                self,
                "No Container Selected",
                "Select a session container with measurements.",
            )
            return

        try:
            payload = self._collect_session_data_preview(Path(container_path))
        except Exception as exc:
            logger.warning(
                "Failed to build session data preview",
                session_path=str(container_path),
                exc_info=True,
            )
            QMessageBox.warning(
                self,
                "Preview Failed",
                f"Could not build session data preview:\n{exc}",
            )
            return

        self._show_session_data_preview_dialog(Path(container_path), payload)

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

    def _selected_archived_containers(
        self, *, fallback_path: Optional[Path] = None
    ) -> List[Path]:
        table = getattr(self, "archive_window_table", None)
        return self._selected_paths_from_archive_table(
            table, fallback_path=fallback_path
        )

    def _selected_paths_from_archive_table(
        self,
        table: Optional[QTableWidget],
        *,
        fallback_path: Optional[Path] = None,
    ) -> List[Path]:
        if table is None:
            return [Path(fallback_path)] if fallback_path is not None else []
        selected_rows = sorted({index.row() for index in table.selectedIndexes()})
        selected_paths: List[Path] = []
        for row in selected_rows:
            path = self._path_from_table_row(table, row, 9)
            if path is not None:
                selected_paths.append(Path(path))

        if fallback_path is None:
            return selected_paths

        fallback_resolved = str(Path(fallback_path))
        if not selected_paths:
            return [Path(fallback_path)]
        if fallback_resolved not in {str(path) for path in selected_paths}:
            return [Path(fallback_path)]
        return selected_paths

    def _update_archive_action_buttons(self):
        window_button = getattr(self, "send_archived_window_btn", None)
        window_table = getattr(self, "archive_window_table", None)
        if window_button is not None:
            window_button.setEnabled(
                bool(self._selected_paths_from_archive_table(window_table))
            )

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

        dialog = ArchiveSessionEditDialog(
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
        elif selected == old_format_action:
            self._generate_old_format_for_container(container_path)

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
            self._on_load_session_container_from_dialog()
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
        runtime_config["operator_id"] = uploader_id
        runtime_config.setdefault("matador_upload_max_parallel", 4)
        runtime_config.setdefault("matador_async_verification_batch_size", 4)
        runtime_config["matador_token"] = str(upload_context.get("token") or runtime_config.get("matador_token") or "")
        runtime_config["matador_url"] = str(upload_context.get("matador_url") or runtime_config.get("matador_url") or "")
        simulate_upload_failure = False
        simulate_upload_failure = bool(runtime_config.get("upload_stub_force_failure", False))
        specimen_overrides = self._collect_matador_specimen_overrides(
            container_paths=container_paths,
            runtime_config=runtime_config,
            uploader_id=uploader_id,
        )
        if specimen_overrides is None:
            QMessageBox.information(self, "Upload Cancelled", "Upload was cancelled by operator.")
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
                specimen_overrides=specimen_overrides,
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
        runtime_config["operator_id"] = uploader_id
        runtime_config.setdefault("matador_upload_max_parallel", 4)
        runtime_config.setdefault("matador_async_verification_batch_size", 4)
        runtime_config["matador_token"] = str(upload_context.get("token") or runtime_config.get("matador_token") or "")
        runtime_config["matador_url"] = str(upload_context.get("matador_url") or runtime_config.get("matador_url") or "")
        simulate_upload_failure = bool(runtime_config.get("upload_stub_force_failure", False))
        specimen_overrides = self._collect_matador_specimen_overrides(
            container_paths=container_paths,
            runtime_config=runtime_config,
            uploader_id=uploader_id,
        )
        if specimen_overrides is None:
            QMessageBox.information(self, "Upload Cancelled", "Upload was cancelled by operator.")
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
        self._update_preview_session_data_enabled()

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
