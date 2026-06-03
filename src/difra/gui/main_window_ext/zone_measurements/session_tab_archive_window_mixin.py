"""Session management tab for Zone Measurements."""

from datetime import datetime
from typing import List, Optional

from difra.gui.qt_compat import Qt
from difra.gui.qt_compat import (
    QAbstractItemView,
    QBrush,
    QColor,
    QComboBox,
    QDialog,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from difra.gui.session_transfer_status import ARCHIVE_STATUS_FILTER_OPTIONS

from difra.gui.archive_project_statistics import (
    build_archive_project_statistics,
    collect_matador_project_sets,
)
from difra.gui.matador_runtime_context import (
    get_runtime_matador_context,
)
from difra.gui.matador_upload_api import build_matador_upload_api
from difra.gui.session_tab_presenter import SessionTabPresenter
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionTabArchiveWindowMixin:
    """Session tab behavior split from SessionTabMixin."""

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

        self._pending_session_summary_text = (
            "No session container in measurements folder."
        )

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
        for column, width in enumerate(
            [320, 110, 180, 260, 140, 140, 150, 150, 110, 0]
        ):
            table.setColumnWidth(column, width)
        table.verticalHeader().setDefaultSectionSize(24)
        return table

    def _show_archive_window(self):
        dialog = getattr(self, "_archive_window_dialog", None)
        if dialog is not None and dialog.isVisible():
            self._refresh_session_container_lists()
            self._start_archive_pending_verification()
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
        self.archive_window_status_filter_combo.addItems(ARCHIVE_STATUS_FILTER_OPTIONS)
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
        self.archive_window_search_edit.setPlaceholderText(
            "Search file/sample/study..."
        )
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

        self.archive_project_statistics_btn = QPushButton("Statistics by project")
        self.archive_project_statistics_btn.clicked.connect(
            self._show_archive_project_statistics
        )
        actions.addWidget(self.archive_project_statistics_btn)
        actions.addStretch()
        layout.addLayout(actions)

        dialog.finished.connect(self._clear_archive_window_refs)
        self._archive_window_dialog = dialog
        self._refresh_session_container_lists()
        dialog.show()
        self._start_archive_pending_verification()

    def _clear_archive_window_refs(self):
        timer = getattr(self, "_archive_pending_refresh_timer", None)
        if timer is not None:
            timer.stop()
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
        self.archive_project_statistics_btn = None

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

    @staticmethod
    def _stats_item(value, *, color: Optional[QColor] = None) -> QTableWidgetItem:
        item = QTableWidgetItem(str(value))
        item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        if color is not None:
            item.setBackground(QBrush(color))
        return item

    @staticmethod
    def _stats_count(value) -> str:
        return "Unknown" if value is None else str(value)

    @classmethod
    def _stats_status_color(cls, value: str) -> QColor:
        text = str(value or "").strip().lower()
        if text in {"sent", "ok", "in", "yes"}:
            return QColor("#d8f5d0")
        if text in {"unsent", "out", "no", "unmeasured"}:
            return QColor("#ffd6d6")
        if text in {"partial", "unknown"}:
            return QColor("#fff4bf")
        return QColor("#eeeeee")

    def _archive_project_keys_from_rows(self, rows: List[dict]) -> List[str]:
        keys = []
        for row in rows:
            raw = str(row.get("matadorProjectId") or "").strip()
            if raw and raw not in keys:
                keys.append(raw)
        return keys

    def _load_matador_archive_project_sets(self, rows: List[dict]):
        context = get_runtime_matador_context(self)
        token = str(context.get("token") or "").strip()
        matador_url = str(context.get("matador_url") or "").strip()
        if not token or not matador_url:
            return {}, {}, "Matador: token not configured"

        runtime_config = dict(getattr(self, "config", {}) or {})
        runtime_config.update(
            {
                "matador_url": matador_url,
                "matador_token": token,
                "matador_force_stub": False,
            }
        )
        try:
            api = build_matador_upload_api(runtime_config)
            studies = api.list_studies()
            specimen_sets, uploaded_sets, errors = collect_matador_project_sets(
                api=api,
                project_keys=self._archive_project_keys_from_rows(rows),
                studies=studies,
            )
        except Exception as exc:
            return {}, {}, f"Matador: unavailable ({exc})"

        if errors:
            return (
                specimen_sets,
                uploaded_sets,
                "Matador: partial data; " + "; ".join(errors[:3]),
            )
        return specimen_sets, uploaded_sets, "Matador: loaded"

    def _populate_project_statistics_tables(
        self,
        *,
        project_table: QTableWidget,
        specimen_table: QTableWidget,
        status_label: QLabel,
        stats,
        matador_status: str,
    ) -> None:
        project_table.setRowCount(0)
        for row_index, row in enumerate(stats.projects):
            project_table.insertRow(row_index)
            values = [
                row.get("label", ""),
                self._stats_count(row.get("matadorSpecimens")),
                row.get("archiveMeasured", 0),
                self._stats_count(row.get("matadorSpecimens")),
                self._stats_count(row.get("matadorUploaded")),
                self._stats_count(row.get("missingInArchive")),
                self._stats_count(row.get("notUploaded")),
                self._stats_count(row.get("archiveOnly")),
            ]
            for col, value in enumerate(values):
                color = None
                if col in {2, 4} and str(value) != "Unknown" and int(value or 0):
                    color = QColor("#d8f5d0")
                if col in {5, 6, 7} and str(value) != "Unknown" and int(value or 0):
                    color = QColor("#ffd6d6")
                item = self._stats_item(value, color=color)
                if col == 0:
                    item.setData(Qt.UserRole, str(row.get("key") or ""))
                project_table.setItem(row_index, col, item)

        def _select_project():
            selected_rows = sorted(
                {index.row() for index in project_table.selectedIndexes()}
            )
            if not selected_rows and project_table.rowCount():
                selected_rows = [0]
            if not selected_rows:
                return
            key_item = project_table.item(selected_rows[0], 0)
            project_key = str(key_item.data(Qt.UserRole) or "") if key_item else ""
            detail_rows = list(stats.specimens_by_project.get(project_key, []) or [])
            specimen_table.setRowCount(0)
            for detail_index, detail in enumerate(detail_rows):
                specimen_table.insertRow(detail_index)
                values = [
                    detail.get("displaySpecimenId") or detail.get("specimenId", ""),
                    detail.get("matadorSpecimen", "Unknown"),
                    "Yes" if detail.get("localMeasured") else "No",
                    detail.get("matadorMeasurement", "Unknown"),
                    detail.get("localStatus", ""),
                ]
                for col, value in enumerate(values):
                    color = None
                    if col in {1, 2, 3, 4}:
                        color = self._stats_status_color(str(value))
                    specimen_table.setItem(
                        detail_index,
                        col,
                        self._stats_item(value, color=color),
                    )
            specimen_table.resizeColumnsToContents()

        project_table.itemSelectionChanged.connect(_select_project)
        project_table.resizeColumnsToContents()
        status_label.setText(matador_status)
        if project_table.rowCount():
            project_table.selectRow(0)
            _select_project()

    def _show_archive_project_statistics(self):
        self._refresh_session_container_lists()
        rows = list(getattr(self, "_archived_rows_all", []) or [])
        if not rows:
            QMessageBox.information(
                self,
                "No Archive Data",
                "No archived session containers found.",
            )
            return

        matador_specimens, matador_uploaded, matador_status = (
            self._load_matador_archive_project_sets(rows)
        )
        stats = build_archive_project_statistics(
            rows,
            matador_specimens_by_project=matador_specimens,
            matador_uploaded_by_project=matador_uploaded,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("Statistics by project")
        dialog.setModal(False)
        dialog.resize(1250, 760)
        layout = QVBoxLayout(dialog)

        status_label = QLabel("")
        status_label.setStyleSheet("color: #555; padding: 4px;")
        layout.addWidget(status_label)

        project_box = QGroupBox("Projects")
        project_layout = QVBoxLayout(project_box)
        project_table = QTableWidget(0, 8, project_box)
        project_table.setHorizontalHeaderLabels(
            [
                "Project",
                "DB specimens",
                "Archive measured",
                "Matador specimens",
                "Matador In",
                "Not measured",
                "Archive not uploaded",
                "Archive only",
            ]
        )
        project_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        project_table.setSelectionMode(QAbstractItemView.SingleSelection)
        project_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        project_layout.addWidget(project_table)
        layout.addWidget(project_box, 1)

        specimen_box = QGroupBox("Specimens")
        specimen_layout = QVBoxLayout(specimen_box)
        specimen_table = QTableWidget(0, 5, specimen_box)
        specimen_table.setHorizontalHeaderLabels(
            [
                "Specimen ID",
                "Matador specimen",
                "Measured archive",
                "Matador In",
                "Archive status",
            ]
        )
        specimen_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        specimen_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        specimen_layout.addWidget(specimen_table)
        layout.addWidget(specimen_box, 2)

        buttons = QHBoxLayout()
        close_button = QPushButton("Close", dialog)
        close_button.clicked.connect(dialog.close)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._populate_project_statistics_tables(
            project_table=project_table,
            specimen_table=specimen_table,
            status_label=status_label,
            stats=stats,
            matador_status=matador_status,
        )
        self._archive_project_statistics_dialog = dialog
        dialog.finished.connect(
            lambda *_args: setattr(self, "_archive_project_statistics_dialog", None)
        )
        dialog.show()
