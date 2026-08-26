"""Email and credential helpers for daily valid-container reports."""

from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage
import getpass
import json
from pathlib import Path
import platform
import smtplib
import socket
import sys
import tempfile
from typing import Any, Dict, Iterable, Optional

from difra.gui.daily_report_common import (
    DEFAULT_DPI,
    DEFAULT_EMAIL_SETUP_PASSWORD,
    DEFAULT_ENCRYPTED_PASSWORD_PATH,
    DEFAULT_KEYCHAIN_SERVICE,
    DEFAULT_REPORT_RECIPIENT,
    DEFAULT_REPORT_SENDER,
    _as_bool,
    _as_email_recipients,
    _as_int,
    _as_text,
    _config_value,
    load_report_config,
)
from difra.gui.daily_report_credentials import (
    _decrypt_secret_blob,
    _delete_macos_keychain_password,
    _delete_windows_credential_password,
    _read_macos_keychain_password,
    _read_windows_credential_password,
    _write_macos_keychain_password,
    _write_windows_credential_password,
)
from difra.gui.daily_report_rendering import create_simple_test_image_zip


def _read_stored_smtp_password(*, account: str, service: str) -> str:
    if platform.system() == "Windows":
        return _read_windows_credential_password(account=account, service=service)
    return _read_macos_keychain_password(account=account, service=service)


def _write_stored_smtp_password(
    *,
    account: str,
    service: str,
    password: str,
) -> bool:
    if platform.system() == "Windows":
        return _write_windows_credential_password(
            account=account,
            service=service,
            password=password,
        )
    return _write_macos_keychain_password(
        account=account,
        service=service,
        password=password,
    )


def _delete_stored_smtp_password(*, account: str, service: str) -> bool:
    if platform.system() == "Windows":
        return _delete_windows_credential_password(account=account, service=service)
    return _delete_macos_keychain_password(account=account, service=service)


def _encrypted_password_path(config: Optional[Dict[str, Any]]) -> Path:
    configured = _as_text(
        _config_value(
            config,
            "daily_report_smtp_encrypted_password_path",
            "DIFRA_DAILY_REPORT_SMTP_ENCRYPTED_PASSWORD_PATH",
            "",
        )
    )
    if configured:
        return Path(configured)
    return DEFAULT_ENCRYPTED_PASSWORD_PATH


def _read_encrypted_bundled_password(
    *,
    config: Optional[Dict[str, Any]],
    passphrase: str,
) -> str:
    path = _encrypted_password_path(config)
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(blob, dict):
        return ""
    return _decrypt_secret_blob(blob, passphrase)


def _interactive_keychain_password_setup(
    *,
    config: Optional[Dict[str, Any]],
    account: str,
    service: str,
) -> str:
    setup_password = _as_text(
        _config_value(
            config,
            "daily_report_email_setup_password",
            "DIFRA_DAILY_REPORT_EMAIL_SETUP_PASSWORD",
            DEFAULT_EMAIL_SETUP_PASSWORD,
        ),
        DEFAULT_EMAIL_SETUP_PASSWORD,
    )
    entered_setup_password = getpass.getpass("Enter Ulster password to configure email: ")
    if entered_setup_password != setup_password:
        return ""
    smtp_password = _read_encrypted_bundled_password(
        config=config,
        passphrase=entered_setup_password,
    )
    if not smtp_password:
        smtp_password = getpass.getpass(f"Enter Gmail App Password for {account}: ")
    smtp_password = str(smtp_password or "").replace(" ", "").strip()
    if not smtp_password:
        return ""
    if not _write_stored_smtp_password(
        account=account,
        service=service,
        password=smtp_password,
    ):
        return ""
    return smtp_password


def ensure_daily_report_email_password_configured_gui(
    *,
    parent: Any = None,
    config_path: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = load_report_config(config_path)
    if config:
        cfg.update(config)
    if not _as_bool(
        _config_value(
            cfg,
            "daily_report_email_enabled",
            "DIFRA_DAILY_REPORT_EMAIL_ENABLED",
            True,
        ),
        True,
    ):
        return {"ok": True, "required": False, "message": "daily report email disabled"}

    smtp_host = _as_text(
        _config_value(
            cfg,
            "daily_report_smtp_host",
            "DIFRA_DAILY_REPORT_SMTP_HOST",
            "",
            fallback_key="upload_error_smtp_host",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_HOST",
        )
    )
    username = _as_text(
        _config_value(
            cfg,
            "daily_report_smtp_username",
            "DIFRA_DAILY_REPORT_SMTP_USERNAME",
            "",
            fallback_key="upload_error_smtp_username",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_USERNAME",
        )
    )
    if not smtp_host or not username:
        return {
            "ok": True,
            "required": False,
            "message": "daily report SMTP host or username not configured",
        }

    configured_password = _as_text(
        _config_value(
            cfg,
            "daily_report_smtp_password",
            "DIFRA_DAILY_REPORT_SMTP_PASSWORD",
            "",
            fallback_key="upload_error_smtp_password",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_PASSWORD",
        )
    )
    keychain_service = _as_text(
        _config_value(
            cfg,
            "daily_report_smtp_keychain_service",
            "DIFRA_DAILY_REPORT_SMTP_KEYCHAIN_SERVICE",
            DEFAULT_KEYCHAIN_SERVICE,
        ),
        DEFAULT_KEYCHAIN_SERVICE,
    )
    if configured_password or _read_stored_smtp_password(
        account=username,
        service=keychain_service,
    ):
        return {"ok": True, "required": False, "message": "daily report SMTP password configured"}

    try:
        from difra.gui.qt_compat import QInputDialog, QLineEdit, QMessageBox
    except Exception as exc:
        return {
            "ok": False,
            "required": True,
            "message": f"Qt password dialog unavailable: {type(exc).__name__}: {exc}",
        }

    setup_password = _as_text(
        _config_value(
            cfg,
            "daily_report_email_setup_password",
            "DIFRA_DAILY_REPORT_EMAIL_SETUP_PASSWORD",
            DEFAULT_EMAIL_SETUP_PASSWORD,
        ),
        DEFAULT_EMAIL_SETUP_PASSWORD,
    )
    entered_setup_password, ok = QInputDialog.getText(
        parent,
        "Daily Report Email",
        "Enter Ulster password to configure daily report email:",
        QLineEdit.Password,
    )
    if not ok:
        return {"ok": False, "required": True, "message": "daily report email setup cancelled"}
    if str(entered_setup_password or "") != setup_password:
        QMessageBox.warning(parent, "Daily Report Email", "Incorrect Ulster password.")
        return {"ok": False, "required": True, "message": "incorrect Ulster password"}

    smtp_password = _read_encrypted_bundled_password(
        config=cfg,
        passphrase=str(entered_setup_password or ""),
    )
    if not smtp_password:
        smtp_password, ok = QInputDialog.getText(
            parent,
            "Daily Report Email",
            f"Enter Gmail App Password for {username}:",
            QLineEdit.Password,
        )
        if not ok:
            return {
                "ok": False,
                "required": True,
                "message": "Gmail App Password entry cancelled",
            }
    smtp_password = str(smtp_password or "").replace(" ", "").strip()
    if not smtp_password:
        QMessageBox.warning(parent, "Daily Report Email", "SMTP password is empty.")
        return {"ok": False, "required": True, "message": "SMTP password is empty"}

    if not _write_stored_smtp_password(
        account=username,
        service=keychain_service,
        password=smtp_password,
    ):
        QMessageBox.critical(
            parent,
            "Daily Report Email",
            "Failed to save SMTP password in local credential storage.",
        )
        return {"ok": False, "required": True, "message": "failed to save SMTP password"}

    QMessageBox.information(
        parent,
        "Daily Report Email",
        "SMTP password saved. Daily reports can be sent automatically.",
    )
    return {
        "ok": True,
        "required": True,
        "message": "daily report SMTP password saved",
    }


def _no_report_images_email_result() -> Dict[str, Any]:
    return {
        "sent": False,
        "skipped": True,
        "message": "daily report has no PNG images; email not sent",
    }


def build_daily_report_email(
    *,
    config: Optional[Dict[str, Any]],
    zip_path: Path,
    manifest: Dict[str, Any],
    attachment_paths: Optional[Iterable[Path]] = None,
    test: bool = False,
) -> EmailMessage:
    recipients = _as_email_recipients(
        _config_value(
            config,
            "daily_report_email_recipient",
            "DIFRA_DAILY_REPORT_EMAIL_TO",
            DEFAULT_REPORT_RECIPIENT,
            fallback_key="upload_error_email_recipient",
            fallback_env_key="DIFRA_UPLOAD_ERROR_EMAIL_TO",
        ),
        DEFAULT_REPORT_RECIPIENT,
    )
    sender = _as_text(
        _config_value(
            config,
            "daily_report_email_sender",
            "DIFRA_DAILY_REPORT_EMAIL_FROM",
            DEFAULT_REPORT_SENDER,
            fallback_key="upload_error_email_sender",
            fallback_env_key="DIFRA_UPLOAD_ERROR_EMAIL_FROM",
        ),
        DEFAULT_REPORT_SENDER,
    )
    report_date = _as_text(manifest.get("reportDate"), "")
    if not report_date:
        generated = _as_text(manifest.get("generatedAt"), "")
        report_date = generated[:10] if len(generated) >= 10 else datetime.now().strftime("%Y-%m-%d")
    subject = f"DifraReport:{report_date}"
    if test:
        subject = f"{subject} TEST"

    project_ids = manifest.get("projectIds", [])
    if isinstance(project_ids, (list, tuple, set)):
        project_text = ", ".join(_as_text(item) for item in project_ids if _as_text(item))
    else:
        project_text = _as_text(project_ids, "-")
    if not project_text:
        project_text = "-"

    body = "\n".join(
        [
            "Daily DiFRA valid-container plot report.",
            "",
            f"Host: {socket.gethostname()}",
            f"Generated: {manifest.get('generatedAt', datetime.now().isoformat(timespec='seconds'))}",
            f"Period start: {manifest.get('periodStart') or manifest.get('since') or '-'}",
            f"Period end: {manifest.get('periodEnd') or '-'}",
            f"Tracking started: {manifest.get('trackingStartedAt') or '-'}",
            f"Project ID(s): {project_text}",
            f"Scanned: {manifest.get('scanned', 0)}",
            f"Containers: {manifest.get('validContainers', 0)} valid / {manifest.get('scanned', 0)} scanned",
            f"Successfully uploaded to Matador: {manifest.get('matadorUploaded', 0)}",
            f"Images: {manifest.get('imageCount', 0)}",
            "",
            f"ZIP report: {manifest.get('analystZip') or Path(zip_path).name}",
            "ZIP contains overview PNG, PONI QC PNG, and manifest.json.",
        ]
    )

    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(body)
    attachments = [Path(path) for path in (attachment_paths or []) if Path(path).exists()]
    if not attachments and zip_path:
        attachments = [Path(zip_path)]
    for attachment in attachments:
        payload = attachment.read_bytes()
        message.add_attachment(
            payload,
            maintype="application",
            subtype="zip" if attachment.suffix.lower() == ".zip" else "octet-stream",
            filename=attachment.name,
        )
    return message


def send_daily_report_email(
    *,
    config: Optional[Dict[str, Any]],
    zip_path: Path,
    manifest: Dict[str, Any],
    attachment_paths: Optional[Iterable[Path]] = None,
    test: bool = False,
    allow_interactive_setup: bool = False,
) -> Dict[str, Any]:
    enabled = _as_bool(
        _config_value(
            config,
            "daily_report_email_enabled",
            "DIFRA_DAILY_REPORT_EMAIL_ENABLED",
            True,
        ),
        True,
    )
    if not enabled:
        return {"sent": False, "skipped": True, "message": "daily report email disabled"}

    smtp_host = _as_text(
        _config_value(
            config,
            "daily_report_smtp_host",
            "DIFRA_DAILY_REPORT_SMTP_HOST",
            "",
            fallback_key="upload_error_smtp_host",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_HOST",
        )
    )
    if not smtp_host:
        return {"sent": False, "skipped": True, "message": "daily report SMTP host not configured"}

    smtp_port = _as_int(
        _config_value(
            config,
            "daily_report_smtp_port",
            "DIFRA_DAILY_REPORT_SMTP_PORT",
            587,
            fallback_key="upload_error_smtp_port",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_PORT",
        ),
        587,
    )
    username = _as_text(
        _config_value(
            config,
            "daily_report_smtp_username",
            "DIFRA_DAILY_REPORT_SMTP_USERNAME",
            "",
            fallback_key="upload_error_smtp_username",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_USERNAME",
        )
    )
    password = _as_text(
        _config_value(
            config,
            "daily_report_smtp_password",
            "DIFRA_DAILY_REPORT_SMTP_PASSWORD",
            "",
            fallback_key="upload_error_smtp_password",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_PASSWORD",
        )
    )
    if username and not password:
        keychain_service = _as_text(
            _config_value(
                config,
                "daily_report_smtp_keychain_service",
                "DIFRA_DAILY_REPORT_SMTP_KEYCHAIN_SERVICE",
                DEFAULT_KEYCHAIN_SERVICE,
            ),
            DEFAULT_KEYCHAIN_SERVICE,
        )
        password = _read_stored_smtp_password(
            account=username,
            service=keychain_service,
        )
        if not password and allow_interactive_setup and sys.stdin.isatty():
            password = _interactive_keychain_password_setup(
                config=config,
                account=username,
                service=keychain_service,
            )
    if username and not password:
        return {
            "sent": False,
            "skipped": True,
            "message": "daily report SMTP password not configured",
        }
    use_tls = _as_bool(
        _config_value(
            config,
            "daily_report_smtp_tls",
            "DIFRA_DAILY_REPORT_SMTP_TLS",
            True,
            fallback_key="upload_error_smtp_tls",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_TLS",
        ),
        True,
    )
    timeout_sec = float(
        _config_value(
            config,
            "daily_report_smtp_timeout_sec",
            "DIFRA_DAILY_REPORT_SMTP_TIMEOUT_SEC",
            10.0,
            fallback_key="upload_error_smtp_timeout_sec",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_TIMEOUT_SEC",
        )
    )

    message = build_daily_report_email(
        config=config,
        zip_path=Path(zip_path),
        manifest=manifest,
        attachment_paths=attachment_paths,
        test=test,
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
        "message": f"daily report email sent to {message['To']}",
    }


def send_simple_test_email(
    *,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    allow_interactive_setup: bool = False,
) -> Dict[str, Any]:
    config = load_report_config(config_path)
    if output_dir is None:
        tmp = tempfile.mkdtemp(prefix="difra_daily_report_test_")
        output = Path(tmp)
    else:
        output = Path(output_dir)
    zip_path = create_simple_test_image_zip(output, dpi=DEFAULT_DPI)
    manifest = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "reportDate": datetime.now().strftime("%Y-%m-%d"),
        "kind": "test",
        "scanned": 0,
        "validContainers": 0,
        "projectIds": [],
        "matadorUploaded": 0,
        "imageCount": 2,
    }
    return send_daily_report_email(
        config=config,
        zip_path=zip_path,
        manifest=manifest,
        test=True,
        allow_interactive_setup=allow_interactive_setup,
    )


def run_keychain_setup_self_test(
    *,
    config_path: Optional[Path] = None,
    service: str = "difra_daily_report_smtp_password_self_test",
) -> Dict[str, Any]:
    config = load_report_config(config_path)
    username = _as_text(
        _config_value(
            config,
            "daily_report_smtp_username",
            "DIFRA_DAILY_REPORT_SMTP_USERNAME",
            "",
            fallback_key="upload_error_smtp_username",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_USERNAME",
        )
    )
    if not username:
        return {"ok": False, "message": "daily report SMTP username not configured"}
    if not sys.stdin.isatty():
        return {"ok": False, "message": "interactive terminal required"}

    _delete_stored_smtp_password(account=username, service=service)
    password = _interactive_keychain_password_setup(
        config=config,
        account=username,
        service=service,
    )
    if not password:
        return {"ok": False, "message": "setup did not produce a password"}
    loaded = _read_stored_smtp_password(account=username, service=service)
    removed = _delete_stored_smtp_password(account=username, service=service)
    return {
        "ok": bool(loaded and loaded == password and removed),
        "account": username,
        "service": service,
        "decrypted": bool(password),
        "readBack": bool(loaded and loaded == password),
        "removed": bool(removed),
    }
