import uuid

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QPainterPath, QPen
from PyQt5.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
)


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
        if not hasattr(self, "profile_paths"):
            self.profile_paths = []

    def _create_pen(self):
        return QPen(Qt.red, 2, Qt.DashLine)

    def set_drawing_mode(self, mode):
        self.drawing_mode = mode

    def mousePressEvent(self, event):
        if self.drawing_mode in ["rect", "ellipse", "crop", "profile"]:
            # Do nothing if no image is loaded.
            if self.current_pixmap is None:
                print("No image loaded. Please open an image first.")
                return
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
                rect_item = QGraphicsRectItem(rect)
                rect_item.setPen(self.pen)
                if self.drawing_mode == "rect":
                    rect_item.setFlags(
                        QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable
                    )
                self.current_shape = rect_item
                self.scene.addItem(rect_item)
            elif self.drawing_mode == "ellipse":
                ellipse_item = QGraphicsEllipseItem(rect)
                ellipse_item.setPen(self.pen)
                ellipse_item.setFlags(
                    QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable
                )
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
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drawing_mode:
            if self.drawing_mode == "crop":
                if self.current_shape and self.current_pixmap:
                    rect = self.current_shape.rect().normalized()
                    from PyQt5.QtCore import QRectF

                    image_rect = QRectF(self.current_pixmap.rect())
                    crop_rect = rect.intersected(image_rect)
                    # Remove the crop rectangle BEFORE applying the crop.
                    self.scene.removeItem(self.current_shape)
                    self.current_shape = None
                    if crop_rect.width() > 0 and crop_rect.height() > 0:
                        cropped_pixmap = self.current_pixmap.copy(
                            int(crop_rect.x()),
                            int(crop_rect.y()),
                            int(crop_rect.width()),
                            int(crop_rect.height()),
                        )
                        self.setImage(cropped_pixmap)
                    else:
                        print("Invalid crop rectangle.")
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
