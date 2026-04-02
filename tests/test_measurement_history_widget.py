from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import h5py
import pytest
from PyQt5.QtWidgets import QApplication

from difra.gui.container_api import get_schema

if "seaborn" not in sys.modules:
    sys.modules["seaborn"] = SimpleNamespace(
        heatmap=lambda data, robust=True, square=True, ax=None, cbar=False: ax.imshow(data)
    )

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
    schema = get_schema(None)

    with h5py.File(session_path, "w") as h5f:
        h5f.require_group(getattr(schema, "GROUP_TECHNICAL", "/entry/technical"))
        poni_group = h5f.require_group(
            getattr(schema, "GROUP_TECHNICAL_PONI", "/entry/technical/poni")
        )
        poni_ds = poni_group.create_dataset("poni_det_saxs", data=poni_text.encode("utf-8"))
        poni_ds.attrs[getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")] = "SAXS"
        poni_ds.attrs[getattr(schema, "ATTR_DETECTOR_ID", "detector_id")] = "DET_SAXS"

        processed = h5f.create_dataset(
            f"{getattr(schema, 'GROUP_MEASUREMENTS', '/entry/measurements')}/pt_001/meas_0001/det_det_saxs/{getattr(schema, 'DATASET_PROCESSED_SIGNAL', 'processed_signal')}",
            data=[[1.0, 2.0], [3.0, 4.0]],
        )
        processed.parent.attrs[getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")] = "SAXS"
        processed.parent.attrs[getattr(schema, "ATTR_DETECTOR_ID", "detector_id")] = "DET_SAXS"
        processed.parent.attrs[getattr(schema, "ATTR_PONI_REF", "poni_ref")] = (
            f"{getattr(schema, 'GROUP_TECHNICAL_PONI', '/entry/technical/poni')}/poni_det_saxs"
        )

    measurement_ref = (
        f"h5ref://{session_path}"
        f"#{getattr(schema, 'GROUP_MEASUREMENTS', '/entry/measurements')}/pt_001/meas_0001/det_det_saxs/{getattr(schema, 'DATASET_PROCESSED_SIGNAL', 'processed_signal')}"
    )

    resolved = MeasurementHistoryWidget._resolve_poni_text_from_h5ref(
        measurement_ref,
        alias="SAXS",
    )

    assert resolved == poni_text.strip()


def test_resolve_poni_text_from_h5ref_matches_raw_detector_role_aliases(tmp_path: Path):
    session_path = tmp_path / "session_role_alias.h5"
    poni_text = "Distance: 0.1701\nPoni1: 0.00704\nPoni2: 0.00088\n"
    schema = get_schema(None)

    with h5py.File(session_path, "w") as h5f:
        h5f.require_group(getattr(schema, "GROUP_TECHNICAL", "/entry/technical"))
        poni_group = h5f.require_group(
            getattr(schema, "GROUP_TECHNICAL_PONI", "/entry/technical/poni")
        )
        poni_ds = poni_group.create_dataset(
            "poni_det_primary",
            data=poni_text.encode("utf-8"),
        )
        poni_ds.attrs[getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")] = "det_primary"
        poni_ds.attrs[getattr(schema, "ATTR_DETECTOR_ID", "detector_id")] = "det_primary"

        processed = h5f.create_dataset(
            f"{getattr(schema, 'GROUP_MEASUREMENTS', '/entry/measurements')}/pt_001/meas_0001/det_primary/{getattr(schema, 'DATASET_PROCESSED_SIGNAL', 'processed_signal')}",
            data=[[1.0, 2.0], [3.0, 4.0]],
        )
        processed.parent.attrs[getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")] = "det_primary"
        processed.parent.attrs[getattr(schema, "ATTR_DETECTOR_ID", "detector_id")] = "det_primary"

    measurement_ref = (
        f"h5ref://{session_path}"
        f"#{getattr(schema, 'GROUP_MEASUREMENTS', '/entry/measurements')}/pt_001/meas_0001/det_primary/{getattr(schema, 'DATASET_PROCESSED_SIGNAL', 'processed_signal')}"
    )

    resolved = MeasurementHistoryWidget._resolve_poni_text_from_h5ref(
        measurement_ref,
        alias="PRIMARY",
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


def test_open_measurement_result_opens_h5ref_on_single_click(monkeypatch, qapp):
    widget = MeasurementHistoryWidget(masks={}, ponis={}, parent=None, point_id=1)
    widget.measurements = [
        {
            "timestamp": "2026-04-02 10:31:21",
            "results": {
                "PRIMARY": {
                    "filename": "h5ref:///tmp/session.nxs.h5#/entry/measurements/pt_004/meas_000000001/det_primary/processed_signal",
                    "goodness": None,
                }
            },
        }
    ]

    opened = {}

    def _fake_show_measurement_window(filename, mask, poni_text, parent):
        opened["filename"] = filename
        opened["mask"] = mask
        opened["poni_text"] = poni_text
        opened["parent"] = parent

    monkeypatch.setattr(
        "difra.gui.technical.capture.show_measurement_window",
        _fake_show_measurement_window,
    )
    monkeypatch.setattr(widget, "_resolve_poni_text_for_result", lambda alias, res: "Distance: 0.17")

    result = widget._open_measurement_result(0, 1, ["PRIMARY"], h5ref_only=True)

    assert result is True
    assert opened["filename"].startswith("h5ref://")
    assert opened["poni_text"] == "Distance: 0.17"
