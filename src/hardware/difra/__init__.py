"""Compatibility alias for legacy imports expecting hardware.EosDxDc.*

Maps to hardware.difra subpackages.
"""

import importlib

__all__ = ["gui", "hardware", "utils"]


def __getattr__(name):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
