"""Helpers for preparing pyFAI calibration review runs."""

from __future__ import annotations

from difra.gui.technical.pyfai_agbh_rings import (
    build_agbh_ring_overlays as build_agbh_ring_overlays,
    refine_poni_from_clicked_ring_points as refine_poni_from_clicked_ring_points,
    ring_two_theta_rad as ring_two_theta_rad,
    write_agbh_clicked_points_npt as write_agbh_clicked_points_npt,
    write_agbh_control_points_npt as write_agbh_control_points_npt,
    write_agbh_points_by_ring_npt as write_agbh_points_by_ring_npt,
)
from difra.gui.technical.pyfai_auto_poni_config import (
    auto_poni_default_config as auto_poni_default_config,
    auto_poni_distance_key as auto_poni_distance_key,
    auto_poni_seed_center_px as auto_poni_seed_center_px,
    auto_poni_seed_distance_cm as auto_poni_seed_distance_cm,
    normalized_auto_poni_config as normalized_auto_poni_config,
)
from difra.gui.technical.pyfai_calibration_common import (
    AGBH_D_SPACING_A as AGBH_D_SPACING_A,
    DEFAULT_CALIBRANT as DEFAULT_CALIBRANT,
    DEFAULT_ENERGY_KEV as DEFAULT_ENERGY_KEV,
    DEFAULT_WAVELENGTH_M as DEFAULT_WAVELENGTH_M,
    HeadlessPoniFitResult as HeadlessPoniFitResult,
    PyfaiCalib2Review as PyfaiCalib2Review,
    _format_float as _format_float,
    _safe_token as _safe_token,
    _to_float as _to_float,
    build_seed_poni_text as build_seed_poni_text,
    detector_size_px as detector_size_px,
    energy_kev_to_wavelength_m as energy_kev_to_wavelength_m,
    parse_poni_parameters as parse_poni_parameters,
    pixel_size_m as pixel_size_m,
    pyfai_detector_name as pyfai_detector_name,
)
from difra.gui.technical.pyfai_calibration_io import (
    _load_array_from_h5ref as _load_array_from_h5ref,
    build_pyfai_calib2_command as build_pyfai_calib2_command,
    export_calibration_image_for_pyfai as export_calibration_image_for_pyfai,
    load_calibration_array as load_calibration_array,
    write_pyfai_calib2_launcher as write_pyfai_calib2_launcher,
)
from difra.gui.technical.pyfai_calibration_review import (
    prepare_agbh_calib2_review as prepare_agbh_calib2_review,
)
from difra.gui.technical.pyfai_headless_fit import (
    is_headless_agbh_fit_plausible as is_headless_agbh_fit_plausible,
    run_headless_agbh_fit as run_headless_agbh_fit,
)

__all__ = [
    "AGBH_D_SPACING_A",
    "DEFAULT_CALIBRANT",
    "DEFAULT_ENERGY_KEV",
    "DEFAULT_WAVELENGTH_M",
    "HeadlessPoniFitResult",
    "PyfaiCalib2Review",
    "_format_float",
    "_safe_token",
    "_to_float",
    "_load_array_from_h5ref",
    "auto_poni_default_config",
    "auto_poni_distance_key",
    "auto_poni_seed_center_px",
    "auto_poni_seed_distance_cm",
    "build_agbh_ring_overlays",
    "build_pyfai_calib2_command",
    "build_seed_poni_text",
    "detector_size_px",
    "energy_kev_to_wavelength_m",
    "export_calibration_image_for_pyfai",
    "is_headless_agbh_fit_plausible",
    "load_calibration_array",
    "normalized_auto_poni_config",
    "parse_poni_parameters",
    "pixel_size_m",
    "prepare_agbh_calib2_review",
    "pyfai_detector_name",
    "refine_poni_from_clicked_ring_points",
    "ring_two_theta_rad",
    "run_headless_agbh_fit",
    "write_agbh_clicked_points_npt",
    "write_agbh_control_points_npt",
    "write_agbh_points_by_ring_npt",
    "write_pyfai_calib2_launcher",
]
