from __future__ import annotations

import numpy as np
import seaborn as sns
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from difra.gui.qt_compat import (
    QObject as QObject,
    QTimer as QTimer,
    pyqtSignal as pyqtSignal,
)
from difra.gui.qt_compat import (
    QCheckBox as QCheckBox,
    QDialog as QDialog,
    QDialogButtonBox as QDialogButtonBox,
    QHBoxLayout as QHBoxLayout,
    QInputDialog as QInputDialog,
    QLabel as QLabel,
    QLineEdit as QLineEdit,
    QMessageBox as QMessageBox,
    QPlainTextEdit as QPlainTextEdit,
    QSpinBox as QSpinBox,
    QVBoxLayout as QVBoxLayout,
)
from difra.gui.technical.analysis_compat import (
    create_mask as create_mask,
    initialize_azimuthal_integrator_df as initialize_azimuthal_integrator_df,
    initialize_azimuthal_integrator_poni_text as initialize_azimuthal_integrator_poni_text,
)
from difra.gui.technical.auto_poni_review_dialog import (
    show_auto_poni_review_window as show_auto_poni_review_window,
)
from difra.gui.technical.capture_acquisition_worker import (
    CaptureWorker as CaptureWorker,
)
from difra.gui.technical.capture_io import (
    _decode_h5_text as _decode_h5_text,
    _dsc_candidates as _dsc_candidates,
    _inspect_embedded_poni as _inspect_embedded_poni,
    _load_measurement_array as _load_measurement_array,
    _place_raw_capture_file as _place_raw_capture_file,
    validate_folder as validate_folder,
)
from difra.gui.technical.capture_measurement_view import (
    _build_measurement_dialog as _build_measurement_dialog,
    _format_measurement_diagnostics as _format_measurement_diagnostics,
    compute_hf_score_from_cake as compute_hf_score_from_cake,
    show_measurement_window as show_measurement_window,
)
from difra.gui.technical.poni_center_preview_window import (
    _PONI_RANGE_EDIT_PASSWORD as _PONI_RANGE_EDIT_PASSWORD,
    _load_json_payload as _load_json_payload,
    _resolve_poni_validation_config_target as _resolve_poni_validation_config_target,
    _save_poni_validation_rule_edits as _save_poni_validation_rule_edits,
    show_poni_centers_preview_window as show_poni_centers_preview_window,
)

__all__ = [
    "np",
    "sns",
    "FigureCanvas",
    "Figure",
    "QObject",
    "QTimer",
    "pyqtSignal",
    "QCheckBox",
    "QDialog",
    "QDialogButtonBox",
    "QHBoxLayout",
    "QInputDialog",
    "QLabel",
    "QLineEdit",
    "QMessageBox",
    "QPlainTextEdit",
    "QSpinBox",
    "QVBoxLayout",
    "create_mask",
    "initialize_azimuthal_integrator_df",
    "initialize_azimuthal_integrator_poni_text",
    "show_auto_poni_review_window",
    "CaptureWorker",
    "validate_folder",
    "show_measurement_window",
    "show_poni_centers_preview_window",
    "compute_hf_score_from_cake",
    "_decode_h5_text",
    "_dsc_candidates",
    "_inspect_embedded_poni",
    "_load_measurement_array",
    "_place_raw_capture_file",
    "_build_measurement_dialog",
    "_format_measurement_diagnostics",
    "_PONI_RANGE_EDIT_PASSWORD",
    "_load_json_payload",
    "_resolve_poni_validation_config_target",
    "_save_poni_validation_rule_edits",
]
