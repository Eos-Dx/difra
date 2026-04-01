"""GUI tests for stage-limit visualization around the sample holder zone."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
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

        scene = QGraphicsScene()
        pixmap = QPixmap(400, 300)
        pixmap.fill(Qt.white)
        self.image_view = SimpleNamespace(
            scene=scene,
            shapes=[],
            points_dict={"generated": {"points": [], "zones": []}, "user": {"points": [], "zones": []}, "beam": []},
            current_pixmap=pixmap,
            image_item=scene.addPixmap(pixmap),
            rotation_angle=0,
        )
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


def test_calibration_shapes_update_ratio_center_and_rotation_flag(qapp, monkeypatch):
    harness = _ShapeHarness()
    question_calls = []
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QInputDialog.getDouble",
        staticmethod(lambda *args, **kwargs: (18.35 if "Square" in str(args[1]) else 15.18, True)),
    )
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: question_calls.append(args[1]) or 16384),
    )

    square_item = QGraphicsRectItem(100.0, 100.0, 183.5, 170.0)
    circle_item = QGraphicsRectItem(210.0, 120.0, 151.8, 140.0)
    harness.image_view.scene.addItem(square_item)
    harness.image_view.scene.addItem(circle_item)
    square_info = {
        "id": 1,
        "uid": "sh_square",
        "type": "Rectangle",
        "item": square_item,
        "role": "include",
    }
    circle_info = {
        "id": 2,
        "uid": "sh_circle",
        "type": "Circle",
        "item": circle_item,
        "role": "include",
    }
    harness.image_view.shapes.extend([square_info, circle_info])

    harness.define_shape_as_calibration_role(square_info, harness.ROLE_CALIBRATION_SQUARE)
    harness.define_shape_as_calibration_role(circle_info, harness.ROLE_HOLDER_CIRCLE)

    assert circle_info["role"] == harness.ROLE_HOLDER_CIRCLE
    assert harness.sample_photo_rotation_confirmed is True
    assert harness.sample_photo_rotation_deg == 180
    assert harness.image_view.rotation_angle == 180
    assert harness.include_center == pytest.approx((114.1, 110.0))
    assert harness.sample_holder_center_px == pytest.approx((114.1, 110.0))
    assert square_info not in harness.image_view.shapes
    assert harness.pixel_to_mm_ratio == pytest.approx(151.8 / 15.18)
    assert question_calls == ["Rotate Sample Holder"]


def test_defining_new_calibration_shape_removes_previous_one(qapp, monkeypatch):
    harness = _ShapeHarness()
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QInputDialog.getDouble",
        staticmethod(lambda *args, **kwargs: (18.35 if "Square" in str(args[1]) else 15.18, True)),
    )
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: 16384),
    )

    square_item = QGraphicsRectItem(50.0, 60.0, 100.0, 100.0)
    circle_item = QGraphicsRectItem(200.0, 80.0, 120.0, 120.0)
    harness.image_view.scene.addItem(square_item)
    harness.image_view.scene.addItem(circle_item)
    square_info = {
        "id": 1,
        "uid": "sh_square_remove",
        "type": "Rectangle",
        "item": square_item,
        "role": "include",
    }
    circle_info = {
        "id": 2,
        "uid": "sh_circle_keep",
        "type": "Circle",
        "item": circle_item,
        "role": "include",
    }
    harness.image_view.shapes.extend([square_info, circle_info])

    harness.define_shape_as_calibration_role(square_info, harness.ROLE_CALIBRATION_SQUARE)
    assert square_info in harness.image_view.shapes

    harness.define_shape_as_calibration_role(circle_info, harness.ROLE_HOLDER_CIRCLE)

    assert circle_info in harness.image_view.shapes
    assert square_info not in harness.image_view.shapes
    assert circle_info["role"] == harness.ROLE_HOLDER_CIRCLE


def test_square_only_can_trigger_rotation_prompt_and_define_center(qapp, monkeypatch):
    harness = _ShapeHarness()
    question_calls = []
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QInputDialog.getDouble",
        staticmethod(lambda *args, **kwargs: (18.35, True)),
    )
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: question_calls.append(args[1]) or 16384),
    )

    square_item = QGraphicsRectItem(100.0, 100.0, 183.5, 170.0)
    harness.image_view.scene.addItem(square_item)
    square_info = {
        "id": 1,
        "uid": "sh_square_only",
        "type": "Rectangle",
        "item": square_item,
        "role": "include",
    }
    harness.image_view.shapes.append(square_info)

    harness.define_shape_as_calibration_role(square_info, harness.ROLE_CALIBRATION_SQUARE)

    assert harness.sample_photo_rotation_confirmed is True
    assert harness.sample_photo_rotation_deg == 180
    assert harness.sample_holder_center_px == pytest.approx((208.25, 115.0))
    assert harness.include_center == pytest.approx((208.25, 115.0))
    assert question_calls == ["Rotate Sample Holder"]
