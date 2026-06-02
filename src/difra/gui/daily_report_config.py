"""Daily valid session-container plot report email."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import base64
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Any, Dict, List, Optional, Tuple


import matplotlib

matplotlib.use("Agg")


DEFAULT_REPORT_RECIPIENT = "sdenisov@matur.co.uk"
DEFAULT_REPORT_SENDER = "difra-upload@company.co.uk"
DEFAULT_POINTS = 100
DEFAULT_DPI = 200
DEFAULT_KEYCHAIN_SERVICE = "difra_daily_report_smtp_password"
DEFAULT_EMAIL_SETUP_PASSWORD = "Ulster2025!"
DEFAULT_ENCRYPTED_PASSWORD_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "secrets"
    / "daily_report_smtp_password.enc.json"
)
DEFAULT_EMAIL_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "config"
    / "daily_report_email.json"
)
DEFAULT_REPORT_STATE_FILENAME = "daily_report_state.json"
SAXS_RANGE = (1.0, 3.0)
WAXS_RANGE = (2.0, 21.0)
SAXS_DISTANCE_THRESHOLD_CM = 10.0
@dataclass
class DailyReportResult:
    scanned: int = 0
    valid_containers: int = 0
    skipped: List[str] = field(default_factory=list)
    images: List[Path] = field(default_factory=list)
    zip_path: Optional[Path] = None
    email_result: Dict[str, Any] = field(default_factory=dict)
    manifest: Dict[str, Any] = field(default_factory=dict)
    state_path: Optional[Path] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    tracking_started_at: Optional[str] = None


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


def _as_email_recipients(value: Any, default: str) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        text = _as_text(value, default)
        raw_items = str(text or "").replace(";", ",").split(",")
    recipients = []
    for item in raw_items:
        text = _as_text(item, "").strip()
        if text and text not in recipients:
            recipients.append(text)
    return recipients or [default]


def _config_value(
    config: Optional[Dict[str, Any]],
    key: str,
    env_key: str,
    default: Any,
    *,
    fallback_key: str = "",
    fallback_env_key: str = "",
) -> Any:
    if env_key in os.environ:
        return os.environ.get(env_key)
    if fallback_env_key and fallback_env_key in os.environ:
        return os.environ.get(fallback_env_key)
    if isinstance(config, dict) and key in config:
        return config.get(key)
    if fallback_key and isinstance(config, dict) and fallback_key in config:
        return config.get(fallback_key)
    return default


def _read_macos_keychain_password(*, account: str, service: str) -> str:
    if platform.system() != "Darwin":
        return ""
    account = str(account or "").strip()
    service = str(service or "").strip()
    if not account or not service:
        return ""
    try:
        completed = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                account,
                "-s",
                service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return str(completed.stdout or "").strip()


def _write_macos_keychain_password(
    *,
    account: str,
    service: str,
    password: str,
) -> bool:
    if platform.system() != "Darwin":
        return False
    account = str(account or "").strip()
    service = str(service or "").strip()
    password = str(password or "").strip()
    if not account or not service or not password:
        return False
    try:
        completed = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-a",
                account,
                "-s",
                service,
                "-w",
                password,
                "-U",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return completed.returncode == 0


def _delete_macos_keychain_password(*, account: str, service: str) -> bool:
    if platform.system() != "Darwin":
        return False
    account = str(account or "").strip()
    service = str(service or "").strip()
    if not account or not service:
        return False
    try:
        completed = subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-a",
                account,
                "-s",
                service,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return completed.returncode == 0


def _read_windows_credential_password(*, account: str, service: str) -> str:
    if platform.system() != "Windows":
        return ""
    service = str(service or "").strip()
    if not service:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        credential_ptr = ctypes.POINTER(CREDENTIAL)()
        if not ctypes.windll.advapi32.CredReadW(service, 1, 0, ctypes.byref(credential_ptr)):
            return ""
        try:
            credential = credential_ptr.contents
            size = int(credential.CredentialBlobSize or 0)
            if size <= 0:
                return ""
            payload = ctypes.string_at(credential.CredentialBlob, size)
            return payload.decode("utf-16-le", errors="ignore").rstrip("\x00").strip()
        finally:
            ctypes.windll.advapi32.CredFree(credential_ptr)
    except Exception:
        return ""


def _write_windows_credential_password(
    *,
    account: str,
    service: str,
    password: str,
) -> bool:
    if platform.system() != "Windows":
        return False
    account = str(account or "").strip()
    service = str(service or "").strip()
    password = str(password or "").strip()
    if not account or not service or not password:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        blob = password.encode("utf-16-le")
        blob_buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = CREDENTIAL()
        credential.Flags = 0
        credential.Type = 1
        credential.TargetName = service
        credential.Comment = "DiFRA daily report SMTP password"
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = blob_buffer
        credential.Persist = 2
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = account
        return bool(ctypes.windll.advapi32.CredWriteW(ctypes.byref(credential), 0))
    except Exception:
        return False


def _delete_windows_credential_password(*, account: str, service: str) -> bool:
    if platform.system() != "Windows":
        return False
    service = str(service or "").strip()
    if not service:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.advapi32.CredDeleteW(service, 1, 0))
    except Exception:
        return False


def _read_stored_smtp_password(*, account: str, service: str) -> str:
    if platform.system() == "Windows":
        return _daily_report_dependency("_read_windows_credential_password")(
            account=account,
            service=service,
        )
    return _daily_report_dependency("_read_macos_keychain_password")(
        account=account,
        service=service,
    )


def _write_stored_smtp_password(
    *,
    account: str,
    service: str,
    password: str,
) -> bool:
    if platform.system() == "Windows":
        return _daily_report_dependency("_write_windows_credential_password")(
            account=account,
            service=service,
            password=password,
        )
    return _daily_report_dependency("_write_macos_keychain_password")(
        account=account,
        service=service,
        password=password,
    )


def _delete_stored_smtp_password(*, account: str, service: str) -> bool:
    if platform.system() == "Windows":
        return _daily_report_dependency("_delete_windows_credential_password")(
            account=account,
            service=service,
        )
    return _daily_report_dependency("_delete_macos_keychain_password")(
        account=account,
        service=service,
    )


def _xor_bytes(payload: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(payload):
        block = hmac.new(
            key,
            nonce + counter.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(left ^ right for left, right in zip(payload, out))


def _derive_secret_keys(passphrase: str, salt: bytes, iterations: int) -> Tuple[bytes, bytes]:
    key_material = hashlib.pbkdf2_hmac(
        "sha256",
        str(passphrase or "").encode("utf-8"),
        salt,
        int(iterations),
        dklen=64,
    )
    return key_material[:32], key_material[32:]


def _blob_mac_payload(blob: Dict[str, Any]) -> bytes:
    payload = {
        key: blob[key]
        for key in (
            "version",
            "kdf",
            "iterations",
            "salt",
            "nonce",
            "ciphertext",
        )
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encrypt_secret_blob(secret: str, passphrase: str, *, iterations: int = 600_000) -> Dict[str, Any]:
    salt = os.urandom(16)
    nonce = os.urandom(16)
    enc_key, mac_key = _derive_secret_keys(passphrase, salt, iterations)
    ciphertext = _xor_bytes(str(secret or "").encode("utf-8"), enc_key, nonce)
    blob = {
        "version": 1,
        "kdf": "pbkdf2-sha256",
        "iterations": int(iterations),
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    mac = hmac.new(mac_key, _blob_mac_payload(blob), hashlib.sha256).digest()
    blob["mac"] = base64.b64encode(mac).decode("ascii")
    return blob


def _decrypt_secret_blob(blob: Dict[str, Any], passphrase: str) -> str:
    if int(blob.get("version", 0)) != 1:
        return ""
    if str(blob.get("kdf") or "") != "pbkdf2-sha256":
        return ""
    iterations = int(blob.get("iterations") or 0)
    if iterations < 100_000:
        return ""
    try:
        salt = base64.b64decode(str(blob.get("salt") or ""), validate=True)
        nonce = base64.b64decode(str(blob.get("nonce") or ""), validate=True)
        ciphertext = base64.b64decode(str(blob.get("ciphertext") or ""), validate=True)
        expected_mac = base64.b64decode(str(blob.get("mac") or ""), validate=True)
    except Exception:
        return ""
    enc_key, mac_key = _derive_secret_keys(passphrase, salt, iterations)
    actual_mac = hmac.new(mac_key, _blob_mac_payload(blob), hashlib.sha256).digest()
    if not hmac.compare_digest(actual_mac, expected_mac):
        return ""
    try:
        return _xor_bytes(ciphertext, enc_key, nonce).decode("utf-8").strip()
    except Exception:
        return ""


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
    smtp_password = _daily_report_dependency("_read_encrypted_bundled_password")(
        config=config,
        passphrase=entered_setup_password,
    )
    if not smtp_password:
        smtp_password = getpass.getpass(f"Enter Gmail App Password for {account}: ")
    smtp_password = str(smtp_password or "").replace(" ", "").strip()
    if not smtp_password:
        return ""
    if not _daily_report_dependency("_write_stored_smtp_password")(
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
    if configured_password or _daily_report_dependency("_read_stored_smtp_password")(
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

    smtp_password = _daily_report_dependency("_read_encrypted_bundled_password")(
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

    if not _daily_report_dependency("_write_stored_smtp_password")(
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


def _load_json_config(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return {}
    return {}


def load_report_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    candidates = []
    if config_path is not None:
        candidates.append(Path(config_path))
    root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            root / "resources" / "config" / "global.json",
            root / "resources" / "config" / "main.json",
        ]
    )
    config: Dict[str, Any] = {}
    base_path: Optional[Path] = None
    for candidate in candidates:
        loaded = _load_json_config(candidate)
        if loaded:
            config.update(loaded)
            base_path = Path(candidate)
            break

    overlay_candidates = []
    env_overlay = os.environ.get("DIFRA_DAILY_REPORT_EMAIL_CONFIG")
    if env_overlay:
        overlay_candidates.append(Path(env_overlay))
    if base_path is not None:
        overlay_candidates.append(base_path.parent / "daily_report_email.json")
    overlay_candidates.append(DEFAULT_EMAIL_CONFIG_PATH)

    seen = set()
    for candidate in overlay_candidates:
        resolved = str(Path(candidate).expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        config.update(_load_json_config(Path(candidate).expanduser()))
    return config


def _parse_report_datetime(value: Any) -> Optional[datetime]:
    text = _as_text(value, "")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _report_state_path(config: Optional[Dict[str, Any]], output_dir: Path) -> Path:
    configured = _as_text(
        _config_value(
            config,
            "daily_report_state_path",
            "DIFRA_DAILY_REPORT_STATE_PATH",
            "",
        )
    )
    if configured:
        return Path(configured).expanduser()
    return Path(output_dir) / DEFAULT_REPORT_STATE_FILENAME


def _load_report_state(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_report_state(path: Path, state: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _append_report_attempt(
    state: Dict[str, Any],
    *,
    result: "DailyReportResult",
    manifest: Dict[str, Any],
    email_result: Dict[str, Any],
    period_start: datetime,
    period_end: datetime,
) -> None:
    attempts = state.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempts.append(
        {
            "attemptedAt": datetime.now().isoformat(timespec="seconds"),
            "periodStart": period_start.isoformat(timespec="seconds"),
            "periodEnd": period_end.isoformat(timespec="seconds"),
            "sent": bool(email_result.get("sent")),
            "skipped": bool(email_result.get("skipped")),
            "message": _as_text(email_result.get("message"), ""),
            "zipPath": str(result.zip_path or ""),
            "scanned": int(result.scanned),
            "validContainers": int(result.valid_containers),
            "imageCount": len(result.images),
            "projectIds": manifest.get("projectIds", []),
            "matadorUploaded": int(manifest.get("matadorUploaded", 0) or 0),
        }
    )
    state["attempts"] = attempts[-200:]


def _safe_token(value: str, fallback: str = "unknown") -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    token = "_".join(part for part in token.split("_") if part)
    return token or fallback


def _daily_report_dependency(name: str):
    from difra.gui import daily_valid_container_reporter

    return getattr(daily_valid_container_reporter, name)
