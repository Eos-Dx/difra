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
DEFAULT_DAILY_REPORT_KEYCHAIN_SERVICE = "difra_daily_report_smtp_password"
DEFAULT_DAILY_REPORT_EMAIL_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "config" / "daily_report_email.json"
)


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


def _load_json_config(path: Path) -> Dict[str, Any]:
    try:
        if not Path(path).exists():
            return {}
        with Path(path).open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_email_fallback_config() -> Dict[str, Any]:
    config: Dict[str, Any] = {}
    env_path = os.environ.get("DIFRA_DAILY_REPORT_EMAIL_CONFIG")
    if env_path:
        config.update(_load_json_config(Path(env_path).expanduser()))
    config.update(_load_json_config(DEFAULT_DAILY_REPORT_EMAIL_CONFIG_PATH))
    return config


def _config_is_blank(config: Dict[str, Any], key: str) -> bool:
    return key not in config or _as_text(config.get(key), "") == ""


def _with_daily_report_email_fallbacks(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(config or {})
    fallback = _load_email_fallback_config()
    key_map = {
        "upload_error_smtp_host": "daily_report_smtp_host",
        "upload_error_smtp_port": "daily_report_smtp_port",
        "upload_error_smtp_tls": "daily_report_smtp_tls",
        "upload_error_smtp_username": "daily_report_smtp_username",
        "upload_error_smtp_password": "daily_report_smtp_password",
        "upload_error_smtp_timeout_sec": "daily_report_smtp_timeout_sec",
        "upload_error_smtp_keychain_service": "daily_report_smtp_keychain_service",
    }
    for target_key, source_key in key_map.items():
        if _config_is_blank(merged, target_key) and source_key in fallback:
            merged[target_key] = fallback.get(source_key)
    return merged


def _read_stored_upload_error_smtp_password(*, account: str, service: str) -> str:
    try:
        from difra.gui.daily_valid_container_reporter import _read_stored_smtp_password

        return _read_stored_smtp_password(account=account, service=service)
    except Exception:
        return ""


def _workflow_value(workflow_result: Any, name: str, default: Any = "") -> Any:
    return getattr(workflow_result, name, default)


def build_matador_upload_error_email(
    *,
    config: Optional[Dict[str, Any]],
    workflow_result: Any,
    log_path: Path,
    context: str,
) -> EmailMessage:
    config = _with_daily_report_email_fallbacks(config)
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
    config = _with_daily_report_email_fallbacks(config)
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
    if username and not password:
        keychain_service = _as_text(
            _config_value(
                config,
                "upload_error_smtp_keychain_service",
                "DIFRA_UPLOAD_ERROR_SMTP_KEYCHAIN_SERVICE",
                DEFAULT_DAILY_REPORT_KEYCHAIN_SERVICE,
            ),
            DEFAULT_DAILY_REPORT_KEYCHAIN_SERVICE,
        )
        password = _read_stored_upload_error_smtp_password(
            account=username,
            service=keychain_service,
        )
    if username and not password:
        return {
            "sent": False,
            "skipped": True,
            "message": "upload error SMTP password not configured",
        }
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
