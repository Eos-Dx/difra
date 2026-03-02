"""Top-level hardware package.

Provides access to difra and compatibility shims.
"""

from importlib import import_module

__all__ = ["difra", "xystages"]


def __getattr__(name):
    if name in __all__:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
