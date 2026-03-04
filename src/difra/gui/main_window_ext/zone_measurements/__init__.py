"""Lazy exports for zone measurements to avoid heavy import side-effects."""

__all__ = ["ZoneMeasurementsMixin"]


def __getattr__(name):
    if name == "ZoneMeasurementsMixin":
        from .zone_measurements_mixin import ZoneMeasurementsMixin

        return ZoneMeasurementsMixin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
