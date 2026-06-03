"""Technical H5 loading/table population responsibilities."""

from difra.gui.technical.analysis_compat import detect_faulty_pixel_masks
from difra.gui.main_window_ext.technical.h5_loading_core_mixin import H5LoadingCoreMixin
from difra.gui.main_window_ext.technical.h5_loading_extract_mixin import H5LoadingExtractMixin
from difra.gui.main_window_ext.technical.h5_loading_preview_mixin import H5LoadingPreviewMixin
from difra.gui.main_window_ext.technical.h5_loading_runtime_rows_mixin import H5LoadingRuntimeRowsMixin
from difra.gui.main_window_ext.technical.h5_loading_startup_mixin import H5LoadingStartupMixin
from difra.gui.main_window_ext.technical.h5_loading_table_mixin import H5LoadingTableMixin


class H5ManagementLoadingMixin(
    H5LoadingTableMixin,
    H5LoadingPreviewMixin,
    H5LoadingExtractMixin,
    H5LoadingRuntimeRowsMixin,
    H5LoadingStartupMixin,
    H5LoadingCoreMixin,
):
    RUNTIME_ROWS_SIGNATURE_ATTR = "technical_aux_rows_signature"
    PONI_SIGNATURE_ATTR = "technical_poni_signature"
