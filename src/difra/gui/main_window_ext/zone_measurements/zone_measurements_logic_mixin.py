from difra.gui.main_window_ext.zone_measurements.logic.file_mixin import (
    ZoneMeasurementsFileMixin,
)
from difra.gui.main_window_ext.zone_measurements.logic.process_mixin import (
    ZoneMeasurementsProcessMixin,
)
from difra.gui.main_window_ext.zone_measurements.logic.stage_control_mixin import (
    StageControlMixin,
)
from difra.gui.main_window_ext.zone_measurements.logic.ui_mixin import (
    ZoneMeasurementsUIMixin,
)
from difra.gui.main_window_ext.zone_measurements.logic.utils import (
    ZoneMeasurementsUtilsMixin,
)


class ZoneMeasurementsLogicMixin(
    ZoneMeasurementsUIMixin,
    StageControlMixin,
    ZoneMeasurementsProcessMixin,
    ZoneMeasurementsFileMixin,
    ZoneMeasurementsUtilsMixin,
):
    pass
