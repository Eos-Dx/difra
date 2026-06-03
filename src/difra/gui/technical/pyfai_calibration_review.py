"""pyFAI calib2 review preparation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from difra.gui.technical.pyfai_agbh_rings import write_agbh_control_points_npt
from difra.gui.technical.pyfai_calibration_common import (
    DEFAULT_CALIBRANT,
    PyfaiCalib2Review,
    _safe_token,
    build_seed_poni_text,
)
from difra.gui.technical.pyfai_calibration_io import (
    build_pyfai_calib2_command,
    export_calibration_image_for_pyfai,
)


def prepare_agbh_calib2_review(
    *,
    source_image: str | Path,
    detector_config: Mapping | None,
    distance_m: float,
    alias: str = "",
    output_dir: str | Path | None = None,
    existing_poni_text: str = "",
    wavelength_m: float | None = None,
    calibrant: str = DEFAULT_CALIBRANT,
    center_px: tuple[float, float] | None = None,
    first_visible_ring: int | None = None,
    rings_to_show: int = 4,
    output_prefix: str | None = None,
) -> PyfaiCalib2Review:
    prefix = (
        _safe_token(output_prefix, _safe_token(alias, "detector"))
        if output_prefix
        else ""
    )
    output_root = Path(output_dir) if output_dir is not None else None
    image_path = export_calibration_image_for_pyfai(
        source_image,
        output_dir=output_root,
        alias=alias,
        output_stem=prefix or None,
    )
    if output_root is None:
        output_root = image_path.parent
    output_root.mkdir(parents=True, exist_ok=True)

    poni_text = build_seed_poni_text(
        detector_config=detector_config,
        distance_m=float(distance_m),
        alias=alias,
        existing_poni_text=existing_poni_text,
        wavelength_m=None if wavelength_m is None else float(wavelength_m),
        center_px=center_px,
    )
    if prefix:
        poni_path = output_root / f"{prefix}.poni"
    else:
        poni_path = (
            output_root
            / f"{_safe_token(image_path.stem)}_{_safe_token(alias, 'detector')}_seed.poni"
        )
    poni_path.write_text(poni_text, encoding="utf-8")
    command = build_pyfai_calib2_command(
        image_path=image_path,
        poni_text=poni_text,
        detector_config=detector_config,
        calibrant=calibrant,
    )
    if first_visible_ring is not None:
        if prefix:
            npt_path = output_root / f"{prefix}.npt"
        else:
            npt_path = (
                output_root
                / f"{_safe_token(image_path.stem)}_{_safe_token(alias, 'detector')}_seed.npt"
            )
        write_agbh_control_points_npt(
            poni_text=poni_text,
            detector_config=detector_config,
            output_path=npt_path,
            first_visible_ring=int(first_visible_ring),
            rings_to_show=int(rings_to_show),
            calibrant=calibrant,
        )
        command = [*command[:-1], "-n", str(npt_path), command[-1]]
    return PyfaiCalib2Review(
        image_path=image_path,
        poni_path=poni_path,
        command=command,
        poni_text=poni_text,
        source_path=None
        if str(source_image).startswith("h5ref://")
        else Path(source_image),
    )
