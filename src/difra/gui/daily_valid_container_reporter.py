"""Daily valid session-container plot report email."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
import base64
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import smtplib
import socket
import subprocess
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple
import zipfile

import h5py
import numpy as np

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402


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
@dataclass(frozen=True)
class DetectorSeries:
    specimen_id: str
    detector_group: str
    detector_alias: str
    detector_name: str
    detector_side: str
    range_name: str
    q_range: Tuple[float, float]
    range_label: str
    range_assignment: str
    q: np.ndarray
    intensity: np.ndarray
    poni_text: str
    poni_source: str
    poni_sha256: str
    source_container: Path
    source_dataset: str

    @property
    def detector_key(self) -> str:
        return _safe_token(
            "_".join(
                item
                for item in (
                    self.detector_alias,
                    self.detector_group,
                    self.detector_name,
                )
                if item
            ),
            "detector",
        )


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


def _candidate_containers(roots: Iterable[Path], *, since: Optional[datetime]) -> List[Path]:
    paths: List[Path] = []
    min_mtime = since.timestamp() if since is not None else None
    for root in roots:
        folder = Path(root)
        if not folder.exists():
            continue
        for path in folder.rglob("*.h5"):
            name_upper = path.name.upper()
            if "H5OLD" in name_upper:
                continue
            if not (path.name.endswith(".nxs.h5") or path.name.endswith(".h5")):
                continue
            if min_mtime is not None and path.stat().st_mtime < min_mtime:
                continue
            paths.append(path)
    return sorted(set(paths))


def _container_report_datetime(path: Path) -> Optional[datetime]:
    try:
        with h5py.File(path, "r") as h5f:
            for attr_name in (
                "acquisition_date",
                "creation_timestamp",
                "created_at",
                "timestamp_start",
                "lock_timestamp",
                "archived_timestamp",
            ):
                text = _as_text(h5f.attrs.get(attr_name), "").strip()
                if not text:
                    continue
                for candidate in (
                    text,
                    text.replace("Z", ""),
                    text.replace(" ", "T"),
                ):
                    try:
                        return datetime.fromisoformat(candidate)
                    except Exception:
                        continue
    except Exception:
        return None
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime)
    except Exception:
        return None


def _filter_containers_for_date(container_paths: Iterable[Path], report_date: date) -> List[Path]:
    target = report_date.isoformat()
    selected: List[Path] = []
    for path in container_paths:
        stamp = _container_report_datetime(Path(path))
        if stamp is not None and stamp.date().isoformat() == target:
            selected.append(Path(path))
    return sorted(set(selected))


def _is_container_valid(h5f: h5py.File) -> Tuple[bool, str]:
    specimen = _as_text(h5f.attrs.get("specimenId", h5f.attrs.get("sample_id", "")))
    if not specimen:
        return False, "missing specimenId"

    transfer_status = _as_text(h5f.attrs.get("transfer_status", "")).lower()
    if transfer_status in {"not_complete", "failed", "error"}:
        return False, f"transfer_status={transfer_status}"

    container_state = _as_text(h5f.attrs.get("container_state", "")).lower()
    session_state = _as_text(h5f.attrs.get("session_state", "")).lower()
    bad_states = {"error", "failed", "rejected", "rejected_blocked"}
    if container_state in bad_states:
        return False, f"container_state={container_state}"
    if session_state in {"draft", "recovery_required"}:
        return False, f"session_state={session_state}"

    measurements_group = h5f.get("/entry/measurements")
    if measurements_group is None:
        return False, "missing /entry/measurements"

    processed_count = 0
    for point_group in measurements_group.values():
        if not isinstance(point_group, h5py.Group):
            continue
        for measurement_group in point_group.values():
            if not isinstance(measurement_group, h5py.Group):
                continue
            status = _as_text(measurement_group.attrs.get("measurement_status", "completed")).lower()
            if status in {"failed", "aborted"}:
                continue
            for det_group in measurement_group.values():
                if isinstance(det_group, h5py.Group) and "processed_signal" in det_group:
                    processed_count += 1
    if processed_count <= 0:
        return False, "no processed_signal measurements"
    return True, ""


def _container_distance_cm(h5f: h5py.File) -> Optional[float]:
    for attr_name in ("distance_cm", "distanceCm", "technical_distance_cm"):
        try:
            value = h5f.attrs.get(attr_name)
            if value not in (None, ""):
                return float(value)
        except Exception:
            continue
    return None


def _detector_group(alias: str, detector_name: str) -> str:
    token = f"{alias} {detector_name}".upper()
    if any(item in token for item in ("PRIMARY", "SAXS", "DET_PRIMARY", "DET_SAXS")):
        return "PRIMARY"
    if any(item in token for item in ("SECONDARY", "WAXS", "DET_SECONDARY", "DET_WAXS")):
        return "SECONDARY"
    return _safe_token(str(alias or detector_name or "DETECTOR").upper(), "DETECTOR")


def _detector_side_label(detector_group: str, alias: str, detector_name: str) -> str:
    token = f"{detector_group} {alias} {detector_name}".upper()
    if any(item in token for item in ("PRIMARY", "LEFT", "SAXS", "DET_PRIMARY", "DET_SAXS")):
        return "LEFT"
    if any(item in token for item in ("SECONDARY", "RIGHT", "WAXS", "DET_SECONDARY", "DET_WAXS")):
        return "RIGHT"
    return ""


def _detector_range_config(
    detector_group: str,
    alias: str,
    detector_name: str,
    distance_cm: Optional[float] = None,
) -> Tuple[str, Tuple[float, float], str, str]:
    if distance_cm is not None:
        try:
            distance = float(distance_cm)
        except Exception:
            distance = None
        if distance is not None and np.isfinite(distance):
            if distance >= SAXS_DISTANCE_THRESHOLD_CM:
                return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", f"distance_cm={distance:g}"
            return "WAXS", WAXS_RANGE, "WAXS 2-21 nm^-1", f"distance_cm={distance:g}"

    token = f"{detector_group} {alias} {detector_name}".upper()
    if any(item in token for item in ("SECONDARY", "WAXS", "RIGHT", "DET_SECONDARY", "DET_WAXS")):
        return "WAXS", WAXS_RANGE, "WAXS 2-21 nm^-1", "alias/name matched WAXS/SECONDARY"
    if any(item in token for item in ("PRIMARY", "SAXS", "LEFT", "DET_PRIMARY", "DET_SAXS")):
        return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", "alias/name matched SAXS/PRIMARY"
    return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", "default: alias/name did not identify SAXS or WAXS"


def _detector_sort_key(item: DetectorSeries) -> Tuple[int, str]:
    token = f"{item.detector_group} {item.detector_alias} {item.detector_side}".upper()
    if any(part in token for part in ("PRIMARY", "LEFT", "SAXS")):
        return (0, f"{item.detector_alias} {item.detector_name}")
    if any(part in token for part in ("SECONDARY", "RIGHT", "WAXS")):
        return (1, f"{item.detector_alias} {item.detector_name}")
    return (2, f"{item.detector_alias} {item.detector_name}")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _candidate_poni_infos(
    h5f: h5py.File,
    det_group: h5py.Group,
    alias: str,
) -> List[Tuple[str, str]]:
    candidates = []
    role = str(det_group.name.rsplit("/", 1)[-1])
    detector_id = _as_text(det_group.attrs.get("detector_id")).strip().lower()
    detector_alias = _as_text(det_group.attrs.get("detector_alias")).strip().lower()
    for attr_name in ("poni_ref", "poni_path"):
        ref = _as_text(det_group.attrs.get(attr_name)).strip()
        if not ref:
            continue
        candidates.append(ref)
        if not ref.startswith("/"):
            candidates.append(f"/entry/technical/poni/{ref}")
            candidates.append(f"/entry/technical/poni/poni_{ref}")
    if detector_id:
        candidates.extend(
            [
                f"/entry/technical/poni/poni_det_{detector_id}",
                f"/entry/technical/poni/poni_{detector_id}",
            ]
        )
    if role.startswith("det_"):
        candidates.extend(
            [
                f"/entry/technical/poni/poni_{role}",
            ]
        )
    if role.startswith("det_"):
        candidates.append(f"/entry/technical/poni/poni_{role[4:]}")
    tokens = {
        alias.strip().lower(),
        role.lower(),
        role.replace("det_", "").lower(),
        detector_alias,
        detector_id,
    }
    poni_group = h5f.get("/entry/technical/poni")
    if poni_group is not None:
        for name in sorted(poni_group.keys()):
            lower_name = name.lower()
            if any(token and token in lower_name for token in tokens):
                candidates.append(f"/entry/technical/poni/{name}")
    seen = set()
    found: List[Tuple[str, str]] = []
    for candidate in candidates:
        if not candidate or candidate in seen or candidate not in h5f:
            continue
        seen.add(candidate)
        text = _as_text(h5f[candidate][()]).strip()
        if text:
            found.append((text, candidate))
    return found


def _resolve_poni_info(h5f: h5py.File, det_group: h5py.Group, alias: str) -> Tuple[str, str]:
    candidates = _candidate_poni_infos(h5f, det_group, alias)
    if candidates:
        return candidates[0]
    return "", ""


def _resolve_poni_text(h5f: h5py.File, det_group: h5py.Group, alias: str) -> str:
    text, _source = _resolve_poni_info(h5f, det_group, alias)
    return text


def _report_image_name(specimen_id: str) -> str:
    return f"{_safe_token(specimen_id)}_detectors.png"


def _poni_arcname(item: DetectorSeries) -> str:
    if not item.poni_text.strip():
        return ""
    source_token = _safe_token(
        str(item.source_dataset or "").replace("/entry/measurements/", ""),
        "measurement",
    )
    hash_token = str(item.poni_sha256 or "")[:12] or "nohash"
    return (
        "poni/"
        f"{_safe_token(item.specimen_id)}_"
        f"{_safe_token(item.detector_group)}_"
        f"{_safe_token(item.detector_name)}_"
        f"{_safe_token(item.detector_side)}_"
        f"{source_token}_{hash_token}.poni"
    )


def _write_report_poni_files(series: Iterable[DetectorSeries], output_dir: Path) -> Dict[str, Path]:
    output = Path(output_dir)
    files: Dict[str, Path] = {}
    for item in series:
        arcname = _poni_arcname(item)
        if not arcname or arcname in files:
            continue
        path = output / arcname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item.poni_text, encoding="utf-8")
        files[arcname] = path
    return files


def build_report_manifest_diagnostics(
    series: Iterable[DetectorSeries],
    *,
    poni_files: Dict[str, Path],
) -> Dict[str, Any]:
    grouped: Dict[str, List[DetectorSeries]] = {}
    for item in series:
        grouped.setdefault(item.specimen_id, []).append(item)

    image_entries = []
    series_entries = []
    poni_entries = {}
    for specimen_id, items in sorted(grouped.items()):
        image_file = _report_image_name(specimen_id)
        detector_panels = []
        for detector_key in sorted({item.detector_key for item in items}):
            panel_items = [item for item in items if item.detector_key == detector_key]
            if not panel_items:
                continue
            first = sorted(panel_items, key=_detector_sort_key)[0]
            detector_panels.append(
                {
                    "detectorAlias": first.detector_alias,
                    "detectorName": first.detector_name,
                    "detectorGroup": first.detector_group,
                    "detectorSide": first.detector_side,
                    "rangeName": first.range_name,
                    "qRangeNm^-1": [float(first.q_range[0]), float(first.q_range[1])],
                    "rangeAssignment": first.range_assignment,
                    "seriesCount": len(panel_items),
                }
            )
        image_entries.append(
            {
                "imageFile": image_file,
                "specimenId": specimen_id,
                "layout": "one subplot per detector alias; PRIMARY/LEFT panels are ordered before SECONDARY/RIGHT panels",
                "detectorPanels": detector_panels,
                "seriesCount": len(items),
            }
        )
        for detector_key in sorted({item.detector_key for item in items}):
            panel_items = sorted(
                [item for item in items if item.detector_key == detector_key],
                key=lambda item: item.source_dataset,
            )
            for panel_index, item in enumerate(panel_items, start=1):
                poni_arcname = _poni_arcname(item)
                if poni_arcname:
                    poni_entries[poni_arcname] = {
                        "poniFile": poni_arcname,
                        "poniSource": item.poni_source,
                        "poniSha256": item.poni_sha256,
                        "presentInZip": poni_arcname in poni_files,
                    }
                side = f" {item.detector_side}" if item.detector_side else ""
                series_entries.append(
                    {
                        "imageFile": image_file,
                        "seriesIndex": panel_index,
                        "label": f"{item.detector_alias}{side} #{panel_index}",
                        "specimenId": item.specimen_id,
                        "detectorGroup": item.detector_group,
                        "detectorSide": item.detector_side,
                        "detectorAlias": item.detector_alias,
                        "detectorName": item.detector_name,
                        "rangeName": item.range_name,
                        "rangeAssignment": item.range_assignment,
                        "qRangeNm^-1": [float(item.q_range[0]), float(item.q_range[1])],
                        "sourceContainer": str(item.source_container),
                        "sourceDataset": item.source_dataset,
                        "poniSource": item.poni_source,
                        "poniFile": poni_arcname,
                        "poniSha256": item.poni_sha256,
                    }
                )
    return {
        "images": image_entries,
        "series": series_entries,
        "poniFiles": sorted(poni_entries.values(), key=lambda item: item["poniFile"]),
    }


def integrate_detector_signal(
    data: np.ndarray,
    poni_text: str,
    *,
    npt: int = DEFAULT_POINTS,
    q_range: Optional[Tuple[float, float]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or not poni_text.strip():
        return np.asarray([]), np.asarray([])
    try:
        from difra.gui.technical.analysis_compat import (
            initialize_azimuthal_integrator_poni_text,
        )

        ai = initialize_azimuthal_integrator_poni_text(poni_text)
        kwargs: Dict[str, Any] = {}
        if q_range is not None:
            kwargs["radial_range"] = (float(q_range[0]), float(q_range[1]))
        result = ai.integrate1d(
            arr,
            max(int(npt), 2),
            unit="q_nm^-1",
            error_model="azimuthal",
            **kwargs,
        )
        q = np.asarray(result.radial, dtype=float).reshape(-1)
        intensity = np.asarray(result.intensity, dtype=float).reshape(-1)
        finite = np.isfinite(q) & np.isfinite(intensity)
        return q[finite], intensity[finite]
    except Exception:
        return np.asarray([]), np.asarray([])


def _resample_range(
    q: np.ndarray,
    intensity: np.ndarray,
    q_range: Tuple[float, float],
    *,
    points: int,
) -> Tuple[np.ndarray, np.ndarray]:
    q = np.asarray(q, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    finite = np.isfinite(q) & np.isfinite(intensity)
    q = q[finite]
    intensity = intensity[finite]
    if q.size < 2:
        return np.asarray([]), np.asarray([])
    order = np.argsort(q)
    q = q[order]
    intensity = intensity[order]
    if q[0] > float(q_range[0]) or q[-1] < float(q_range[1]):
        return np.asarray([]), np.asarray([])
    mask = (q >= float(q_range[0])) & (q <= float(q_range[1]))
    if np.count_nonzero(mask) < 2:
        return np.asarray([]), np.asarray([])
    target_q = np.linspace(float(q_range[0]), float(q_range[1]), int(points))
    target_i = np.interp(target_q, q[mask], intensity[mask])
    return target_q, target_i


def _integrated_range_is_complete(
    q: np.ndarray,
    intensity: np.ndarray,
    q_range: Tuple[float, float],
    *,
    points: int,
) -> bool:
    q = np.asarray(q, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    if q.size != int(points) or intensity.size != int(points):
        return False
    if not np.all(np.isfinite(q)) or not np.all(np.isfinite(intensity)):
        return False
    q_min = float(np.nanmin(q))
    q_max = float(np.nanmax(q))
    return q_min >= float(q_range[0]) - 1e-6 and q_max <= float(q_range[1]) + 1e-6


def _integrated_signal_fraction(intensity: np.ndarray) -> float:
    values = np.asarray(intensity, dtype=float).reshape(-1)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0
    scale = float(np.nanmax(np.abs(finite)))
    if scale <= 0.0:
        return 0.0
    threshold = max(scale * 1e-6, 1e-12)
    return float(np.count_nonzero(np.abs(finite) > threshold) / finite.size)


def collect_report_series(
    container_paths: Iterable[Path],
    *,
    points: int = DEFAULT_POINTS,
) -> Tuple[List[DetectorSeries], List[str], int]:
    series: List[DetectorSeries] = []
    skipped: List[str] = []
    valid_count = 0
    for container_path in container_paths:
        path = Path(container_path)
        try:
            with h5py.File(path, "r") as h5f:
                is_valid, reason = _is_container_valid(h5f)
                if not is_valid:
                    skipped.append(f"{path.name}: {reason}")
                    continue
                valid_count += 1
                specimen_id = _as_text(
                    h5f.attrs.get("specimenId", h5f.attrs.get("sample_id", "unknown")),
                    "unknown",
                )
                distance_cm = _container_distance_cm(h5f)
                measurements_group = h5f.get("/entry/measurements")
                for point_name in sorted(measurements_group.keys()):
                    point_group = measurements_group[point_name]
                    if not isinstance(point_group, h5py.Group):
                        continue
                    for measurement_name in sorted(point_group.keys()):
                        measurement_group = point_group[measurement_name]
                        if not isinstance(measurement_group, h5py.Group):
                            continue
                        status = _as_text(
                            measurement_group.attrs.get("measurement_status", "completed")
                        ).lower()
                        if status in {"failed", "aborted"}:
                            continue
                        for det_name in sorted(measurement_group.keys()):
                            det_group = measurement_group[det_name]
                            if not isinstance(det_group, h5py.Group):
                                continue
                            if "processed_signal" not in det_group:
                                continue
                            alias = _as_text(
                                det_group.attrs.get(
                                    "detector_alias",
                                    str(det_name).replace("det_", "").upper(),
                                )
                            ).upper()
                            group_name = _detector_group(alias, str(det_name))
                            side = _detector_side_label(group_name, alias, str(det_name))
                            range_name, q_range, range_label, range_reason = (
                                _detector_range_config(
                                    group_name,
                                    alias,
                                    str(det_name),
                                    distance_cm=distance_cm,
                                )
                            )
                            signal = det_group["processed_signal"][()]
                            best: Optional[Tuple[str, str, np.ndarray, np.ndarray, float]] = None
                            for candidate_poni_text, candidate_poni_source in (
                                _candidate_poni_infos(h5f, det_group, alias) or [("", "")]
                            ):
                                q, intensity = integrate_detector_signal(
                                    signal,
                                    candidate_poni_text,
                                    npt=points,
                                    q_range=q_range,
                                )
                                if not _integrated_range_is_complete(
                                    q,
                                    intensity,
                                    q_range,
                                    points=points,
                                ):
                                    continue
                                signal_fraction = _integrated_signal_fraction(intensity)
                                candidate = (
                                    candidate_poni_text,
                                    candidate_poni_source,
                                    q,
                                    intensity,
                                    signal_fraction,
                                )
                                if best is None or signal_fraction > best[4]:
                                    best = candidate
                                if signal_fraction >= 0.5:
                                    break
                            if best is None:
                                skipped.append(
                                    f"{path.name}:{det_group.name}: no q data in {q_range[0]}-{q_range[1]} nm^-1"
                                )
                                continue
                            poni_text, poni_source, q, intensity, _signal_fraction = best
                            series.append(
                                DetectorSeries(
                                    specimen_id=specimen_id,
                                    detector_group=group_name,
                                    detector_alias=alias,
                                    detector_name=str(det_name),
                                    detector_side=side,
                                    range_name=range_name,
                                    q_range=q_range,
                                    range_label=range_label,
                                    range_assignment=range_reason,
                                    q=q,
                                    intensity=intensity,
                                    poni_text=poni_text,
                                    poni_source=poni_source,
                                    poni_sha256=_sha256_text(poni_text) if poni_text else "",
                                    source_container=path,
                                    source_dataset=det_group["processed_signal"].name,
                                )
                            )
        except Exception as exc:
            skipped.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return series, skipped, valid_count


def summarize_valid_containers(container_paths: Iterable[Path]) -> Dict[str, Any]:
    project_ids: List[str] = []
    valid_containers = 0
    matador_uploaded = 0
    for container_path in container_paths:
        try:
            with h5py.File(container_path, "r") as h5f:
                is_valid, _reason = _is_container_valid(h5f)
                if not is_valid:
                    continue
                valid_containers += 1
                project_id = _as_text(
                    h5f.attrs.get(
                        "matadorProjectId",
                        h5f.attrs.get("project_id", h5f.attrs.get("matadorProjectName", "")),
                    )
                )
                if project_id and project_id not in project_ids:
                    project_ids.append(project_id)
                upload_status = _as_text(h5f.attrs.get("upload_status", "")).lower()
                matador_send_status = _as_text(
                    h5f.attrs.get("matador_send_status", "")
                ).lower()
                transfer_status = _as_text(h5f.attrs.get("transfer_status", "")).lower()
                if (
                    upload_status == "success"
                    or matador_send_status == "successful"
                    or transfer_status == "sent"
                ):
                    matador_uploaded += 1
        except Exception:
            continue
    return {
        "projectIds": project_ids,
        "validContainers": valid_containers,
        "matadorUploaded": matador_uploaded,
    }


def render_report_images(
    series: Iterable[DetectorSeries],
    output_dir: Path,
    *,
    dpi: int = DEFAULT_DPI,
) -> List[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    grouped: Dict[str, List[DetectorSeries]] = {}
    for item in series:
        grouped.setdefault(item.specimen_id, []).append(item)

    images: List[Path] = []
    for specimen_id, items in sorted(grouped.items()):
        detector_keys = []
        for item in sorted(items, key=_detector_sort_key):
            if item.detector_key not in detector_keys:
                detector_keys.append(item.detector_key)
        panel_count = max(len(detector_keys), 1)
        ncols = min(panel_count, 3)
        nrows = int(np.ceil(panel_count / ncols))
        fig, axes = plt.subplots(
            nrows,
            ncols,
            figsize=(6.2 * ncols, 4.6 * nrows),
            dpi=dpi,
            squeeze=False,
        )
        for axis in axes.reshape(-1):
            axis.set_visible(False)
        for panel_index, detector_key in enumerate(detector_keys):
            ax = axes.reshape(-1)[panel_index]
            ax.set_visible(True)
            panel_items = [item for item in items if item.detector_key == detector_key]
            panel_items = sorted(panel_items, key=lambda item: item.source_dataset)
            if not panel_items:
                continue
            first = panel_items[0]
            q_range = tuple(first.q_range)
            for index, item in enumerate(panel_items, start=1):
                label = f"{item.detector_alias} #{index}"
                ax.plot(item.q, item.intensity, linewidth=1.1, alpha=0.85, label=label)
            side = f" ({first.detector_side})" if first.detector_side else ""
            ax.set_title(f"{first.detector_alias}{side} | {first.range_label}")
            ax.set_xlabel("q (nm^-1)")
            ax.set_ylabel("I(q)")
            ax.set_xlim(q_range)
            ax.grid(True, alpha=0.25)
            if len(panel_items) <= 12:
                ax.legend(fontsize=7)
        fig.suptitle(str(specimen_id), fontsize=12)
        fig.tight_layout()
        image_path = output / _report_image_name(specimen_id)
        fig.savefig(image_path, dpi=dpi)
        plt.close(fig)
        images.append(image_path)
    return images


def _no_report_images_email_result() -> Dict[str, Any]:
    return {
        "sent": False,
        "skipped": True,
        "message": "daily report has no PNG images; email not sent",
    }


def create_zip(
    zip_path: Path,
    image_paths: Iterable[Path],
    *,
    manifest: Dict[str, Any],
    extra_files: Optional[Dict[str, Path]] = None,
) -> Path:
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        for image_path in image_paths:
            path = Path(image_path)
            archive.write(path, arcname=path.name)
        for arcname, source_path in sorted((extra_files or {}).items()):
            path = Path(source_path)
            if path.exists():
                archive.write(path, arcname=str(arcname))
    return target


def create_simple_test_image_zip(output_dir: Path, *, dpi: int = DEFAULT_DPI) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    image_paths = []
    for name, q_range, fn in (
        ("test_PRIMARY_SAXS_1-3nm-1.png", SAXS_RANGE, lambda q: np.sin(q * 4.0) + 2.0),
        ("test_SECONDARY_WAXS_2-21nm-1.png", WAXS_RANGE, lambda q: np.cos(q * 3.0) + 2.0),
    ):
        q = np.linspace(float(q_range[0]), float(q_range[1]), DEFAULT_POINTS)
        y = fn(q)
        fig, ax = plt.subplots(figsize=(6, 4), dpi=dpi)
        ax.plot(q, y, linewidth=1.5)
        ax.set_xlabel("q (nm^-1)")
        ax.set_ylabel("I(q)")
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        image_path = output / name
        fig.savefig(image_path, dpi=dpi)
        plt.close(fig)
        image_paths.append(image_path)
    return create_zip(
        output / "difra_daily_report_test_images.zip",
        image_paths,
        manifest={"kind": "test", "imageCount": len(image_paths)},
    )


def build_daily_report_email(
    *,
    config: Optional[Dict[str, Any]],
    zip_path: Path,
    manifest: Dict[str, Any],
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
            "Attached ZIP contains 200 dpi PNG files and manifest.json.",
        ]
    )

    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(body)
    payload = Path(zip_path).read_bytes()
    message.add_attachment(
        payload,
        maintype="application",
        subtype="zip",
        filename=Path(zip_path).name,
    )
    return message


def send_daily_report_email(
    *,
    config: Optional[Dict[str, Any]],
    zip_path: Path,
    manifest: Dict[str, Any],
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


def build_daily_report(
    *,
    config: Optional[Dict[str, Any]],
    output_dir: Path,
    since: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
    tracking_started_at: Optional[str] = None,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
) -> DailyReportResult:
    cfg = dict(config or {})
    generated_at = datetime.now()
    period_end = period_end or generated_at
    roots = [
        Path(cfg.get("measurements_archive_folder") or ""),
        Path(cfg.get("measurements_folder") or ""),
    ]
    containers = _candidate_containers([root for root in roots if str(root)], since=since)
    result = DailyReportResult(scanned=len(containers))
    result.period_start = since.isoformat(timespec="seconds") if since else None
    result.period_end = period_end.isoformat(timespec="seconds")
    result.tracking_started_at = tracking_started_at
    series, skipped, valid_count = collect_report_series(containers, points=DEFAULT_POINTS)
    summary = summarize_valid_containers(containers)
    result.skipped.extend(skipped)
    result.valid_containers = valid_count
    out = Path(output_dir)
    image_dir = out / "images"
    result.images = render_report_images(series, image_dir, dpi=DEFAULT_DPI)
    poni_files = _write_report_poni_files(series, out)
    manifest = {
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "reportDate": generated_at.strftime("%Y-%m-%d"),
        "since": since.isoformat(timespec="seconds") if since else None,
        "periodStart": since.isoformat(timespec="seconds") if since else None,
        "periodEnd": period_end.isoformat(timespec="seconds"),
        "trackingStartedAt": tracking_started_at,
        "scanned": result.scanned,
        "validContainers": result.valid_containers,
        "projectIds": summary.get("projectIds", []),
        "matadorUploaded": int(summary.get("matadorUploaded", 0) or 0),
        "imageCount": len(result.images),
        "skipped": result.skipped[:200],
    }
    manifest.update(build_report_manifest_diagnostics(series, poni_files=poni_files))
    result.manifest = manifest
    result.zip_path = create_zip(
        out / f"difra_daily_valid_container_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
        result.images,
        manifest=manifest,
        extra_files=poni_files,
    )
    if send_email:
        if not result.images:
            result.email_result = _no_report_images_email_result()
        else:
            try:
                result.email_result = send_daily_report_email(
                    config=cfg,
                    zip_path=result.zip_path,
                    manifest=manifest,
                    allow_interactive_setup=allow_interactive_setup,
                )
            except Exception as exc:
                result.email_result = {
                    "sent": False,
                    "skipped": False,
                    "message": f"{type(exc).__name__}: {exc}",
                }
    return result


def build_daily_report_for_containers(
    *,
    config: Optional[Dict[str, Any]],
    container_paths: Iterable[Path],
    output_dir: Path,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
    report_date: Optional[date] = None,
    tracking_started_at: Optional[str] = None,
) -> DailyReportResult:
    cfg = dict(config or {})
    generated_at = datetime.now()
    paths = sorted({Path(path) for path in container_paths if Path(path).exists()})
    result = DailyReportResult(scanned=len(paths))
    result.period_start = None
    result.period_end = generated_at.isoformat(timespec="seconds")
    result.tracking_started_at = tracking_started_at
    series, skipped, valid_count = collect_report_series(paths, points=DEFAULT_POINTS)
    summary = summarize_valid_containers(paths)
    result.skipped.extend(skipped)
    result.valid_containers = valid_count
    out = Path(output_dir)
    image_dir = out / "images"
    result.images = render_report_images(series, image_dir, dpi=DEFAULT_DPI)
    poni_files = _write_report_poni_files(series, out)
    report_day = report_date or generated_at.date()
    manifest = {
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "reportDate": report_day.isoformat(),
        "since": None,
        "periodStart": None,
        "periodEnd": generated_at.isoformat(timespec="seconds"),
        "trackingStartedAt": tracking_started_at,
        "scanned": result.scanned,
        "validContainers": result.valid_containers,
        "projectIds": summary.get("projectIds", []),
        "matadorUploaded": int(summary.get("matadorUploaded", 0) or 0),
        "imageCount": len(result.images),
        "selectedContainers": [str(path) for path in paths],
        "skipped": result.skipped[:200],
    }
    manifest.update(build_report_manifest_diagnostics(series, poni_files=poni_files))
    result.manifest = manifest
    result.zip_path = create_zip(
        out / f"difra_selected_valid_container_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
        result.images,
        manifest=manifest,
        extra_files=poni_files,
    )
    if send_email:
        if not result.images:
            result.email_result = _no_report_images_email_result()
        else:
            try:
                result.email_result = send_daily_report_email(
                    config=cfg,
                    zip_path=result.zip_path,
                    manifest=manifest,
                    allow_interactive_setup=allow_interactive_setup,
                )
            except Exception as exc:
                result.email_result = {
                    "sent": False,
                    "skipped": False,
                    "message": f"{type(exc).__name__}: {exc}",
                }
    return result


def run_daily_report_from_config(
    *,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    since_days: float = 1.0,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
) -> DailyReportResult:
    config = load_report_config(config_path)
    base = output_dir
    if base is None:
        base_folder = config.get("difra_base_folder") or Path.home() / "difra"
        base = Path(base_folder) / "daily_reports"
    base = Path(base)
    period_end = datetime.now()
    fallback_since = period_end - timedelta(days=float(since_days))
    since = fallback_since
    state_path = _report_state_path(config, base)
    state: Dict[str, Any] = {}
    tracking_started_at: Optional[str] = None
    if send_email:
        state = _load_report_state(state_path)
        tracking_started = _parse_report_datetime(state.get("trackingStartedAt"))
        last_successful = _parse_report_datetime(state.get("lastSuccessfulUntil"))
        if last_successful is not None:
            since = last_successful
        elif tracking_started is not None:
            since = tracking_started
        else:
            tracking_started = fallback_since
            state["trackingStartedAt"] = tracking_started.isoformat(timespec="seconds")
        tracking_started_at = state.get("trackingStartedAt")

    result = build_daily_report(
        config=config,
        output_dir=base,
        since=since,
        period_end=period_end,
        tracking_started_at=tracking_started_at,
        send_email=send_email,
        allow_interactive_setup=allow_interactive_setup,
    )
    result.state_path = state_path if send_email else None
    if send_email:
        _append_report_attempt(
            state,
            result=result,
            manifest=result.manifest,
            email_result=result.email_result,
            period_start=since,
            period_end=period_end,
        )
        if result.email_result.get("sent"):
            state["lastSuccessfulUntil"] = period_end.isoformat(timespec="seconds")
            state["lastSuccessfulAt"] = datetime.now().isoformat(timespec="seconds")
            state["lastSuccessfulZipPath"] = str(result.zip_path or "")
        _write_report_state(state_path, state)
    return result


def run_daily_report_for_date_from_config(
    *,
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    report_date: date,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
    skip_if_sent: bool = True,
    resend_if_changed: bool = True,
    skip_if_no_containers: bool = True,
) -> DailyReportResult:
    cfg = load_report_config(config_path)
    if config:
        cfg.update(config)
    base = output_dir
    if base is None:
        base_folder = cfg.get("difra_base_folder") or Path.home() / "difra"
        base = Path(base_folder) / "daily_reports"
    base = Path(base)
    state_path = _report_state_path(cfg, base)
    state: Dict[str, Any] = _load_report_state(state_path) if send_email else {}
    by_date = state.get("byDate")
    if not isinstance(by_date, dict):
        by_date = {}
    date_key = report_date.isoformat()
    date_state = by_date.get(date_key) if isinstance(by_date.get(date_key), dict) else {}

    roots = [
        Path(cfg.get("measurements_archive_folder") or ""),
        Path(cfg.get("measurements_folder") or ""),
    ]
    all_containers = _candidate_containers([root for root in roots if str(root)], since=None)
    containers = _filter_containers_for_date(all_containers, report_date)
    fingerprint = hashlib.sha256(
        "\n".join(
            f"{path}:{path.stat().st_mtime_ns}:{path.stat().st_size}" for path in containers
        ).encode("utf-8")
    ).hexdigest()

    previous_image_count = _as_int(date_state.get("imageCount"), 0)
    if (
        send_email
        and skip_if_sent
        and date_state.get("sent") is True
        and previous_image_count > 0
    ):
        if not resend_if_changed or date_state.get("fingerprint") == fingerprint:
            result = DailyReportResult(scanned=len(containers))
            result.period_start = datetime.combine(report_date, time.min).isoformat(timespec="seconds")
            result.period_end = datetime.combine(report_date, time.max).isoformat(timespec="seconds")
            result.state_path = state_path
            result.email_result = {
                "sent": False,
                "skipped": True,
                "message": f"daily report already sent for {date_key}",
            }
            return result

    if send_email and skip_if_no_containers and not containers:
        result = DailyReportResult(scanned=0)
        result.period_start = datetime.combine(report_date, time.min).isoformat(timespec="seconds")
        result.period_end = datetime.combine(report_date, time.max).isoformat(timespec="seconds")
        result.state_path = state_path
        result.email_result = {
            "sent": False,
            "skipped": True,
            "message": f"no containers for {date_key}",
        }
        date_state.update(
            {
                "lastAttemptAt": datetime.now().isoformat(timespec="seconds"),
                "sent": False,
                "fingerprint": fingerprint,
                "message": result.email_result["message"],
            }
        )
        by_date[date_key] = date_state
        state["byDate"] = by_date
        _write_report_state(state_path, state)
        return result

    result = build_daily_report_for_containers(
        config=cfg,
        container_paths=containers,
        output_dir=base / date_key,
        send_email=send_email,
        allow_interactive_setup=allow_interactive_setup,
        report_date=report_date,
        tracking_started_at=state.get("trackingStartedAt"),
    )
    result.period_start = datetime.combine(report_date, time.min).isoformat(timespec="seconds")
    result.period_end = datetime.combine(report_date, time.max).isoformat(timespec="seconds")
    result.state_path = state_path if send_email else None
    if send_email:
        _append_report_attempt(
            state,
            result=result,
            manifest=result.manifest,
            email_result=result.email_result,
            period_start=datetime.combine(report_date, time.min),
            period_end=datetime.combine(report_date, time.max),
        )
        sent = bool(result.email_result.get("sent"))
        date_state.update(
            {
                "lastAttemptAt": datetime.now().isoformat(timespec="seconds"),
                "sent": sent,
                "fingerprint": fingerprint,
                "message": _as_text(result.email_result.get("message"), ""),
                "zipPath": str(result.zip_path or ""),
                "validContainers": int(result.valid_containers),
                "imageCount": len(result.images),
            }
        )
        if sent:
            date_state["lastSentAt"] = datetime.now().isoformat(timespec="seconds")
        by_date[date_key] = date_state
        state["byDate"] = by_date
        _write_report_state(state_path, state)
    return result


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
