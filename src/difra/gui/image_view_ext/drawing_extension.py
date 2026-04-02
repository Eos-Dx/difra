import uuid

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QPainterPath, QPen
from PyQt5.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
)
from difra.gui.extra.resizable_zone import ResizableEllipseItem, ResizableRectangleItem


class DrawingMixin:

    def init_drawing(self):
        # Initialize drawing properties.
        self.drawing_mode = None  # "rect", "ellipse", "crop", or None (select mode)
        self.pen = self._create_pen()
        self.start_point = None
        self.current_shape = None
        self.current_profile_points = []
        self.shapes = []  # List to hold drawn shapes (each: {"id", "type", "item"})
        self.shape_counter = 1
        self.shape_updated_callback = None
        self.crop_item = None
        self.crop_rect = None
        self.crop_state_changed_callback = None
        self.image_click_sample_callback = None
        if not hasattr(self, "profile_paths"):
            self.profile_paths = []

    def _create_pen(self):
        return QPen(Qt.red, 2, Qt.DashLine)

    def set_drawing_mode(self, mode):
        self.drawing_mode = mode

    def _notify_crop_state_changed(self):
        callback = getattr(self, "crop_state_changed_callback", None)
        if callable(callback):
            callback(bool(self.crop_item is not None))

    def _update_crop_rect_from_item(self):
        item = getattr(self, "crop_item", None)
        if item is None:
            self.crop_rect = None
            self._notify_crop_state_changed()
            return
        try:
            self.crop_rect = item.mapRectToScene(item.rect()).normalized()
        except Exception:
            self.crop_rect = item.sceneBoundingRect().normalized()
        self._notify_crop_state_changed()

    def clear_crop_preview(self):
        item = getattr(self, "crop_item", None)
        if item is not None:
            try:
                self.scene.removeItem(item)
            except Exception:
                pass
        self.crop_item = None
        self.crop_rect = None
        self._notify_crop_state_changed()

    def _activate_select_mode_for_crop_preview(self):
        self.set_drawing_mode(None)
        main_window = self.window()
        if main_window is not None and hasattr(main_window, "select_act"):
            try:
                main_window.select_act.setChecked(True)
            except Exception:
                pass

    def _create_crop_preview(self, rect: QRectF):
        self.clear_crop_preview()
        crop_item = ResizableRectangleItem(
            float(rect.x()),
            float(rect.y()),
            float(rect.width()),
            float(rect.height()),
        )
        crop_item.setPen(self.pen)
        crop_item.geometry_changed_callback = self._update_crop_rect_from_item
        self.scene.addItem(crop_item)
        crop_item.setSelected(True)
        self.crop_item = crop_item
        self._update_crop_rect_from_item()
        self._activate_select_mode_for_crop_preview()
        return crop_item

    def _clear_workspace_geometry_after_crop(self):
        main_window = self.window()
        if main_window is not None:
            delete_shapes = getattr(main_window, "delete_all_shapes_from_table", None)
            if callable(delete_shapes):
                try:
                    delete_shapes(force=True)
                except Exception:
                    pass
            clear_profiles = getattr(main_window, "_clear_profile_paths", None)
            if callable(clear_profiles):
                try:
                    clear_profiles()
                except Exception:
                    pass
            delete_points = getattr(main_window, "delete_all_points", None)
            if callable(delete_points):
                try:
                    delete_points()
                except Exception:
                    pass
        self.shapes = []
        for profile_info in list(getattr(self, "profile_paths", []) or []):
            try:
                self.scene.removeItem(profile_info.get("item"))
            except Exception:
                pass
        self.profile_paths = []
        if hasattr(self, "points_dict"):
            for point_type in ("generated", "user"):
                bucket = self.points_dict.get(point_type, {})
                bucket["points"] = []
                bucket["zones"] = []

    def apply_crop_preview(self):
        if self.current_pixmap is None:
            return False
        rect = getattr(self, "crop_rect", None)
        if rect is None:
            return False
        image_rect = QRectF(self.current_pixmap.rect())
        crop_rect = rect.normalized().intersected(image_rect)
        if crop_rect.width() <= 0 or crop_rect.height() <= 0:
            return False
        cropped_pixmap = self.current_pixmap.copy(
            int(crop_rect.x()),
            int(crop_rect.y()),
            int(crop_rect.width()),
            int(crop_rect.height()),
        )
        image_path = getattr(self, "current_image_path", None)
        self._clear_workspace_geometry_after_crop()
        self.set_image(cropped_pixmap, image_path=image_path)
        self.clear_crop_preview()
        return True

    def mousePressEvent(self, event):
        if (
            callable(getattr(self, "image_click_sample_callback", None))
            and event.button() == Qt.LeftButton
            and self.current_pixmap is not None
        ):
            try:
                scene_point = self.mapToScene(event.pos())
                self.image_click_sample_callback(scene_point)
                event.accept()
                return
            except Exception:
                pass
        if self.drawing_mode in ["rect", "ellipse", "crop", "profile"]:
            # Do nothing if no image is loaded.
            if self.current_pixmap is None:
                print("No image loaded. Please open an image first.")
                return
            if self.drawing_mode == "crop":
                self.clear_crop_preview()
            self.start_point = self.mapToScene(event.pos())
            if self.drawing_mode == "profile":
                self.current_profile_points = [(float(self.start_point.x()), float(self.start_point.y()))]
                path = QPainterPath(self.start_point)
                path_item = QGraphicsPathItem(path)
                path_item.setPen(self.pen)
                path_item.setFlags(
                    QGraphicsItem.ItemIsSelectable
                )
                self.current_shape = path_item
                self.scene.addItem(path_item)
                return
            rect = QRectF(self.start_point, self.start_point)
            if self.drawing_mode in ["rect", "crop"]:
                rect_item = (
                    QGraphicsRectItem(rect)
                    if self.drawing_mode == "crop"
                    else ResizableRectangleItem(
                        rect.x(),
                        rect.y(),
                        rect.width(),
                        rect.height(),
                    )
                )
                rect_item.setPen(self.pen)
                if self.drawing_mode == "rect":
                    callback = getattr(self, "shape_updated_callback", None)
                    if callable(callback):
                        rect_item.geometry_changed_callback = callback
                self.current_shape = rect_item
                self.scene.addItem(rect_item)
            elif self.drawing_mode == "ellipse":
                ellipse_item = ResizableEllipseItem(
                    rect.x(),
                    rect.y(),
                    rect.width(),
                    rect.height(),
                )
                ellipse_item.setPen(self.pen)
                callback = getattr(self, "shape_updated_callback", None)
                if callable(callback):
                    ellipse_item.geometry_changed_callback = callback
                self.current_shape = ellipse_item
                self.scene.addItem(ellipse_item)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drawing_mode and self.start_point and self.current_shape:
            current_point = self.mapToScene(event.pos())
            if self.drawing_mode == "profile":
                point = (float(current_point.x()), float(current_point.y()))
                if not self.current_profile_points or point != self.current_profile_points[-1]:
                    self.current_profile_points.append(point)
                    path = QPainterPath(self.start_point)
                    for x_value, y_value in self.current_profile_points[1:]:
                        path.lineTo(float(x_value), float(y_value))
                    self.current_shape.setPath(path)
                return
            rect = QRectF(self.start_point, current_point).normalized()
            if hasattr(self.current_shape, "setRect"):
                self.current_shape.setRect(rect)
                updater = getattr(self.current_shape, "_update_handle_positions", None)
                if callable(updater):
                    updater()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drawing_mode:
            if self.drawing_mode == "crop":
                if self.current_shape and self.current_pixmap:
                    rect = self.current_shape.rect().normalized()
                    self.scene.removeItem(self.current_shape)
                    self.current_shape = None
                    if rect.width() > 0 and rect.height() > 0:
                        self._create_crop_preview(rect)
            elif self.drawing_mode in ["rect", "ellipse"]:
                if self.current_shape:
                    shape_info = {
                        "id": self.shape_counter,
                        "uid": f"sh_{uuid.uuid4().hex}",
                        "type": (
                            "Rectangle" if self.drawing_mode == "rect" else "Circle"
                        ),
                        "item": self.current_shape,
                        "role": "include",  # Default role is include.
                        "isNew": True,
                        "locked_after_measurements": False,
                    }
                    self.shapes.append(shape_info)
                    self.shape_counter += 1
                    if self.shape_updated_callback:
                        self.shape_updated_callback()
            elif self.drawing_mode == "profile":
                if self.current_shape and len(self.current_profile_points) >= 2:
                    profile_info = {
                        "id": f"profile_{uuid.uuid4().hex[:8]}",
                        "type": "Profile",
                        "item": self.current_shape,
                        "points": list(self.current_profile_points),
                        "active": True,
                    }
                    self.profile_paths = [profile_info]
                    if self.shape_updated_callback:
                        self.shape_updated_callback()
                elif self.current_shape is not None:
                    self.scene.removeItem(self.current_shape)

            self.start_point = None
            self.current_shape = None
            self.current_profile_points = []
        else:
            super().mouseReleaseEvent(event)
