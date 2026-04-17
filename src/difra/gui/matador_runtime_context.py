"""Helpers for keeping Matador runtime credentials in memory only."""

from __future__ import annotations

import os
from typing import Any, Dict

from difra.gui.matador_upload_api import (
    normalize_matador_base_url,
    normalize_matador_token,
)

DEFAULT_MATADOR_URL = "https://portal.matur.co.uk"


def get_runtime_matador_context(owner: Any) -> Dict[str, str]:
    """Return the in-memory Matador context attached to a window-like owner."""
    context = {}
    if owner is not None:
        context = getattr(owner, "_matador_runtime_context", None) or {}
    token = str(context.get("token") or "").strip()
    url = str(context.get("matador_url") or "").strip()
    if not url:
        cfg = getattr(owner, "config", None)
        if isinstance(cfg, dict):
            url = str(cfg.get("matador_url") or "").strip()
    if not url:
        url = str(os.environ.get("MATADOR_URL") or "").strip()
    return {
        "token": normalize_matador_token(token),
        "matador_url": normalize_matador_base_url(url or DEFAULT_MATADOR_URL),
    }


def set_runtime_matador_context(
    owner: Any,
    *,
    token: str | None = None,
    matador_url: str | None = None,
) -> Dict[str, str]:
    """Persist the runtime Matador context on the owner without touching config files."""
    if owner is None:
        return {
            "token": normalize_matador_token(token or ""),
            "matador_url": normalize_matador_base_url(
                str(matador_url or DEFAULT_MATADOR_URL).strip()
                or DEFAULT_MATADOR_URL
            ),
        }
    context = dict(getattr(owner, "_matador_runtime_context", None) or {})
    if token is not None:
        context["token"] = normalize_matador_token(token or "")
    if matador_url is not None:
        context["matador_url"] = normalize_matador_base_url(
            str(matador_url or DEFAULT_MATADOR_URL).strip() or DEFAULT_MATADOR_URL
        )
    setattr(owner, "_matador_runtime_context", context)
    return get_runtime_matador_context(owner)
