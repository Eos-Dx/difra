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
    QBrush,
    QColor,
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
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTimer,
    QVBoxLayout,
    QWidget,
)

from difra.gui.archive_project_statistics import (
    build_archive_project_statistics,
    collect_matador_project_sets,
)
from difra.gui.container_api import get_container_manager, get_schema
from difra.gui.daily_valid_container_reporter import build_daily_report_for_containers
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

from .session_tab_archive_actions_mixin import SessionTabArchiveActionsMixin
from .session_tab_archive_window_mixin import SessionTabArchiveWindowMixin
from .session_tab_matador_mixin import SessionTabMatadorMixin
from .session_tab_queue_mixin import SessionTabQueueMixin
from .session_tab_send_workflow_mixin import SessionTabSendWorkflowMixin

logger = get_module_logger(__name__)


class SessionTabMixin(
    SessionTabArchiveActionsMixin,
    SessionTabArchiveWindowMixin,
    SessionTabMatadorMixin,
    SessionTabQueueMixin,
    SessionTabSendWorkflowMixin,
):
    """Mixin for session management tab in Zone Measurements."""

    ARCHIVE_METADATA_EDIT_PASSWORD_HASH = (
        "64ae5ac9f98ac4a2bb67a66cc913909022d4d0bb7d673fcf76d1999c33debd93"
    )
