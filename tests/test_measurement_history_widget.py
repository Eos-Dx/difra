from __future__ import annotations

from pathlib import Path

import h5py
import pytest
from PyQt5.QtWidgets import QApplication

from difra.gui.technical.widgets import MeasurementHistoryWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_resolve_poni_text_from_h5ref_reads_detector_poni_ref(tmp_path: Path):
    session_path = tmp_path / "session.h5"
    poni_text = "Distance: 0.17\nPoni1: 0.00704\nPoni2: 0.00088\n"

    with h5py.File(session_path, "w") as h5f:
        technical = h5f.create_group("technical")
        poni_group = technical.create_group("poni")
        poni_ds = poni_group.create_dataset("poni_det_saxs", data=poni_text.encode("utf-8"))
        poni_ds.attrs["detector_alias"] = "SAXS"
        poni_ds.attrs["detector_id"] = "DET_SAXS"

        processed = h5f.create_dataset(
            "measurements/pt_001/meas_0001/det_det_saxs/processed_signal",
            data=[[1.0, 2.0], [3.0, 4.0]],
        )
        processed.parent.attrs["detector_alias"] = "SAXS"
        processed.parent.attrs["detector_id"] = "DET_SAXS"
        processed.parent.attrs["poni_ref"] = "/technical/poni/poni_det_saxs"

    measurement_ref = (
        f"h5ref://{session_path}"
        "#/measurements/pt_001/meas_0001/det_det_saxs/processed_signal"
    )

    resolved = MeasurementHistoryWidget._resolve_poni_text_from_h5ref(
        measurement_ref,
        alias="SAXS",
    )

    assert resolved == poni_text.strip()


def test_measurement_history_widget_does_not_fallback_to_in_memory_ponis_for_plain_file(qapp):
    widget = MeasurementHistoryWidget(
        masks={},
        ponis={"PRIMARY": "memory poni should not be used"},
        parent=None,
        point_id=1,
    )

    resolved = widget._resolve_poni_text_for_result(
        "PRIMARY",
        {"filename": "/tmp/non_container_measurement.npy"},
    )

    assert resolved is None
