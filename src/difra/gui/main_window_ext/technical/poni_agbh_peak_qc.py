"""Non-blocking AgBH azimuthal peak QC for technical PONI files."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Mapping, Optional

import numpy as np


FALLBACK_AGBH_D_SPACING_A = (
    58.38,
    29.19,
    19.46,
    14.595,
    11.676,
    9.73,
    8.34,
    7.2975,
    6.4867,
    5.838,
    5.3073,
    4.865,
    4.491,
    4.17,
    3.892,
    3.6488,
    3.433,
    3.2433,
    3.0737,
    2.919,
)


def _to_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _qc_enabled(config: Mapping | None) -> bool:
    cfg = config if isinstance(config, Mapping) else {}
    return bool(cfg.get("enabled", False))


def _alias_key(alias: str) -> str:
    return str(alias or "").strip().upper()


def agbh_theoretical_q_nm(calibrant: str = "AgBh") -> np.ndarray:
    d_spacing_a = None
    try:
        from pyFAI.calibrant import get_calibrant

        cal = get_calibrant(str(calibrant or "AgBh"))
        d_spacing_a = getattr(cal, "dspacing", None)
        if d_spacing_a is None:
            d_spacing_a = getattr(cal, "dSpacing", None)
    except Exception:
        d_spacing_a = None

    try:
        has_d_spacing = d_spacing_a is not None and len(d_spacing_a) > 0
    except Exception:
        has_d_spacing = False
    if not has_d_spacing:
        d_spacing_a = FALLBACK_AGBH_D_SPACING_A

    d_nm = np.asarray(list(d_spacing_a), dtype=float) * 0.1
    d_nm = d_nm[np.isfinite(d_nm) & (d_nm > 0)]
    if d_nm.size == 0:
        return np.asarray([], dtype=float)
    q_nm = 2.0 * math.pi / d_nm
    return np.asarray(sorted(float(value) for value in q_nm if np.isfinite(value)))


def _moving_average(values: np.ndarray, width: int = 3) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size < max(int(width), 2):
        return arr
    kernel = np.ones(int(width), dtype=float) / float(width)
    return np.convolve(arr, kernel, mode="same")


def evaluate_peak_alignment(
    q_values,
    intensity,
    theoretical_q,
    *,
    q_min: float | None = None,
    q_max: float | None = None,
    peak_window_nm_inv: float = 0.20,
    peak_shift_warning_nm_inv: float = 0.25,
    min_checked_peaks: int = 4,
) -> dict:
    q = np.asarray(q_values, dtype=float).reshape(-1)
    i = np.asarray(intensity, dtype=float).reshape(-1)
    finite = np.isfinite(q) & np.isfinite(i)
    q = q[finite]
    i = i[finite]
    if q.size < 5 or i.size < 5:
        return {"ok": False, "reason": "not_enough_profile_points", "checked": 0}

    order = np.argsort(q)
    q = q[order]
    i = _moving_average(i[order], width=3)

    q0s = np.asarray(theoretical_q, dtype=float).reshape(-1)
    q0s = q0s[np.isfinite(q0s)]
    lower = float(q_min) if q_min is not None else float(np.nanmin(q))
    upper = float(q_max) if q_max is not None else float(np.nanmax(q))
    q0s = q0s[(q0s >= lower) & (q0s <= upper)]

    checked = []
    for q0 in q0s:
        mask = (q >= float(q0) - float(peak_window_nm_inv)) & (
            q <= float(q0) + float(peak_window_nm_inv)
        )
        if int(np.count_nonzero(mask)) < 3:
            continue
        local_q = q[mask]
        local_i = i[mask]
        peak_index = int(np.nanargmax(local_i))
        peak_q = float(local_q[peak_index])
        checked.append(
            {
                "expected_q": float(q0),
                "peak_q": peak_q,
                "shift": abs(peak_q - float(q0)),
            }
        )

    if len(checked) < int(min_checked_peaks):
        return {
            "ok": False,
            "reason": "not_enough_agbh_peaks_checked",
            "checked": len(checked),
        }

    bad = [
        item
        for item in checked
        if float(item["shift"]) > float(peak_shift_warning_nm_inv)
    ]
    max_shift = max(float(item["shift"]) for item in checked) if checked else 0.0
    mean_shift = float(np.mean([float(item["shift"]) for item in checked])) if checked else 0.0
    return {
        "ok": not bad,
        "reason": "peak_shift",
        "checked": len(checked),
        "bad": len(bad),
        "max_shift": max_shift,
        "mean_shift": mean_shift,
        "worst": max(checked, key=lambda item: float(item["shift"])) if checked else None,
    }


def _q_range_for_alias(alias: str, config: Mapping | None):
    cfg = config if isinstance(config, Mapping) else {}
    ranges = cfg.get("q_ranges_by_alias") or cfg.get("q_ranges") or {}
    if not isinstance(ranges, Mapping):
        return None, None
    value = ranges.get(_alias_key(alias)) or ranges.get(str(alias or "").strip())
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None, None
    return _to_float(value[0]), _to_float(value[1])


def _integrate_profile(image, poni_text: str, config: Mapping | None):
    cfg = config if isinstance(config, Mapping) else {}
    npt = int(_to_float(cfg.get("npt")) or 600)
    npt = max(npt, 50)
    try:
        from difra.gui.technical.analysis_compat import (
            initialize_azimuthal_integrator_poni_text,
        )

        ai = initialize_azimuthal_integrator_poni_text(str(poni_text or ""))
        result = ai.integrate1d(
            np.asarray(image, dtype=float),
            npt,
            unit="q_nm^-1",
            error_model="azimuthal",
        )
        return np.asarray(result.radial, dtype=float), np.asarray(result.intensity, dtype=float), ""
    except Exception as exc:
        return None, None, str(exc)


def _format_warning(label: str, alias: str, evaluation: Mapping) -> str:
    reason = str(evaluation.get("reason") or "")
    if reason == "peak_shift":
        worst = evaluation.get("worst") if isinstance(evaluation.get("worst"), Mapping) else {}
        return (
            f"AgBH peak QC warning: {label} {alias}: "
            f"{int(evaluation.get('bad') or 0)}/{int(evaluation.get('checked') or 0)} "
            f"peaks outside tolerance; max shift {float(evaluation.get('max_shift') or 0):.3f} nm^-1 "
            f"near q={float(worst.get('expected_q') or 0):.3f} nm^-1"
        )
    return (
        f"AgBH peak QC warning: {label} {alias}: {reason or 'failed'} "
        f"(checked {int(evaluation.get('checked') or 0)} peaks)"
    )


def evaluate_agbh_peak_qc_for_series(
    series: Iterable[Mapping],
    *,
    validation_config: Mapping | None,
) -> list[str]:
    if not _qc_enabled(validation_config):
        return []

    cfg = validation_config if isinstance(validation_config, Mapping) else {}
    theoretical_q = agbh_theoretical_q_nm(str(cfg.get("calibrant") or "AgBh"))
    if theoretical_q.size == 0:
        return []

    window = float(_to_float(cfg.get("peak_window_nm_inv")) or 0.20)
    tolerance = float(_to_float(cfg.get("peak_shift_warning_nm_inv")) or 0.25)
    min_peaks = int(_to_float(cfg.get("min_checked_peaks")) or 4)
    warn_on_unavailable = bool(cfg.get("warn_on_unavailable", False))
    warnings: list[str] = []

    for item in series or ():
        alias = _alias_key(str(item.get("alias") or ""))
        image = item.get("image")
        poni_text = str(item.get("poni_text") or "")
        label = str(item.get("label") or "technical")
        if not alias or image is None or not poni_text.strip():
            continue

        q, intensity, error = _integrate_profile(image, poni_text, cfg)
        if q is None or intensity is None:
            if warn_on_unavailable:
                warnings.append(f"AgBH peak QC skipped: {label} {alias}: {error}")
            continue

        q_min, q_max = _q_range_for_alias(alias, cfg)
        result = evaluate_peak_alignment(
            q,
            intensity,
            theoretical_q,
            q_min=q_min,
            q_max=q_max,
            peak_window_nm_inv=window,
            peak_shift_warning_nm_inv=tolerance,
            min_checked_peaks=min_peaks,
        )
        if not bool(result.get("ok", False)):
            warnings.append(_format_warning(label, alias, result))

    return warnings


def _decode_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def evaluate_agbh_peak_qc_for_h5(
    container_path: Path | str,
    *,
    schema,
    validation_config: Mapping | None,
) -> list[str]:
    if not _qc_enabled(validation_config):
        return []

    import h5py

    path = Path(container_path)
    series = []
    with h5py.File(path, "r") as h5f:
        poni_by_alias = {}
        poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
        if poni_group is not None:
            for name, ds in poni_group.items():
                alias = _alias_key(
                    ds.attrs.get(getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias"), "")
                    or str(name).replace("poni_", "")
                )
                if alias:
                    poni_by_alias[alias] = _decode_text(ds[()])

        technical_group = h5f.get(schema.GROUP_TECHNICAL)
        if technical_group is None:
            return []

        for event_name in sorted(technical_group.keys()):
            event_group = technical_group[event_name]
            event_type = _decode_text(
                event_group.attrs.get("type")
                or event_group.attrs.get(getattr(schema, "ATTR_TECHNICAL_TYPE", "technical_type"))
            ).upper()
            if event_type != "AGBH":
                continue
            for det_name in sorted(event_group.keys()):
                det_group = event_group[det_name]
                if schema.DATASET_PROCESSED_SIGNAL not in det_group:
                    continue
                alias = _alias_key(
                    det_group.attrs.get(getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias"), "")
                    or det_name.replace("det_", "")
                )
                poni_text = poni_by_alias.get(alias, "")
                if not poni_text:
                    poni_ref = _decode_text(
                        det_group.attrs.get(getattr(schema, "ATTR_PONI_REF", "poni_ref"))
                        or det_group.attrs.get("poni_path")
                    ).strip()
                    if poni_ref and poni_ref in h5f:
                        poni_text = _decode_text(h5f[poni_ref][()])
                series.append(
                    {
                        "label": f"{path.name}:{event_name}",
                        "alias": alias,
                        "image": det_group[schema.DATASET_PROCESSED_SIGNAL][()],
                        "poni_text": poni_text,
                    }
                )

    return evaluate_agbh_peak_qc_for_series(
        series,
        validation_config=validation_config,
    )


def evaluate_agbh_peak_qc_for_aux_measurements(
    aux_measurements: Mapping,
    poni_data: Mapping,
    *,
    validation_config: Mapping | None,
) -> list[str]:
    if not _qc_enabled(validation_config):
        return []

    from difra.gui.technical.capture import _load_measurement_array

    agbh_map = {}
    for key, value in dict(aux_measurements or {}).items():
        if str(key or "").strip().upper() == "AGBH" and isinstance(value, Mapping):
            agbh_map = value
            break
    if not agbh_map:
        return []

    series = []
    for alias, entry in dict(agbh_map).items():
        if not isinstance(entry, Mapping):
            entry = {"file_path": entry}
        file_path = str(entry.get("file_path") or "").strip()
        if not file_path:
            continue
        try:
            image = _load_measurement_array(file_path)
        except Exception:
            continue
        payload = dict(poni_data or {}).get(alias) or dict(poni_data or {}).get(_alias_key(alias))
        if isinstance(payload, (list, tuple)) and payload:
            poni_text = str(payload[0] or "")
        else:
            poni_text = str(payload or "")
        series.append(
            {
                "label": Path(file_path).name,
                "alias": str(alias),
                "image": image,
                "poni_text": poni_text,
            }
        )

    return evaluate_agbh_peak_qc_for_series(
        series,
        validation_config=validation_config,
    )
