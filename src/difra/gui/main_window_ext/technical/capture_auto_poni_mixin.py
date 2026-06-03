from difra.gui.main_window_ext.technical.capture_auto_poni_config_mixin import (
    TechnicalCaptureAutoPoniConfigMixin,
)
from difra.gui.main_window_ext.technical.capture_auto_poni_workflow_mixin import (
    TechnicalCaptureAutoPoniWorkflowMixin,
)


def _tm():
    from difra.gui.main_window_ext.technical import capture_mixin

    return capture_mixin._tm()


class TechnicalCaptureAutoPoniMixin(
    TechnicalCaptureAutoPoniWorkflowMixin,
    TechnicalCaptureAutoPoniConfigMixin,
):
    pass
