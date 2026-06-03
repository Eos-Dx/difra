"""Rendering, manifest, and ZIP helpers for daily reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
import zipfile

import numpy as np

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from difra.gui.daily_report_common import (
    DEFAULT_DPI,
    DEFAULT_POINTS,
    SAXS_RANGE,
    WAXS_RANGE,
    _safe_token,
)
from difra.gui.daily_report_models import DetectorSeries


def _detector_sort_key(item: DetectorSeries) -> Tuple[int, str]:
    token = f"{item.detector_group} {item.detector_alias} {item.detector_side}".upper()
    if any(part in token for part in ("PRIMARY", "LEFT", "SAXS")):
        return (0, f"{item.detector_alias} {item.detector_name}")
    if any(part in token for part in ("SECONDARY", "RIGHT", "WAXS")):
        return (1, f"{item.detector_alias} {item.detector_name}")
    return (2, f"{item.detector_alias} {item.detector_name}")


def _report_image_name(specimen_id: str) -> str:
    return f"{_safe_token(specimen_id)}_detectors.png"


def _poni_arcname(item: DetectorSeries) -> str:
    if not item.poni_text.strip():
        return ""
    source_token = _safe_token(
        str(item.source_dataset or "").replace("/entry/measurements/", ""),
        "measurement",
    )
    hash_token = str(item.poni_sha256 or "")[:12] or "nohash"
    return (
        "poni/"
        f"{_safe_token(item.specimen_id)}_"
        f"{_safe_token(item.detector_group)}_"
        f"{_safe_token(item.detector_name)}_"
        f"{_safe_token(item.detector_side)}_"
        f"{source_token}_{hash_token}.poni"
    )


def _write_report_poni_files(series: Iterable[DetectorSeries], output_dir: Path) -> Dict[str, Path]:
    output = Path(output_dir)
    files: Dict[str, Path] = {}
    for item in series:
        arcname = _poni_arcname(item)
        if not arcname or arcname in files:
            continue
        path = output / arcname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item.poni_text, encoding="utf-8")
        files[arcname] = path
    return files


def build_report_manifest_diagnostics(
    series: Iterable[DetectorSeries],
    *,
    poni_files: Dict[str, Path],
) -> Dict[str, Any]:
    grouped: Dict[str, List[DetectorSeries]] = {}
    for item in series:
        grouped.setdefault(item.specimen_id, []).append(item)

    image_entries = []
    series_entries = []
    poni_entries = {}
    for specimen_id, items in sorted(grouped.items()):
        image_file = _report_image_name(specimen_id)
        detector_panels = []
        for detector_key in sorted({item.detector_key for item in items}):
            panel_items = [item for item in items if item.detector_key == detector_key]
            if not panel_items:
                continue
            first = sorted(panel_items, key=_detector_sort_key)[0]
            detector_panels.append(
                {
                    "detectorAlias": first.detector_alias,
                    "detectorName": first.detector_name,
                    "detectorGroup": first.detector_group,
                    "detectorSide": first.detector_side,
                    "rangeName": first.range_name,
                    "qRangeNm^-1": [float(first.q_range[0]), float(first.q_range[1])],
                    "rangeAssignment": first.range_assignment,
                    "seriesCount": len(panel_items),
                }
            )
        image_entries.append(
            {
                "imageFile": image_file,
                "specimenId": specimen_id,
                "layout": "one subplot per detector alias; PRIMARY/LEFT panels are ordered before SECONDARY/RIGHT panels",
                "detectorPanels": detector_panels,
                "seriesCount": len(items),
            }
        )
        for detector_key in sorted({item.detector_key for item in items}):
            panel_items = sorted(
                [item for item in items if item.detector_key == detector_key],
                key=lambda item: item.source_dataset,
            )
            for panel_index, item in enumerate(panel_items, start=1):
                poni_arcname = _poni_arcname(item)
                if poni_arcname:
                    poni_entries[poni_arcname] = {
                        "poniFile": poni_arcname,
                        "poniSource": item.poni_source,
                        "poniSha256": item.poni_sha256,
                        "presentInZip": poni_arcname in poni_files,
                    }
                side = f" {item.detector_side}" if item.detector_side else ""
                series_entries.append(
                    {
                        "imageFile": image_file,
                        "seriesIndex": panel_index,
                        "label": f"{item.detector_alias}{side} #{panel_index}",
                        "specimenId": item.specimen_id,
                        "detectorGroup": item.detector_group,
                        "detectorSide": item.detector_side,
                        "detectorAlias": item.detector_alias,
                        "detectorName": item.detector_name,
                        "rangeName": item.range_name,
                        "rangeAssignment": item.range_assignment,
                        "qRangeNm^-1": [float(item.q_range[0]), float(item.q_range[1])],
                        "sourceContainer": str(item.source_container),
                        "sourceDataset": item.source_dataset,
                        "sourceDataSha256": item.source_data_sha256,
                        "sourceDataShape": list(item.source_data_shape),
                        "sourceDataMin": item.source_data_min,
                        "sourceDataMedian": item.source_data_median,
                        "sourceDataMax": item.source_data_max,
                        "integrationBackend": item.integration_backend,
                        "poniSource": item.poni_source,
                        "poniFile": poni_arcname,
                        "poniSha256": item.poni_sha256,
                    }
                )
    return {
        "images": image_entries,
        "series": series_entries,
        "poniFiles": sorted(poni_entries.values(), key=lambda item: item["poniFile"]),
    }


def render_report_images(
    series: Iterable[DetectorSeries],
    output_dir: Path,
    *,
    dpi: int = DEFAULT_DPI,
) -> List[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    grouped: Dict[str, List[DetectorSeries]] = {}
    for item in series:
        grouped.setdefault(item.specimen_id, []).append(item)

    images: List[Path] = []
    for specimen_id, items in sorted(grouped.items()):
        detector_keys = []
        for item in sorted(items, key=_detector_sort_key):
            if item.detector_key not in detector_keys:
                detector_keys.append(item.detector_key)
        panel_count = max(len(detector_keys), 1)
        ncols = min(panel_count, 3)
        nrows = int(np.ceil(panel_count / ncols))
        fig, axes = plt.subplots(
            nrows,
            ncols,
            figsize=(6.2 * ncols, 4.6 * nrows),
            dpi=dpi,
            squeeze=False,
        )
        for axis in axes.reshape(-1):
            axis.set_visible(False)
        for panel_index, detector_key in enumerate(detector_keys):
            ax = axes.reshape(-1)[panel_index]
            ax.set_visible(True)
            panel_items = [item for item in items if item.detector_key == detector_key]
            panel_items = sorted(panel_items, key=lambda item: item.source_dataset)
            if not panel_items:
                continue
            first = panel_items[0]
            q_range = tuple(first.q_range)
            for index, item in enumerate(panel_items, start=1):
                label = f"{item.detector_alias} #{index}"
                ax.plot(item.q, item.intensity, linewidth=1.1, alpha=0.85, label=label)
            side = f" ({first.detector_side})" if first.detector_side else ""
            ax.set_title(f"{first.detector_alias}{side} | {first.range_label}")
            ax.set_xlabel("q (nm^-1)")
            ax.set_ylabel("I(q)")
            ax.set_xlim(q_range)
            ax.grid(True, alpha=0.25)
            if len(panel_items) <= 12:
                ax.legend(fontsize=7)
        fig.suptitle(str(specimen_id), fontsize=12)
        fig.tight_layout()
        image_path = output / _report_image_name(specimen_id)
        fig.savefig(image_path, dpi=dpi)
        plt.close(fig)
        images.append(image_path)
    return images


def create_zip(
    zip_path: Path,
    image_paths: Iterable[Path],
    *,
    manifest: Dict[str, Any],
    extra_files: Dict[str, Path] | None = None,
) -> Path:
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        for image_path in image_paths:
            path = Path(image_path)
            archive.write(path, arcname=path.name)
        for arcname, source_path in sorted((extra_files or {}).items()):
            path = Path(source_path)
            if path.exists():
                archive.write(path, arcname=str(arcname))
    return target


def create_simple_test_image_zip(output_dir: Path, *, dpi: int = DEFAULT_DPI) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    image_paths = []
    for name, q_range, fn in (
        ("test_PRIMARY_SAXS_1-3nm-1.png", SAXS_RANGE, lambda q: np.sin(q * 4.0) + 2.0),
        ("test_SECONDARY_WAXS_2-21nm-1.png", WAXS_RANGE, lambda q: np.cos(q * 3.0) + 2.0),
    ):
        q = np.linspace(float(q_range[0]), float(q_range[1]), DEFAULT_POINTS)
        y = fn(q)
        fig, ax = plt.subplots(figsize=(6, 4), dpi=dpi)
        ax.plot(q, y, linewidth=1.5)
        ax.set_xlabel("q (nm^-1)")
        ax.set_ylabel("I(q)")
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        image_path = output / name
        fig.savefig(image_path, dpi=dpi)
        plt.close(fig)
        image_paths.append(image_path)
    return create_zip(
        output / "difra_daily_report_test_images.zip",
        image_paths,
        manifest={"kind": "test", "imageCount": len(image_paths)},
    )
