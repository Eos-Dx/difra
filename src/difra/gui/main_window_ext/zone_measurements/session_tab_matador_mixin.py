"""Session management tab for Zone Measurements."""

from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import List, Optional

from difra.gui.qt_compat import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTimer,
    QVBoxLayout,
)

from difra.gui.container_api import get_container_manager, get_schema
from difra.gui.matador_runtime_context import (
    get_runtime_matador_context,
    set_runtime_matador_context,
)
from difra.gui.matador_upload_error_reporter import (
    send_matador_upload_error_report,
)
from difra.gui.session_lifecycle_actions import SessionLifecycleActions
from difra.gui.session_tab_presenter import SessionTabPresenter
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionTabMatadorMixin:
    """Session tab behavior split from SessionTabMixin."""

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
        logs_root = SessionLifecycleActions.resolve_matador_logs_root(
            config=runtime_config
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = logs_root / f"matador_send_{timestamp}.log"
        payload = {
            "createdAt": datetime.now().isoformat(timespec="seconds"),
            "uploadSessionId": str(
                getattr(workflow_result, "upload_session_id", "") or ""
            ),
            "uploadSuccess": int(getattr(workflow_result, "upload_success", 0)),
            "uploadPending": int(getattr(workflow_result, "upload_pending", 0)),
            "uploadFailed": int(getattr(workflow_result, "upload_failed", 0)),
            "moved": int(getattr(workflow_result, "moved", 0)),
            "archivedPaths": [
                str(path) for path in getattr(workflow_result, "archived_paths", [])
            ],
            "oldFormatPaths": [
                str(path) for path in getattr(workflow_result, "old_format_paths", [])
            ],
            "failed": list(getattr(workflow_result, "failed", []) or []),
            "oldFormatFailed": list(
                getattr(workflow_result, "old_format_failed", []) or []
            ),
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
        initial_delay_sec: Optional[float] = None,
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
        first_delay_sec = (
            interval_sec
            if initial_delay_sec is None
            else max(float(initial_delay_sec), 0.0)
        )
        container_manager = self._container_manager()
        setattr(self, "_matador_pending_verification_running", True)

        def _worker():
            try:
                offset = 0
                for round_index in range(1, max_rounds + 1):
                    delay_sec = first_delay_sec if round_index == 1 else interval_sec
                    if delay_sec > 0:
                        time.sleep(delay_sec)
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

    def _archive_pending_verification_paths(self) -> List[Path]:
        paths = []
        for row in list(getattr(self, "_archived_rows_all", []) or []):
            if str(row.get("upload_status") or "").strip() != (
                SessionLifecycleActions.UPLOAD_STATUS_PENDING_VERIFICATION
            ):
                continue
            raw_path = str(row.get("path") or "").strip()
            if not raw_path:
                continue
            path = Path(raw_path)
            if path.exists():
                paths.append(path)
        return paths

    def _runtime_config_for_archive_pending_verification(self) -> Optional[dict]:
        runtime_config = dict(
            self.config
            if hasattr(self, "config") and isinstance(self.config, dict)
            else {}
        )
        context = get_runtime_matador_context(self)
        token = str(
            context.get("token")
            or runtime_config.get("matador_token")
            or os.environ.get("MATADOR_TOKEN")
            or ""
        ).strip()
        matador_url = str(
            context.get("matador_url")
            or runtime_config.get("matador_url")
            or os.environ.get("MATADOR_URL")
            or ""
        ).strip()
        if not token or not matador_url:
            upload_context = self._request_upload_login_context(
                fallback_operator=str(runtime_config.get("operator_id") or "unknown")
            )
            if upload_context is None:
                return None
            token = str(upload_context.get("token") or "").strip()
            matador_url = str(upload_context.get("matador_url") or "").strip()
            runtime_config["operator_id"] = str(
                upload_context.get("uploader_id")
                or runtime_config.get("operator_id")
                or "unknown"
            )
        runtime_config["matador_token"] = token
        runtime_config["matador_url"] = matador_url
        runtime_config.setdefault("matador_upload_max_parallel", 4)
        runtime_config.setdefault("matador_async_verification_batch_size", 4)
        runtime_config.setdefault("matador_async_verification_interval_sec", 30.0)
        runtime_config.setdefault("matador_async_verification_max_rounds", 40)
        runtime_config.setdefault("operator_id", "unknown")
        return runtime_config

    def _ensure_archive_pending_refresh_timer(self) -> None:
        timer = getattr(self, "_archive_pending_refresh_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(5000)
            timer.timeout.connect(self._archive_pending_refresh_tick)
            setattr(self, "_archive_pending_refresh_timer", timer)
        if not timer.isActive():
            timer.start()

    def _archive_pending_refresh_tick(self) -> None:
        dialog = getattr(self, "_archive_window_dialog", None)
        if dialog is None or not dialog.isVisible():
            timer = getattr(self, "_archive_pending_refresh_timer", None)
            if timer is not None:
                timer.stop()
            return
        self._refresh_session_container_lists()
        if (
            not getattr(self, "_matador_pending_verification_running", False)
            and not self._archive_pending_verification_paths()
        ):
            timer = getattr(self, "_archive_pending_refresh_timer", None)
            if timer is not None:
                timer.stop()

    def _start_archive_pending_verification(self) -> None:
        pending_paths = self._archive_pending_verification_paths()
        if not pending_paths:
            return
        self._ensure_archive_pending_refresh_timer()
        if getattr(self, "_matador_pending_verification_running", False):
            return
        runtime_config = self._runtime_config_for_archive_pending_verification()
        if runtime_config is None:
            timer = getattr(self, "_archive_pending_refresh_timer", None)
            if timer is not None:
                timer.stop()
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    "Matador pending verification skipped: token or URL not configured."
                )
            return
        self._schedule_matador_pending_verification(
            container_paths=pending_paths,
            runtime_config=runtime_config,
            initial_delay_sec=0.0,
        )

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
