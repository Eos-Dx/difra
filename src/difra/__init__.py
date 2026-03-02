"""Compatibility alias for legacy imports expecting hardware.EosDxDc.*

Maps to difra subpackages.
"""

import importlib

from ._local_dependency_aliases import bootstrap_local_dependency_aliases

__all__ = ["gui", "hardware", "utils"]


bootstrap_local_dependency_aliases()


def __getattr__(name):
    if name in __all__:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
