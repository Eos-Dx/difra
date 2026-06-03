"""Credential-storage and bundled-secret helpers for daily reports."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import subprocess
from typing import Any, Dict, Tuple


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
