from __future__ import annotations

from difra.gui.main_window_ext.zone_measurements.logic.beam_center_utils import (
    get_beam_center,
)
from difra.gui.main_window_ext.zone_measurements.measurement_table_utils import (
    add_measurement_to_table,
    delete_selected_points,
)


class _MeasurementWidget:
    def __init__(self) -> None:
        self.calls = []
        self.deleted = 0

    def add_measurement(self, results, timestamp) -> None:
        self.calls.append((results, timestamp))

    def deleteLater(self) -> None:
        self.deleted += 1


class _PointsTable:
    def __init__(self) -> None:
        self.removed_rows = []

    def removeRow(self, row: int) -> None:
        self.removed_rows.append(row)


def test_get_beam_center_falls_back_to_detector_center_without_poni():
    assert get_beam_center("", detector_size=(320, 200)) == (160, 100)


def test_get_beam_center_parses_poni_detector_config():
    poni = "\n".join(
        [
            "Poni1: 0.005",
            "Poni2: 0.01",
            'Detector_config: {"pixel1": 0.0001, "pixel2": 0.0002}',
        ]
    )

    assert get_beam_center(poni) == (50.0, 50.0)


def test_get_beam_center_falls_back_when_required_values_are_missing_or_invalid():
    malformed = "\n".join(
        [
            "Poni1: not-a-number",
            "Poni2: 0.01",
            'Detector_config: {"pixel1": 0.0001}',
        ]
    )

    assert get_beam_center(malformed, detector_size=(300, 120)) == (150, 60)


def test_add_measurement_to_table_ignores_missing_widget():
    add_measurement_to_table(None, 0, None, {"ok": True}, "2026-03-04T10:00:00")


def test_add_measurement_to_table_forwards_measurement_to_widget():
    widget = _MeasurementWidget()

    add_measurement_to_table(
        None,
        3,
        widget,
        {"intensity": 123},
        "2026-03-04T10:00:00",
    )

    assert widget.calls == [({"intensity": 123}, "2026-03-04T10:00:00")]


def test_delete_selected_points_removes_rows_in_reverse_order_and_cleans_widgets():
    points_table = _PointsTable()
    widget_a = _MeasurementWidget()
    widget_b = _MeasurementWidget()
    measurement_widgets = {1: widget_a, 3: widget_b}

    delete_selected_points(points_table, measurement_widgets, [1, 3, 2])

    assert points_table.removed_rows == [3, 2, 1]
    assert widget_a.deleted == 1
    assert widget_b.deleted == 1
    assert measurement_widgets == {}
