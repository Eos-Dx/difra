"""Helpers for keeping Matador runtime credentials in memory only."""

from __future__ import annotations

import os
from typing import Any, Dict

try:
    from PyQt5.QtCore import QSettings
except Exception:  # pragma: no cover - PyQt should exist in app runtime.
    QSettings = None

from difra.gui.matador_upload_api import (
    normalize_matador_base_url,
    normalize_matador_token,
)

_MATADOR_URL_SETTINGS_KEY = "matador/url"


def _settings_matador_url() -> str:
    if QSettings is None:
        return ""
    try:
        settings = QSettings("EOSDx", "DiFRA")
        return str(settings.value(_MATADOR_URL_SETTINGS_KEY, "", type=str) or "").strip()
    except Exception:
        return ""


def _save_settings_matador_url(value: str) -> None:
    if QSettings is None:
        return
    try:
        settings = QSettings("EOSDx", "DiFRA")
        text = str(value or "").strip()
        if text:
            settings.setValue(_MATADOR_URL_SETTINGS_KEY, text)
        else:
            settings.remove(_MATADOR_URL_SETTINGS_KEY)
        settings.sync()
    except Exception:
        return


def _resolved_default_matador_url(owner: Any) -> str:
    url = ""
    if owner is not None:
        context = getattr(owner, "_matador_runtime_context", None) or {}
        url = str(context.get("matador_url") or "").strip()
    if not url:
        url = _settings_matador_url()
    if not url and owner is not None:
        cfg = getattr(owner, "config", None)
        if isinstance(cfg, dict):
            url = str(cfg.get("matador_url") or "").strip()
    if not url:
        url = str(os.environ.get("MATADOR_URL") or "").strip()
    return normalize_matador_base_url(url)


def get_runtime_matador_context(owner: Any) -> Dict[str, str]:
    """Return the in-memory Matador context attached to a window-like owner."""
    context = {}
    if owner is not None:
        context = getattr(owner, "_matador_runtime_context", None) or {}
    token = str(context.get("token") or "").strip()
    return {
        "token": normalize_matador_token(token),
        "matador_url": _resolved_default_matador_url(owner),
    }


def set_runtime_matador_context(
    owner: Any,
    *,
    token: str | None = None,
    matador_url: str | None = None,
) -> Dict[str, str]:
    """Persist the runtime Matador context on the owner without touching config files."""
    normalized_token = normalize_matador_token(token or "")
    normalized_url = (
        normalize_matador_base_url(str(matador_url or "").strip())
        if matador_url is not None
        else ""
    )

    if owner is None:
        if matador_url is not None:
            _save_settings_matador_url(normalized_url)
        return {
            "token": normalized_token,
            "matador_url": normalized_url or _resolved_default_matador_url(None),
        }
    context = dict(getattr(owner, "_matador_runtime_context", None) or {})
    if token is not None:
        context["token"] = normalized_token
    if matador_url is not None:
        context["matador_url"] = normalized_url
        _save_settings_matador_url(normalized_url)
    setattr(owner, "_matador_runtime_context", context)
    return get_runtime_matador_context(owner)
