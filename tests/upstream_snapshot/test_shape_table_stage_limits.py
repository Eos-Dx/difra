"""GUI tests for stage-limit visualization around the sample holder zone."""

import os
from types import SimpleNamespace

import pytest
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QGraphicsRectItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
)

from difra.gui.extra.resizable_zone import (
    ResizableEllipseItem,
    ResizableRectangleItem,
    ResizableZoneItem,
)
from difra.gui.main_window_ext.shape_table_extension import ShapeTableMixin
from difra.gui.views.image_view import ImageView


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
        self._deleted_points = 0
        self._cleared_profiles = 0
        self._messagebox_warnings = []
        self.create_shape_table()

    def update_conversion_label(self):
        self._conversion_updates += 1

    def _get_stage_limits(self):
        return {"x": (-14.0, 14.0), "y": (-10.0, 10.0)}

    def delete_all_points(self):
        self._deleted_points += 1
        self.image_view.points_dict["generated"]["points"] = []
        self.image_view.points_dict["generated"]["zones"] = []
        self.image_view.points_dict["user"]["points"] = []
        self.image_view.points_dict["user"]["zones"] = []

    def _clear_profile_paths(self):
        self._cleared_profiles += 1
        self.image_view.profile_paths = []

    def _append_warning(self, text: str):
        self._messagebox_warnings.append(str(text))


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
        staticmethod(lambda *args, **kwargs: QMessageBox.No),
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


def test_drawn_ellipse_is_immediately_resizable(qapp):
    view = ImageView()
    pixmap = QPixmap(400, 300)
    pixmap.fill(Qt.white)
    view.set_image(pixmap)
    callback_hits = []
    view.shape_updated_callback = lambda: callback_hits.append("updated")

    view.set_drawing_mode("ellipse")
    item = ResizableEllipseItem(50.0, 60.0, 80.0, 40.0)
    item.geometry_changed_callback = view.shape_updated_callback
    view.scene.addItem(item)
    item.setSelected(True)

    assert isinstance(item, ResizableEllipseItem)
    assert any(handle.isVisible() for handle in item._handles.values())

    east_handle = item._handles["E"]
    original_rect = item.rect()
    item.resize_from_handle("E", east_handle.pos() + QPointF(25.0, 0.0))

    resized_rect = item.rect()
    assert resized_rect.width() > original_rect.width()
    assert callback_hits


def test_drawn_rectangle_is_immediately_resizable(qapp):
    view = ImageView()
    pixmap = QPixmap(400, 300)
    pixmap.fill(Qt.white)
    view.set_image(pixmap)
    callback_hits = []
    view.shape_updated_callback = lambda: callback_hits.append("updated")

    item = ResizableRectangleItem(40.0, 50.0, 70.0, 30.0)
    item.geometry_changed_callback = view.shape_updated_callback
    view.scene.addItem(item)
    item.setSelected(True)

    assert isinstance(item, ResizableRectangleItem)
    assert any(handle.isVisible() for handle in item._handles.values())

    south_handle = item._handles["S"]
    original_rect = item.rect()
    item.resize_from_handle("S", south_handle.pos() + QPointF(0.0, 20.0))

    resized_rect = item.rect()
    assert resized_rect.height() > original_rect.height()
    assert callback_hits


def test_crop_creates_editable_preview_and_applies(qapp):
    view = ImageView()
    pixmap = QPixmap(200, 100)
    pixmap.fill(Qt.white)
    view.set_image(pixmap)

    view._create_crop_preview(QRectF(10.0, 15.0, 80.0, 40.0))

    assert isinstance(view.crop_item, ResizableRectangleItem)
    assert view.crop_rect is not None
    assert any(handle.isVisible() for handle in view.crop_item._handles.values())

    east_handle = view.crop_item._handles["E"]
    view.crop_item.resize_from_handle("E", east_handle.pos() + QPointF(20.0, 0.0))
    crop_rect = view.crop_rect

    assert crop_rect is not None
    assert crop_rect.width() > 80.0

    applied = view.apply_crop_preview()

    assert applied is True
    assert view.current_pixmap.width() == int(crop_rect.width())
    assert view.current_pixmap.height() == int(crop_rect.height())
    assert view.crop_item is None
    assert view.crop_rect is None


def _pixmap_from_rgb_array(array):
    image = QImage(
        array.data,
        array.shape[1],
        array.shape[0],
        array.strides[0],
        QImage.Format_RGB888,
    ).copy()
    return QPixmap.fromImage(image)


def test_catch_auto_refines_outer_shape_from_selected_contrast_colors(qapp, monkeypatch):
    harness = _ShapeHarness()
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QInputDialog.getDouble",
        staticmethod(lambda *args, **kwargs: (15.18, True)),
    )
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: QMessageBox.No),
    )
    monkeypatch.setattr(
        harness,
        "_prompt_catch_auto_colors",
        lambda: {"holder_rgb": (190, 165, 70), "background_rgb": (50, 110, 50)},
    )

    height, width = 240, 240
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = np.array([50, 110, 50], dtype=np.uint8)  # green background
    yy, xx = np.indices((height, width))
    outer_center = (120.0, 110.0)
    outer_mask = (((xx - outer_center[0]) / 70.0) ** 2 + ((yy - outer_center[1]) / 52.0) ** 2) <= 1.0
    image[outer_mask] = np.array([190, 165, 70], dtype=np.uint8)  # golden holder

    pixmap = _pixmap_from_rgb_array(image)
    harness.image_view.scene.clear()
    harness.image_view.current_pixmap = pixmap
    harness.image_view.image_item = harness.image_view.scene.addPixmap(pixmap)

    ellipse_item = ResizableEllipseItem(35.0, 35.0, 150.0, 120.0)  # manual center = (110, 95)
    harness.image_view.scene.addItem(ellipse_item)
    circle_info = {
        "id": 1,
        "uid": "holder_auto",
        "type": "Circle",
        "item": ellipse_item,
        "role": "include",
    }
    harness.image_view.shapes.append(circle_info)

    harness.define_shape_as_calibration_role(circle_info, harness.ROLE_HOLDER_CIRCLE)
    before_center = circle_info["center_px"]

    changed = harness.catch_auto_for_shape(circle_info)

    after_center = circle_info["center_px"]
    after_rect = circle_info["item"].mapRectToScene(circle_info["item"].rect())
    assert changed is True
    assert after_center[0] > before_center[0]
    assert after_center[1] > before_center[1]
    assert after_center[0] == pytest.approx(outer_center[0], abs=3.0)
    assert after_center[1] == pytest.approx(outer_center[1], abs=3.0)
    assert after_rect.width() == pytest.approx(140.0, abs=10.0)
    assert after_rect.height() == pytest.approx(104.0, abs=15.0)
    assert circle_info["item"].isSelected() is True
    assert all(handle.isVisible() for handle in circle_info["item"]._handles.values())


def test_catch_auto_assistant_uses_image_samples_and_keeps_detection_on_source_image(
    qapp, monkeypatch
):
    harness = _ShapeHarness()
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QInputDialog.getDouble",
        staticmethod(lambda *args, **kwargs: (15.18, True)),
    )
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: QMessageBox.No),
    )

    class _ViewportStub:
        def setCursor(self, *_args, **_kwargs):
            return None

        def unsetCursor(self, *_args, **_kwargs):
            return None

    harness.image_view.viewport = lambda: _ViewportStub()
    harness.image_view.set_drawing_mode = lambda mode: setattr(harness.image_view, "drawing_mode", mode)

    height, width = 220, 240
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = np.array([40, 120, 45], dtype=np.uint8)
    yy, xx = np.indices((height, width))
    outer_center = (122.0, 104.0)
    outer_mask = (((xx - outer_center[0]) / 62.0) ** 2 + ((yy - outer_center[1]) / 50.0) ** 2) <= 1.0
    image[outer_mask] = np.array([195, 168, 72], dtype=np.uint8)

    pixmap = _pixmap_from_rgb_array(image)
    harness.image_view.scene.clear()
    harness.image_view.current_pixmap = pixmap
    harness.image_view.image_item = harness.image_view.scene.addPixmap(pixmap)

    ellipse_item = ResizableEllipseItem(48.0, 42.0, 142.0, 116.0)
    harness.image_view.scene.addItem(ellipse_item)
    circle_info = {
        "id": 1,
        "uid": "holder_assistant",
        "type": "Circle",
        "item": ellipse_item,
        "role": "include",
    }
    harness.image_view.shapes.append(circle_info)
    harness.define_shape_as_calibration_role(circle_info, harness.ROLE_HOLDER_CIRCLE)

    opened = harness.open_catch_auto_assistant_for_shape(circle_info)
    assert opened is True
    dialog = harness._catch_auto_assistant_dialog
    assert dialog is not None

    buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
    buttons["Pick Holder From Image"].click()
    assert callable(harness.image_view.image_click_sample_callback)
    harness.image_view.image_click_sample_callback(QPointF(outer_center[0], outer_center[1]))

    buttons["Pick Background From Image"].click()
    assert callable(harness.image_view.image_click_sample_callback)
    harness.image_view.image_click_sample_callback(QPointF(10.0, 10.0))

    assert harness._catch_auto_preview_active is True
    assert harness.catch_auto_holder_rgb == pytest.approx((195, 168, 72), abs=6)
    assert harness.catch_auto_background_rgb == pytest.approx((40, 120, 45), abs=6)

    changed = harness.catch_auto_for_shape(
        circle_info,
        color_payload={
            "holder_rgb": tuple(harness.catch_auto_holder_rgb),
            "background_rgb": tuple(harness.catch_auto_background_rgb),
        },
        prompt_for_colors=False,
    )
    assert changed is True
    assert circle_info["center_px"][0] == pytest.approx(outer_center[0], abs=4.0)
    assert circle_info["center_px"][1] == pytest.approx(outer_center[1], abs=4.0)

    dialog.close()
    qapp.processEvents()


def test_define_calibration_shape_keeps_shape_selected_and_editable(qapp, monkeypatch):
    harness = _ShapeHarness()
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QInputDialog.getDouble",
        staticmethod(lambda *args, **kwargs: (15.18, True)),
    )
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: QMessageBox.No),
    )

    ellipse_item = ResizableEllipseItem(80.0, 90.0, 140.0, 100.0)
    harness.image_view.scene.addItem(ellipse_item)
    circle_info = {
        "id": 11,
        "uid": "editable_holder",
        "type": "Circle",
        "item": ellipse_item,
        "role": "include",
    }
    harness.image_view.shapes.append(circle_info)

    harness.define_shape_as_calibration_role(circle_info, harness.ROLE_HOLDER_CIRCLE)

    assert ellipse_item.isSelected() is True
    assert all(handle.isVisible() for handle in ellipse_item._handles.values())


def test_calibration_geometry_change_clears_points_profiles_and_non_calibration_shapes(qapp, monkeypatch):
    harness = _ShapeHarness()
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QInputDialog.getDouble",
        staticmethod(lambda *args, **kwargs: (15.18, True)),
    )
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: QMessageBox.No),
    )

    circle_item = ResizableEllipseItem(100.0, 100.0, 120.0, 80.0)
    include_item = QGraphicsRectItem(20.0, 20.0, 30.0, 30.0)
    exclude_item = QGraphicsRectItem(60.0, 60.0, 20.0, 20.0)
    harness.image_view.scene.addItem(circle_item)
    harness.image_view.scene.addItem(include_item)
    harness.image_view.scene.addItem(exclude_item)
    circle_info = {
        "id": 1,
        "uid": "holder",
        "type": "Circle",
        "item": circle_item,
        "role": "include",
    }
    include_info = {
        "id": 2,
        "uid": "include",
        "type": "Rectangle",
        "item": include_item,
        "role": "include",
    }
    exclude_info = {
        "id": 3,
        "uid": "exclude",
        "type": "Rectangle",
        "item": exclude_item,
        "role": "exclude",
    }
    harness.image_view.shapes.extend([circle_info, include_info, exclude_info])
    harness.image_view.profile_paths = [{"item": object(), "points": [(0.0, 0.0), (1.0, 1.0)]}]
    harness.image_view.points_dict["generated"]["points"] = [object()]
    harness.image_view.points_dict["generated"]["zones"] = [object()]
    harness.image_view.points_dict["user"]["points"] = [object()]
    harness.image_view.points_dict["user"]["zones"] = [object()]

    harness.define_shape_as_calibration_role(circle_info, harness.ROLE_HOLDER_CIRCLE)
    circle_info["item"].resize_from_handle("E", circle_info["item"]._handles["E"].pos() + QPointF(10.0, 0.0))

    assert harness._deleted_points >= 1
    assert harness._cleared_profiles >= 1
    assert include_info not in harness.image_view.shapes


def test_define_holder_circle_keeps_moved_ellipse_center(qapp, monkeypatch):
    harness = _ShapeHarness()
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QInputDialog.getDouble",
        staticmethod(lambda *args, **kwargs: (15.18, True)),
    )
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: QMessageBox.No),
    )

    ellipse_item = ResizableEllipseItem(100.0, 120.0, 80.0, 40.0)
    harness.image_view.scene.addItem(ellipse_item)
    ellipse_item.setPos(35.0, -15.0)
    original_scene_rect = ellipse_item.mapRectToScene(ellipse_item.rect())
    original_center = original_scene_rect.center()
    ellipse_item._normalize_translation()

    circle_info = {
        "id": 1,
        "uid": "holder_from_moved_ellipse",
        "type": "Circle",
        "item": ellipse_item,
        "role": "include",
    }
    harness.image_view.shapes.append(circle_info)

    harness.define_shape_as_calibration_role(circle_info, harness.ROLE_HOLDER_CIRCLE)

    new_item = circle_info["item"]
    new_rect = new_item.mapRectToScene(new_item.rect())
    new_center = new_rect.center()
    assert circle_info["role"] == harness.ROLE_HOLDER_CIRCLE
    assert isinstance(new_item, ResizableEllipseItem)
    assert new_rect.width() == pytest.approx(original_scene_rect.width())
    assert new_rect.height() == pytest.approx(original_scene_rect.height())
    assert new_center.x() == pytest.approx(original_center.x())
    assert new_center.y() == pytest.approx(original_center.y())


def test_deleting_holder_circle_clears_all_shapes_points_and_profiles_before_measurements(
    qapp, monkeypatch
):
    harness = _ShapeHarness()
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: harness._append_warning(args[2] if len(args) > 2 else "")),
    )

    holder_item = ResizableZoneItem(120.0, 100.0, 40.0)
    include_item = ResizableRectangleItem(80.0, 80.0, 60.0, 40.0)
    harness.image_view.scene.addItem(holder_item)
    harness.image_view.scene.addItem(include_item)
    holder_info = {
        "id": 1,
        "uid": "holder_delete_all",
        "type": "Circle",
        "item": holder_item,
        "role": harness.ROLE_HOLDER_CIRCLE,
    }
    include_info = {
        "id": 2,
        "uid": "include_delete_all",
        "type": "Rectangle",
        "item": include_item,
        "role": "include",
    }
    harness.image_view.shapes.extend([holder_info, include_info])
    harness.image_view.profile_paths = [{"id": "p1", "item": object(), "points": [(0.0, 0.0), (1.0, 1.0)]}]
    harness.image_view.points_dict["generated"]["points"] = [object()]
    harness.image_view.points_dict["generated"]["zones"] = [object()]

    changed = harness._delete_shape_infos([holder_info])

    assert changed is True
    assert harness.image_view.shapes == []
    assert harness._deleted_points >= 1
    assert harness._cleared_profiles >= 1
    assert harness._messagebox_warnings == []


def test_deleting_include_shape_clears_points_and_profiles_before_measurements(
    qapp, monkeypatch
):
    harness = _ShapeHarness()
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: harness._append_warning(args[2] if len(args) > 2 else "")),
    )

    include_item = ResizableRectangleItem(80.0, 80.0, 60.0, 40.0)
    exclude_item = ResizableEllipseItem(140.0, 90.0, 40.0, 40.0)
    harness.image_view.scene.addItem(include_item)
    harness.image_view.scene.addItem(exclude_item)
    include_info = {
        "id": 1,
        "uid": "include_delete_points",
        "type": "Rectangle",
        "item": include_item,
        "role": "include",
    }
    exclude_info = {
        "id": 2,
        "uid": "exclude_keep",
        "type": "Circle",
        "item": exclude_item,
        "role": "exclude",
    }
    harness.image_view.shapes.extend([include_info, exclude_info])
    harness.image_view.profile_paths = [{"id": "p1", "item": object(), "points": [(0.0, 0.0), (1.0, 1.0)]}]
    harness.image_view.points_dict["generated"]["points"] = [object()]
    harness.image_view.points_dict["generated"]["zones"] = [object()]

    changed = harness._delete_shape_infos([include_info])

    assert changed is True
    assert include_info not in harness.image_view.shapes
    assert exclude_info in harness.image_view.shapes
    assert harness._deleted_points >= 1
    assert harness._cleared_profiles >= 1
    assert harness._messagebox_warnings == []


def test_measured_shapes_cannot_be_deleted_but_new_shapes_can(qapp, monkeypatch):
    harness = _ShapeHarness()
    monkeypatch.setattr(
        "difra.gui.main_window_ext.shape_table_extension.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: harness._append_warning(args[2] if len(args) > 2 else "")),
    )
    harness.session_manager = SimpleNamespace(has_point_measurements=lambda: True)

    locked_item = ResizableRectangleItem(50.0, 50.0, 40.0, 40.0)
    new_item = ResizableRectangleItem(140.0, 50.0, 40.0, 40.0)
    harness.image_view.scene.addItem(locked_item)
    harness.image_view.scene.addItem(new_item)
    locked_info = {
        "id": 1,
        "uid": "locked_shape",
        "type": "Rectangle",
        "item": locked_item,
        "role": "include",
        "locked_after_measurements": True,
        "isNew": False,
    }
    new_info = {
        "id": 2,
        "uid": "new_shape",
        "type": "Rectangle",
        "item": new_item,
        "role": "include",
        "locked_after_measurements": False,
        "isNew": True,
    }
    harness.image_view.shapes.extend([locked_info, new_info])

    changed = harness._delete_shape_infos([locked_info, new_info])

    assert changed is True
    assert locked_info in harness.image_view.shapes
    assert new_info not in harness.image_view.shapes
    assert any("cannot be deleted" in text for text in harness._messagebox_warnings)


def test_shape_table_role_colors_override_is_new_gray(qapp):
    harness = _ShapeHarness()

    include_item = ResizableRectangleItem(10.0, 10.0, 20.0, 20.0)
    exclude_item = ResizableRectangleItem(40.0, 10.0, 20.0, 20.0)
    circle_item = ResizableEllipseItem(70.0, 10.0, 30.0, 20.0)
    square_item = ResizableRectangleItem(110.0, 10.0, 30.0, 24.0)
    for item in (include_item, exclude_item, circle_item, square_item):
        harness.image_view.scene.addItem(item)

    harness.image_view.shapes = [
        {
            "id": 1,
            "uid": "include_new",
            "type": "Rectangle",
            "item": include_item,
            "role": "include",
            "isNew": True,
        },
        {
            "id": 2,
            "uid": "exclude_new",
            "type": "Rectangle",
            "item": exclude_item,
            "role": "exclude",
            "isNew": True,
        },
        {
            "id": 3,
            "uid": "holder_new",
            "type": "Circle",
            "item": circle_item,
            "role": harness.ROLE_HOLDER_CIRCLE,
            "physical_size_mm": 15.18,
            "isNew": True,
        },
        {
            "id": 4,
            "uid": "square_new",
            "type": "Rectangle",
            "item": square_item,
            "role": harness.ROLE_CALIBRATION_SQUARE,
            "physical_size_mm": 18.35,
            "isNew": True,
        },
    ]

    harness.update_shape_table()

    assert harness.shapeTable.item(0, 0).background().color() == QColor("lightgreen")
    assert harness.shapeTable.item(1, 0).background().color() == QColor("lightcoral")
    assert harness.shapeTable.item(2, 0).background().color() == QColor("#BBDEFB")
    assert harness.shapeTable.item(3, 0).background().color() == QColor("#E1BEE7")


def test_stage_limit_outline_uses_holder_center_and_beam_center_mm(qapp):
    harness = _ShapeHarness()
    harness.pixel_to_mm_ratio = 10.0
    harness.sample_holder_center_px = (200.0, 150.0)
    harness.sample_photo_beam_center_mm = (6.15, -9.15)

    shape_item = QGraphicsRectItem(180.0, 130.0, 40.0, 40.0)
    harness.image_view.scene.addItem(shape_item)
    shape_info = {
        "id": 10,
        "uid": "holder_outline",
        "type": "Circle",
        "item": shape_item,
        "role": harness.ROLE_HOLDER_CIRCLE,
    }
    harness.image_view.shapes.append(shape_info)

    harness._draw_stage_limit_outline(shape_info, 200.0, 150.0)

    outline = shape_info.get("stage_limit_outline")
    assert outline is not None
    rect = outline.rect()
    assert rect.x() == pytest.approx(-1.5)
    assert rect.y() == pytest.approx(141.5)
    assert rect.width() == pytest.approx(280.0)
    assert rect.height() == pytest.approx(200.0)
