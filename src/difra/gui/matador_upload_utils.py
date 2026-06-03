from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import parse as urllib_parse

_DEFAULT_MATADOR_CACHE_PATH = (
    Path(__file__).resolve().parent.parent
    / "resources"
    / "config"
    / "matador_cache.json"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        while True:
            chunk = file_handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_token(value: Optional[str], fallback: str = "unknown") -> str:
    token = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(value or "")
    ).strip("_")
    return token or fallback


def _strip_trailing_slash(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def _strip_wrapping_quotes(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1].strip()
    return text


def normalize_matador_base_url(value: str) -> str:
    text = _strip_wrapping_quotes(value)
    if not text:
        return ""
    parsed = urllib_parse.urlparse(text)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return _strip_trailing_slash(text)


def normalize_matador_token(value: str) -> str:
    text = _strip_wrapping_quotes(value)
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text


def _normalize_iso_date(value: Any) -> str:
    text = _strip_wrapping_quotes(value)
    if not text:
        return ""
    candidate = text[:10]
    try:
        time.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return ""
    return candidate


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def default_matador_cache_path() -> Path:
    return _DEFAULT_MATADOR_CACHE_PATH


def load_matador_reference_cache(cache_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(cache_path or default_matador_cache_path())
    if not path.exists():
        return {"studies": [], "machines": [], "savedAt": ""}
    with open(path, "r", encoding="utf-8") as file_handle:
        payload = json.load(file_handle)
    studies = payload.get("studies")
    machines = payload.get("machines")
    return {
        "studies": studies if isinstance(studies, list) else [],
        "machines": machines if isinstance(machines, list) else [],
        "savedAt": _as_text(payload.get("savedAt")),
    }


def save_matador_reference_cache(
    *,
    studies: List[Dict[str, Any]],
    machines: List[Dict[str, Any]],
    cache_path: Optional[Path] = None,
) -> Path:
    path = Path(cache_path or default_matador_cache_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "studies": studies,
        "machines": machines,
    }
    with open(path, "w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, indent=2, ensure_ascii=False)
    return path
