"""Legacy test-path wrapper for process_start_mixin."""

from difra.gui.main_window_ext.zone_measurements.logic import process_start_mixin as _impl

_pm = _impl._pm
_impl._pm = lambda: _pm()
ZoneMeasurementsProcessStartMixin = _impl.ZoneMeasurementsProcessStartMixin
