"""Email reporting for Matador upload failures."""

from __future__ import annotations

from email.message import EmailMessage
import json
import os
import platform
import smtplib
import socket
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_ERROR_EMAIL_RECIPIENT = "sdenisov@matur.co"
DEFAULT_ERROR_EMAIL_SENDER = "difra-upload@company.co.uk"


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    text = str(value).strip()
    return text if text else default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _config_value(config: Optional[Dict[str, Any]], key: str, env_key: str, default: Any) -> Any:
    if env_key in os.environ:
        return os.environ.get(env_key)
    if isinstance(config, dict) and key in config:
        return config.get(key)
    return default


def _workflow_value(workflow_result: Any, name: str, default: Any = "") -> Any:
    return getattr(workflow_result, name, default)


def build_matador_upload_error_email(
    *,
    config: Optional[Dict[str, Any]],
    workflow_result: Any,
    log_path: Path,
    context: str,
) -> EmailMessage:
    recipient = _as_text(
        _config_value(
            config,
            "upload_error_email_recipient",
            "DIFRA_UPLOAD_ERROR_EMAIL_TO",
            DEFAULT_ERROR_EMAIL_RECIPIENT,
        ),
        DEFAULT_ERROR_EMAIL_RECIPIENT,
    )
    sender = _as_text(
        _config_value(
            config,
            "upload_error_email_sender",
            "DIFRA_UPLOAD_ERROR_EMAIL_FROM",
            DEFAULT_ERROR_EMAIL_SENDER,
        ),
        DEFAULT_ERROR_EMAIL_SENDER,
    )

    failed = list(_workflow_value(workflow_result, "failed", []) or [])
    old_format_failed = list(_workflow_value(workflow_result, "old_format_failed", []) or [])
    upload_failed = int(_workflow_value(workflow_result, "upload_failed", 0) or 0)
    upload_success = int(_workflow_value(workflow_result, "upload_success", 0) or 0)
    upload_pending = int(_workflow_value(workflow_result, "upload_pending", 0) or 0)
    upload_session_id = _as_text(_workflow_value(workflow_result, "upload_session_id", ""))

    subject = (
        f"[DiFRA] Matador upload failure: {upload_failed} failed"
        f" / {upload_success} success"
        f" / {upload_pending} pending"
    )
    if upload_session_id:
        subject += f" | session {upload_session_id}"

    payload = {
        "context": str(context or "matador-upload"),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "logPath": str(Path(log_path)),
        "uploadSessionId": upload_session_id,
        "uploadSuccess": upload_success,
        "uploadPending": upload_pending,
        "uploadFailed": upload_failed,
        "moved": int(_workflow_value(workflow_result, "moved", 0) or 0),
        "failed": failed[:50],
        "oldFormatFailed": old_format_failed[:50],
    }

    body_lines = [
        "Matador upload failed.",
        "",
        f"Context: {payload['context']}",
        f"Host: {payload['host']}",
        f"Log: {payload['logPath']}",
        f"Upload session: {upload_session_id or '-'}",
        f"Result: {upload_success} success / {upload_pending} pending / {upload_failed} failed",
        "",
        "Failures:",
    ]
    if failed:
        body_lines.extend(f"- {item}" for item in failed[:20])
        if len(failed) > 20:
            body_lines.append(f"- ... and {len(failed) - 20} more")
    else:
        body_lines.append("- none recorded")

    if old_format_failed:
        body_lines.extend(["", "Old-format failures:"])
        body_lines.extend(f"- {item}" for item in old_format_failed[:20])
        if len(old_format_failed) > 20:
            body_lines.append(f"- ... and {len(old_format_failed) - 20} more")

    message = EmailMessage()
    message["To"] = recipient
    message["From"] = sender
    message["Subject"] = subject
    message.set_content("\n".join(body_lines))
    message.add_attachment(
        json.dumps(payload, indent=2, ensure_ascii=False),
        subtype="json",
        filename="matador_upload_failure.json",
    )
    return message


def send_matador_upload_error_report(
    *,
    config: Optional[Dict[str, Any]],
    workflow_result: Any,
    log_path: Path,
    context: str,
) -> Dict[str, Any]:
    enabled = _as_bool(
        _config_value(
            config,
            "upload_error_email_enabled",
            "DIFRA_UPLOAD_ERROR_EMAIL_ENABLED",
            True,
        ),
        True,
    )
    if not enabled:
        return {"sent": False, "skipped": True, "message": "upload error email disabled"}

    smtp_host = _as_text(
        _config_value(config, "upload_error_smtp_host", "DIFRA_UPLOAD_ERROR_SMTP_HOST", "")
    )
    if not smtp_host:
        return {"sent": False, "skipped": True, "message": "upload error SMTP host not configured"}

    smtp_port = _as_int(
        _config_value(config, "upload_error_smtp_port", "DIFRA_UPLOAD_ERROR_SMTP_PORT", 587),
        587,
    )
    username = _as_text(
        _config_value(
            config,
            "upload_error_smtp_username",
            "DIFRA_UPLOAD_ERROR_SMTP_USERNAME",
            "",
        )
    )
    password = _as_text(
        _config_value(
            config,
            "upload_error_smtp_password",
            "DIFRA_UPLOAD_ERROR_SMTP_PASSWORD",
            "",
        )
    )
    use_tls = _as_bool(
        _config_value(config, "upload_error_smtp_tls", "DIFRA_UPLOAD_ERROR_SMTP_TLS", True),
        True,
    )

    message = build_matador_upload_error_email(
        config=config,
        workflow_result=workflow_result,
        log_path=Path(log_path),
        context=context,
    )
    timeout_sec = float(
        _config_value(config, "upload_error_smtp_timeout_sec", "DIFRA_UPLOAD_ERROR_SMTP_TIMEOUT_SEC", 10.0)
    )
    with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout_sec) as smtp:
        if use_tls:
            smtp.starttls()
        if username or password:
            smtp.login(username, password)
        smtp.send_message(message)
    return {
        "sent": True,
        "skipped": False,
        "message": f"upload error email sent to {message['To']}",
    }
