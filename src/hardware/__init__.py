"""Compatibility alias for legacy imports expecting top-level `hardware`."""

from __future__ import annotations

import importlib
import sys

_IMPL = importlib.import_module("difra.hardware")

__all__ = getattr(_IMPL, "__all__", [])
__path__ = list(getattr(_IMPL, "__path__", []))


def __getattr__(name):
    try:
        return getattr(_IMPL, name)
    except AttributeError:
        module = importlib.import_module(f"difra.hardware.{name}")
        sys.modules[f"{__name__}.{name}"] = module
        return module
