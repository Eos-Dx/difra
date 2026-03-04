"""GUI tests for stage-limit visualization around the sample holder zone."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QGraphicsRectItem, QMainWindow

from difra.gui.main_window_ext.shape_table_extension import ShapeTableMixin


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _ShapeHarness(QMainWindow, ShapeTableMixin):
    def __init__(self):
        super().__init__()
        from PyQt5.QtWidgets import QGraphicsScene, QDoubleSpinBox

        self.image_view = SimpleNamespace(scene=QGraphicsScene(), shapes=[])
        self.real_x_pos_mm = QDoubleSpinBox()
        self.real_y_pos_mm = QDoubleSpinBox()
        self.real_x_pos_mm.setValue(0.0)
        self.real_y_pos_mm.setValue(0.0)
        self.pixel_to_mm_ratio = 1.0
        self.include_center = (0.0, 0.0)
        self._conversion_updates = 0
        self.create_shape_table()

    def update_conversion_label(self):
        self._conversion_updates += 1

    def _get_stage_limits(self):
        return {"x": (-14.0, 14.0), "y": (-10.0, 10.0)}


def test_sample_holder_draws_stage_limit_outline(qapp):
    harness = _ShapeHarness()
    shape_item = QGraphicsRectItem(100.0, 100.0, 180.0, 180.0)
    harness.image_view.scene.addItem(shape_item)
    shape_info = {
        "id": 1,
        "uid": "sh_test",
        "type": "Circle",
        "item": shape_item,
        "role": "sample holder",
    }
    harness.image_view.shapes.append(shape_info)

    harness.apply_shape_role(shape_info)

    outline = shape_info.get("stage_limit_outline")
    assert outline is not None
    rect = outline.rect()
    assert rect.x() == pytest.approx(50.0)
    assert rect.y() == pytest.approx(90.0)
    assert rect.width() == pytest.approx(280.0)
    assert rect.height() == pytest.approx(200.0)

    harness.real_x_pos_mm.setValue(5.0)
    harness.refresh_stage_limit_overlays()

    moved_outline = shape_info.get("stage_limit_outline")
    moved_rect = moved_outline.rect()
    assert moved_rect.x() == pytest.approx(100.0)
    assert moved_rect.y() == pytest.approx(90.0)
    assert moved_rect.width() == pytest.approx(280.0)
    assert moved_rect.height() == pytest.approx(200.0)
