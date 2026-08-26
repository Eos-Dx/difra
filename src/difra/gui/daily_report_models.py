"""Data models for daily valid-container reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from difra.gui.daily_report_common import _safe_token


@dataclass(frozen=True)
class DetectorSeries:
    specimen_id: str
    detector_group: str
    detector_alias: str
    detector_name: str
    detector_side: str
    range_name: str
    q_range: Tuple[float, float]
    range_label: str
    range_assignment: str
    q: np.ndarray
    intensity: np.ndarray
    poni_text: str
    poni_source: str
    poni_sha256: str
    source_data_sha256: str
    source_data_shape: Tuple[int, ...]
    source_data_min: float
    source_data_median: float
    source_data_max: float
    source_data: np.ndarray
    integration_backend: str
    source_container: Path
    source_dataset: str
    operator_id: str = ""

    @property
    def detector_key(self) -> str:
        return _safe_token(
            "_".join(
                item
                for item in (
                    self.detector_alias,
                    self.detector_group,
                    self.detector_name,
                )
                if item
            ),
            "detector",
        )


@dataclass
class DailyReportResult:
    scanned: int = 0
    valid_containers: int = 0
    skipped: List[str] = field(default_factory=list)
    images: List[Path] = field(default_factory=list)
    zip_path: Optional[Path] = None
    email_result: Dict[str, Any] = field(default_factory=dict)
    manifest: Dict[str, Any] = field(default_factory=dict)
    state_path: Optional[Path] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    tracking_started_at: Optional[str] = None


@dataclass(frozen=True)
class PoniQcPanel:
    operator_id: str
    source_container: Path
    source_dataset: str
    detector_group: str
    detector_alias: str
    detector_name: str
    detector_side: str
    distance_key: str
    distance_cm: float
    poni_distance_cm: float
    poni_text: str
    poni_source: str
    data: np.ndarray
    q: np.ndarray
    intensity: np.ndarray
    cake_q: np.ndarray
    cake_chi: np.ndarray
    cake_intensity: np.ndarray
