"""Legacy test-path wrapper for process_mixin."""

from difra.gui.main_window_ext.zone_measurements.logic import process_start_mixin as _start_impl
from difra.gui.main_window_ext.zone_measurements.logic.process_mixin import (
    QMessageBox,
    ZoneMeasurementsProcessMixin,
)

if hasattr(_start_impl, "_DEFAULT_PM"):
    _start_impl._pm = _start_impl._DEFAULT_PM
