"""PONI QC extraction and rendering for analyst reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import h5py
import numpy as np

import matplotlib

matplotlib.use("Agg")
from matplotlib import patches  # noqa: E402
from matplotlib import pyplot as plt  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402

from difra.gui.daily_report_common import DEFAULT_DPI, _as_text, _safe_token
from difra.gui.daily_report_integration import integrate_detector_signal
from difra.gui.daily_report_models import PoniQcPanel
from difra.gui.daily_report_series import (
    _candidate_poni_infos,
    _container_distance_cm,
    _container_operator_id,
    _detector_group,
    _detector_side_label,
)
from difra.gui.technical.agbh_peak_qc_service import agbh_theoretical_q_nm
from difra.gui.technical.pyfai_agbh_rings import build_agbh_ring_overlays
from difra.gui.technical.pyfai_auto_poni_config import normalized_auto_poni_config
from difra.gui.technical.pyfai_calibration_common import parse_poni_parameters


def _decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _distance_key(distance_cm: Optional[float]) -> str:
    try:
        value = float(distance_cm)
    except Exception:
        return ""
    if not np.isfinite(value) or value <= 0.0:
        return ""
    return "17" if value >= 10.0 else "2"


def _poni_distance_cm(poni_text: str) -> float:
    params = parse_poni_parameters(poni_text)
    try:
        return float(params.get("Distance", 0.0)) * 100.0
    except Exception:
        return 0.0


def _detector_config_from_poni(poni_text: str) -> Mapping | None:
    params = parse_poni_parameters(poni_text)
    detector_config = params.get("Detector_config")
    return detector_config if isinstance(detector_config, Mapping) else None


def _technical_event_is_agbh(event_name: str, event_group: h5py.Group) -> bool:
    tokens = [
        event_name,
        _decode_text(event_group.attrs.get("type")),
        _decode_text(event_group.attrs.get("technical_type")),
        _decode_text(event_group.attrs.get("measurement_type")),
        _decode_text(event_group.attrs.get("sample_type")),
    ]
    return any("AGBH" in token.upper() for token in tokens)


def _detector_is_agbh(det_group: h5py.Group) -> bool:
    tokens = [
        _decode_text(det_group.attrs.get("source_file")),
        _decode_text(det_group.attrs.get("file_path")),
        _decode_text(det_group.attrs.get("measurement_type")),
    ]
    return any("AGBH" in token.upper() for token in tokens)


def _iter_agbh_detector_groups(h5f: h5py.File):
    technical = h5f.get("/entry/technical")
    if not isinstance(technical, h5py.Group):
        return
    for event_name in sorted(technical.keys()):
        if event_name == "poni":
            continue
        event_group = technical[event_name]
        if not isinstance(event_group, h5py.Group):
            continue
        event_is_agbh = _technical_event_is_agbh(event_name, event_group)
        for det_name in sorted(event_group.keys()):
            det_group = event_group[det_name]
            if not isinstance(det_group, h5py.Group):
                continue
            if "processed_signal" not in det_group:
                continue
            if event_is_agbh or _detector_is_agbh(det_group):
                yield event_name, det_name, det_group


def _integrate_cake(
    data: np.ndarray,
    poni_text: str,
    *,
    q_range: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not str(poni_text or "").strip():
        return np.asarray([]), np.asarray([]), np.asarray([[]])
    try:
        from difra.gui.technical.analysis_compat import (
            initialize_azimuthal_integrator_poni_text,
        )

        ai = initialize_azimuthal_integrator_poni_text(str(poni_text or ""))
        result = ai.integrate2d(
            np.asarray(data, dtype=float),
            220,
            96,
            unit="q_nm^-1",
            radial_range=(float(q_range[0]), float(q_range[1])),
            error_model="azimuthal",
        )
        if hasattr(result, "intensity"):
            intensity = np.asarray(result.intensity, dtype=float)
            q = np.asarray(result.radial, dtype=float)
            chi = np.asarray(result.azimuthal, dtype=float)
        else:
            intensity = np.asarray(result[0], dtype=float)
            q = np.asarray(result[1], dtype=float)
            chi = np.asarray(result[2], dtype=float)
        return q.reshape(-1), chi.reshape(-1), intensity
    except Exception:
        return np.asarray([]), np.asarray([]), np.asarray([[]])


def collect_poni_qc_panels(
    container_paths: Iterable[Path],
    *,
    config: Optional[Mapping[str, Any]] = None,
    points: int = 600,
) -> list[PoniQcPanel]:
    panels: list[PoniQcPanel] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for container_path in sorted({Path(path) for path in container_paths if Path(path).exists()}):
        try:
            with h5py.File(container_path, "r") as h5f:
                operator_id = _container_operator_id(h5f)
                container_distance = _container_distance_cm(h5f)
                for event_name, det_name, det_group in _iter_agbh_detector_groups(h5f) or ():
                    alias = _as_text(
                        det_group.attrs.get(
                            "detector_alias",
                            str(det_name).replace("det_", "").upper(),
                        )
                    ).upper()
                    detector_group = _detector_group(alias, str(det_name))
                    side = _detector_side_label(detector_group, alias, str(det_name))
                    candidates = _candidate_poni_infos(h5f, det_group, alias)
                    poni_text, poni_source = candidates[0] if candidates else ("", "")
                    distance_cm = _poni_distance_cm(poni_text) or float(container_distance or 0.0)
                    distance_key = _distance_key(distance_cm)
                    if distance_key not in {"2", "17"}:
                        continue
                    q_range = (2.0, 21.0) if distance_key == "2" else (1.0, 4.0)
                    data = np.asarray(det_group["processed_signal"][()])
                    q, intensity = integrate_detector_signal(
                        data,
                        poni_text,
                        npt=points,
                        q_range=q_range,
                    )
                    cake_q, cake_chi, cake_i = _integrate_cake(
                        data,
                        poni_text,
                        q_range=q_range,
                    )
                    key = (
                        str(container_path),
                        event_name,
                        str(det_group.name),
                        distance_key,
                        detector_group,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    panels.append(
                        PoniQcPanel(
                            operator_id=operator_id,
                            source_container=container_path,
                            source_dataset=det_group["processed_signal"].name,
                            detector_group=detector_group,
                            detector_alias=alias,
                            detector_name=str(det_name),
                            detector_side=side,
                            distance_key=distance_key,
                            distance_cm=float(distance_cm),
                            poni_distance_cm=_poni_distance_cm(poni_text),
                            poni_text=poni_text,
                            poni_source=poni_source,
                            data=data,
                            q=q,
                            intensity=intensity,
                            cake_q=cake_q,
                            cake_chi=cake_chi,
                            cake_intensity=cake_i,
                        )
                    )
        except Exception:
            continue
    return panels


def _panel_column(panel: PoniQcPanel) -> int:
    offset = 0 if panel.distance_key == "2" else 2
    side = 1 if str(panel.detector_group).upper() == "SECONDARY" else 0
    return offset + side


def _q_range_for_distance(distance_key: str) -> tuple[float, float]:
    return (2.0, 21.0) if distance_key == "2" else (1.0, 4.0)


def _first_ring_for_panel(panel: PoniQcPanel, config: Mapping[str, Any] | None) -> int:
    cfg = normalized_auto_poni_config(config or {})
    by_distance = cfg.get("first_visible_ring_by_distance_cm", {})
    alias_key = str(panel.detector_group or panel.detector_alias or "").upper()
    try:
        return int((by_distance.get(panel.distance_key, {}) or {}).get(alias_key) or 1)
    except Exception:
        return 1


def _rings_to_show_for_panel(panel: PoniQcPanel, config: Mapping[str, Any] | None) -> int:
    cfg = normalized_auto_poni_config(config or {})
    by_distance = cfg.get("rings_to_search_by_distance_cm", {})
    alias_key = str(panel.detector_group or panel.detector_alias or "").upper()
    try:
        return max(1, int((by_distance.get(panel.distance_key, {}) or {}).get(alias_key) or cfg.get("rings_to_show", 3)))
    except Exception:
        return 3


def _plot_heatmap(ax, panel: PoniQcPanel, *, config: Mapping[str, Any] | None) -> None:
    data = np.asarray(panel.data, dtype=float)
    positive = data[np.isfinite(data) & (data > 0)]
    norm = None
    if positive.size:
        vmin = max(float(np.nanpercentile(positive, 2)), 1e-12)
        vmax = float(np.nanpercentile(positive, 99.8))
        if vmax > vmin:
            norm = LogNorm(vmin=vmin, vmax=vmax)
    ax.imshow(data, cmap="viridis", origin="upper", norm=norm)
    overlays = build_agbh_ring_overlays(
        poni_text=panel.poni_text,
        detector_config=_detector_config_from_poni(panel.poni_text),
        first_visible_ring=_first_ring_for_panel(panel, config),
        rings_to_show=_rings_to_show_for_panel(panel, config),
    )
    for overlay in overlays:
        circle = patches.Circle(
            (float(overlay["center_col_px"]), float(overlay["center_row_px"])),
            float(overlay["radius_px"]),
            fill=False,
            edgecolor="white",
            linewidth=0.7,
            alpha=0.9,
        )
        ax.add_patch(circle)
    ax.set_xticks([])
    ax.set_yticks([])


def _plot_cake(ax, panel: PoniQcPanel) -> None:
    q = np.asarray(panel.cake_q, dtype=float)
    chi = np.asarray(panel.cake_chi, dtype=float)
    intensity = np.asarray(panel.cake_intensity, dtype=float)
    if q.size < 2 or chi.size < 2 or intensity.ndim != 2 or intensity.size == 0:
        ax.text(0.5, 0.5, "no cake", ha="center", va="center", transform=ax.transAxes, fontsize=6)
        return
    positive = intensity[np.isfinite(intensity) & (intensity > 0)]
    norm = None
    if positive.size:
        vmin = max(float(np.nanpercentile(positive, 2)), 1e-12)
        vmax = float(np.nanpercentile(positive, 99.5))
        if vmax > vmin:
            norm = LogNorm(vmin=vmin, vmax=vmax)
    ax.imshow(
        intensity,
        extent=(float(np.nanmin(q)), float(np.nanmax(q)), float(np.nanmin(chi)), float(np.nanmax(chi))),
        aspect="auto",
        origin="lower",
        cmap="magma",
        norm=norm,
    )
    ax.set_ylabel("chi", fontsize=6)
    ax.tick_params(labelsize=5)


def _plot_profile(ax, panel: PoniQcPanel) -> None:
    q = np.asarray(panel.q, dtype=float)
    intensity = np.asarray(panel.intensity, dtype=float)
    finite = np.isfinite(q) & np.isfinite(intensity)
    if np.count_nonzero(finite) >= 2:
        ax.plot(q[finite], intensity[finite], linewidth=0.9, color="#1f77b4")
    q_min, q_max = _q_range_for_distance(panel.distance_key)
    for q0 in agbh_theoretical_q_nm("AgBh"):
        if q_min <= float(q0) <= q_max:
            ax.axvline(float(q0), color="black", linewidth=0.45, alpha=0.65)
    ax.set_xlim(q_min, q_max)
    ax.set_yscale("log")
    ax.grid(True, alpha=0.18)
    ax.tick_params(labelsize=5)
    ax.set_xlabel("q (nm^-1)", fontsize=6)
    ax.set_ylabel("I(q)", fontsize=6)


def render_poni_qc_images_by_operator(
    panels: Iterable[PoniQcPanel],
    output_dir: Path,
    *,
    config: Optional[Mapping[str, Any]] = None,
    dpi: int = DEFAULT_DPI,
) -> dict[str, Path]:
    grouped: dict[str, list[PoniQcPanel]] = {}
    for panel in panels:
        grouped.setdefault(panel.operator_id or "unknown", []).append(panel)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    columns = [
        ("2 cm PRIMARY", "2", "PRIMARY"),
        ("2 cm SECONDARY", "2", "SECONDARY"),
        ("17 cm PRIMARY", "17", "PRIMARY"),
        ("17 cm SECONDARY", "17", "SECONDARY"),
    ]
    for operator_id, items in sorted(grouped.items()):
        rows = max(1, max((len([p for p in items if _panel_column(p) == col]) for col in range(4)), default=0))
        fig, axes = plt.subplots(
            rows * 3,
            4,
            figsize=(18, max(7.0, 2.35 * rows * 3)),
            dpi=dpi,
            squeeze=False,
        )
        by_col: dict[int, list[PoniQcPanel]] = {idx: [] for idx in range(4)}
        for panel in sorted(items, key=lambda p: (p.source_container.name, p.detector_group, p.source_dataset)):
            by_col[_panel_column(panel)].append(panel)
        for col, (title, _dist, _det) in enumerate(columns):
            axes[0][col].set_title(title, fontsize=9)
        for col in range(4):
            for row in range(rows):
                panel = by_col[col][row] if row < len(by_col[col]) else None
                heat_ax = axes[row * 3][col]
                cake_ax = axes[row * 3 + 1][col]
                profile_ax = axes[row * 3 + 2][col]
                if panel is None:
                    for ax in (heat_ax, cake_ax, profile_ax):
                        ax.text(0.5, 0.5, "blank", ha="center", va="center", transform=ax.transAxes, fontsize=7)
                        ax.set_xticks([])
                        ax.set_yticks([])
                    continue
                label = (
                    f"{Path(panel.source_container).stem[:22]} | "
                    f"{panel.detector_group} | {panel.poni_distance_cm:.3g} cm"
                )
                _plot_heatmap(heat_ax, panel, config=config)
                heat_ax.set_title(label, fontsize=6, pad=2)
                _plot_cake(cake_ax, panel)
                _plot_profile(profile_ax, panel)
        fig.suptitle(f"DiFRA PONI QC | operator: {operator_id}", fontsize=11, y=0.995)
        fig.subplots_adjust(left=0.045, right=0.99, top=0.93, bottom=0.035, hspace=0.42, wspace=0.22)
        path = output / f"poni_qc_{_safe_token(operator_id, 'operator')}.png"
        fig.savefig(path, dpi=dpi)
        plt.close(fig)
        paths[operator_id] = path
    return paths


def build_poni_qc_manifest(panels: Iterable[PoniQcPanel]) -> list[dict[str, Any]]:
    rows = []
    for panel in panels:
        rows.append(
            {
                "operatorId": panel.operator_id,
                "sourceContainer": str(panel.source_container),
                "sourceDataset": panel.source_dataset,
                "detectorGroup": panel.detector_group,
                "detectorAlias": panel.detector_alias,
                "detectorSide": panel.detector_side,
                "distanceBucket": panel.distance_key,
                "distanceCm": panel.distance_cm,
                "poniDistanceCm": panel.poni_distance_cm,
                "poniSource": panel.poni_source,
                "qPoints": int(np.asarray(panel.q).size),
                "cakeShape": list(np.asarray(panel.cake_intensity).shape),
            }
        )
    return rows
