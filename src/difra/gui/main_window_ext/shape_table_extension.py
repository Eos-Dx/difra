import logging
import uuid

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QBrush, QColor, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QInputDialog,
    QMessageBox,
)

from difra.gui.extra.resizable_zone import ResizableSquareItem, ResizableZoneItem

logger = logging.getLogger(__name__)


class ShapeTableMixin:
    DEFAULT_CALIBRATION_SQUARE_SIDE_MM = 18.35
    DEFAULT_HOLDER_CIRCLE_DIAMETER_MM = 15.18
    DEFAULT_SAMPLE_HOLDER_LENGTH_MM = 65.45
    DEFAULT_LOAD_POSITION_MM = (-13.9, -6.0)
    DEFAULT_BEAM_CENTER_MM = (6.15, -9.15)
    ROLE_CALIBRATION_SQUARE = "calibration square"
    ROLE_HOLDER_CIRCLE = "holder circle"

    def _ensure_shape_calibration_defaults(self):
        if not hasattr(self, "sample_photo_calibration_square_mm_default"):
            self.sample_photo_calibration_square_mm_default = float(
                self.DEFAULT_CALIBRATION_SQUARE_SIDE_MM
            )
        if not hasattr(self, "sample_photo_holder_circle_mm_default"):
            self.sample_photo_holder_circle_mm_default = float(
                self.DEFAULT_HOLDER_CIRCLE_DIAMETER_MM
            )
        if not hasattr(self, "sample_photo_holder_length_mm"):
            self.sample_photo_holder_length_mm = float(
                self.DEFAULT_SAMPLE_HOLDER_LENGTH_MM
            )
        if not hasattr(self, "sample_photo_load_position_mm"):
            self.sample_photo_load_position_mm = tuple(self.DEFAULT_LOAD_POSITION_MM)
        if not hasattr(self, "sample_photo_beam_center_mm"):
            self.sample_photo_beam_center_mm = tuple(self.DEFAULT_BEAM_CENTER_MM)
        if not hasattr(self, "sample_photo_rotation_confirmed"):
            self.sample_photo_rotation_confirmed = False
        if not hasattr(self, "sample_photo_rotation_deg"):
            self.sample_photo_rotation_deg = 0
        if not hasattr(self, "_sample_photo_rotation_prompted"):
            self._sample_photo_rotation_prompted = False
        if not hasattr(self, "_sample_photo_rotation_applied"):
            self._sample_photo_rotation_applied = False
        if not hasattr(self, "sample_photo_workspace_image_type"):
            self.sample_photo_workspace_image_type = "sample"

    def _reset_sample_photo_rotation_state(self):
        self._ensure_shape_calibration_defaults()
        self.sample_photo_rotation_confirmed = False
        self.sample_photo_rotation_deg = 0
        self.sample_photo_workspace_image_type = "sample"
        self._sample_photo_has_explicit_holder_circle = False
        self._sample_photo_rotation_prompted = False
        self._sample_photo_rotation_applied = False
        image_view = getattr(self, "image_view", None)
        image_item = getattr(image_view, "image_item", None)
        if image_item is not None:
            try:
                image_item.setRotation(0)
            except Exception:
                logger.debug("Failed to reset sample photo image rotation", exc_info=True)
        if image_view is not None:
            try:
                image_view.rotation_angle = 0
            except Exception:
                logger.debug("Failed to reset image_view rotation angle", exc_info=True)
        self._update_sample_photo_rotation_ui()

    def _update_sample_photo_rotation_ui(self):
        self._ensure_shape_calibration_defaults()
        status_label = getattr(self, "rotationStatusLabel", None)
        rotate_button = getattr(self, "rotateSamplePhotoBtn", None)
        is_rotated = bool(getattr(self, "sample_photo_rotation_confirmed", False))
        can_rotate = bool(getattr(self, "_sample_photo_has_explicit_holder_circle", False)) and float(
            getattr(self, "pixel_to_mm_ratio", 0.0) or 0.0
        ) > 0.0

        if status_label is not None:
            if is_rotated:
                status_label.setText(
                    "Image rotated 180°. Ensure the sample is physically rotated."
                )
                try:
                    status_label.setStyleSheet(
                        "color: #1B5E20; font-size: 9px; margin: 1px; font-weight: 600;"
                    )
                except Exception:
                    pass
            elif can_rotate:
                status_label.setText("Image not rotated yet. Confirm 180° before generating points.")
                try:
                    status_label.setStyleSheet(
                        "color: #8D6E63; font-size: 9px; margin: 1px;"
                    )
                except Exception:
                    pass
            else:
                status_label.setText("Set calibration square or holder circle to enable rotation.")
                try:
                    status_label.setStyleSheet(
                        "color: #666; font-size: 9px; margin: 1px;"
                    )
                except Exception:
                    pass

        if rotate_button is not None:
            rotate_button.setEnabled(bool(can_rotate and not is_rotated))
            rotate_button.setText("Already Rotated" if is_rotated else "Rotate 180°")

    def _handle_sample_photo_rotate_clicked(self):
        self._ensure_shape_calibration_defaults()
        if bool(getattr(self, "sample_photo_rotation_confirmed", False)):
            QMessageBox.information(
                self,
                "Image Already Rotated",
                "The working image is already rotated by 180°.\n\n"
                "Ensure the physical sample holder is also rotated before measurement.",
            )
            self._update_sample_photo_rotation_ui()
            return

        if not bool(getattr(self, "_sample_photo_has_explicit_holder_circle", False)):
            QMessageBox.information(
                self,
                "Set Calibration First",
                "Define a calibration square or holder circle first to determine the beam center and enable rotation.",
            )
            self._update_sample_photo_rotation_ui()
            return

        self._maybe_prompt_sample_photo_rotation(force_prompt=True)

    def _get_image_scene_center_px(self):
        image_view = getattr(self, "image_view", None)
        image_item = getattr(image_view, "image_item", None)
        if image_item is None:
            return None
        try:
            local_center = image_item.boundingRect().center()
            scene_center = image_item.mapToScene(local_center)
            return (float(scene_center.x()), float(scene_center.y()))
        except Exception:
            return None

    @staticmethod
    def _rotate_xy_180(x_value: float, y_value: float, center_x: float, center_y: float):
        return (
            2.0 * float(center_x) - float(x_value),
            2.0 * float(center_y) - float(y_value),
        )

    def _apply_sample_photo_rotation_to_workspace(self, *, rotate_geometry: bool):
        self._ensure_shape_calibration_defaults()
        image_view = getattr(self, "image_view", None)
        image_item = getattr(image_view, "image_item", None)
        rotation_deg = int(getattr(self, "sample_photo_rotation_deg", 0) or 0)
        scene_center = self._get_image_scene_center_px()

        if image_item is not None:
            try:
                image_item.setTransformOriginPoint(image_item.boundingRect().center())
                image_item.setRotation(rotation_deg)
                image_view.rotation_angle = rotation_deg
            except Exception:
                logger.debug("Failed to rotate workspace image item", exc_info=True)

        if not rotate_geometry or rotation_deg % 360 == 0 or scene_center is None:
            self._sample_photo_rotation_applied = bool(rotation_deg % 360)
            return

        center_x, center_y = scene_center

        for shape_info in list(getattr(image_view, "shapes", []) or []):
            item = shape_info.get("item")
            if item is None:
                continue
            payload = self._shape_center_and_extent(shape_info)
            if payload is None:
                continue
            rect, shape_cx, shape_cy = payload
            rotated_cx, rotated_cy = self._rotate_xy_180(
                shape_cx, shape_cy, center_x, center_y
            )
            try:
                if isinstance(item, ResizableSquareItem):
                    side = float(item.get_side())
                    callback = getattr(item, "geometry_changed_callback", None)
                    item.geometry_changed_callback = None
                    item._center_x = float(rotated_cx)
                    item._center_y = float(rotated_cy)
                    half = side / 2.0
                    item.setRect(
                        float(rotated_cx) - half,
                        float(rotated_cy) - half,
                        side,
                        side,
                    )
                    updater = getattr(item, "_update_handle_positions", None)
                    if callable(updater):
                        updater()
                    item.geometry_changed_callback = callback
                elif isinstance(item, ResizableZoneItem):
                    radius = float(item.get_radius())
                    callback = getattr(item, "geometry_changed_callback", None)
                    item.geometry_changed_callback = None
                    item._center_x = float(rotated_cx)
                    item._center_y = float(rotated_cy)
                    item.setRect(
                        float(rotated_cx) - radius,
                        float(rotated_cy) - radius,
                        2.0 * radius,
                        2.0 * radius,
                    )
                    updater = getattr(item, "_update_handle_positions", None)
                    if callable(updater):
                        updater()
                    item.geometry_changed_callback = callback
                elif hasattr(item, "setRect"):
                    item.setRect(
                        float(rotated_cx) - rect.width() / 2.0,
                        float(rotated_cy) - rect.height() / 2.0,
                        rect.width(),
                        rect.height(),
                    )
                shape_info["center_px"] = (float(rotated_cx), float(rotated_cy))
            except Exception:
                logger.debug("Failed to rotate calibration shape", exc_info=True)

        move_point = getattr(self, "_move_point_and_zone", None)
        points_dict = getattr(image_view, "points_dict", None) or {}
        for point_type in ("generated", "user"):
            point_items = list((points_dict.get(point_type) or {}).get("points", []) or [])
            zone_items = list((points_dict.get(point_type) or {}).get("zones", []) or [])
            for index, point_item in enumerate(point_items):
                try:
                    point_center = point_item.sceneBoundingRect().center()
                    rotated_x, rotated_y = self._rotate_xy_180(
                        point_center.x(), point_center.y(), center_x, center_y
                    )
                    zone_item = zone_items[index] if index < len(zone_items) else None
                    if callable(move_point):
                        move_point(point_item, zone_item, rotated_x, rotated_y)
                except Exception:
                    logger.debug("Failed to rotate workspace point", exc_info=True)

        rotated_holder_center = None
        fallback_center = None
        for shape_info in list(getattr(image_view, "shapes", []) or []):
            role = str(shape_info.get("role", "") or "").lower()
            center = shape_info.get("center_px")
            if not center or len(center) < 2:
                continue
            center_tuple = (float(center[0]), float(center[1]))
            if role == self.ROLE_HOLDER_CIRCLE:
                rotated_holder_center = center_tuple
                break
            if role == self.ROLE_CALIBRATION_SQUARE and fallback_center is None:
                fallback_center = center_tuple
        active_center = rotated_holder_center or fallback_center
        if active_center is not None:
            self.include_center = active_center
            self.sample_holder_center_px = active_center

        self._sample_photo_rotation_applied = True
        try:
            self.update_shape_table()
        except Exception:
            logger.debug("Failed to refresh shape table after workspace rotation", exc_info=True)
        try:
            self.update_points_table()
        except Exception:
            logger.debug("Failed to refresh points table after workspace rotation", exc_info=True)
        refresh_points = getattr(self, "refresh_point_visual_states", None)
        if callable(refresh_points):
            try:
                refresh_points()
            except Exception:
                logger.debug("Failed to refresh point visuals after workspace rotation", exc_info=True)
        try:
            image_view.scene.update()
        except Exception:
            pass

    def _remove_shape_overlay_items(self, shape_info):
        for key in ("diagonals", "center_marker", "stage_limit_outline"):
            extra_items = shape_info.get(key)
            if isinstance(extra_items, list):
                for extra_item in extra_items:
                    try:
                        self.image_view.scene.removeItem(extra_item)
                    except Exception:
                        pass
            elif extra_items is not None:
                try:
                    self.image_view.scene.removeItem(extra_items)
                except Exception:
                    pass
            shape_info.pop(key, None)

    def _get_stage_reference_mm(self):
        # Keep stage-limit overlay in the same coordinate frame as mm_to_pixels(),
        # which uses these spinboxes as stage reference for beam-cross conversion.
        x_widget = getattr(self, "real_x_pos_mm", None)
        y_widget = getattr(self, "real_y_pos_mm", None)
        try:
            ref_x = float(x_widget.value()) if x_widget is not None else 0.0
            ref_y = float(y_widget.value()) if y_widget is not None else 0.0
            return ref_x, ref_y
        except Exception:
            return 0.0, 0.0

    def _draw_stage_limit_outline(self, shape_info, cx: float, cy: float) -> None:
        if not hasattr(self, "_get_stage_limits"):
            return

        try:
            limits = self._get_stage_limits()
        except Exception:
            limits = None
        if not limits:
            return

        try:
            px_per_mm = float(getattr(self, "pixel_to_mm_ratio", 0.0) or 0.0)
        except Exception:
            px_per_mm = 0.0
        if px_per_mm <= 0.0:
            return

        ref_x_mm, ref_y_mm = self._get_stage_reference_mm()
        x_min, x_max = limits["x"]
        y_min, y_max = limits["y"]

        x_a = cx + (ref_x_mm - float(x_min)) * px_per_mm
        x_b = cx + (ref_x_mm - float(x_max)) * px_per_mm
        y_a = cy + (ref_y_mm - float(y_min)) * px_per_mm
        y_b = cy + (ref_y_mm - float(y_max)) * px_per_mm

        outline = QGraphicsRectItem(
            min(x_a, x_b),
            min(y_a, y_b),
            abs(x_b - x_a),
            abs(y_b - y_a),
        )
        outline.setPen(QPen(QColor("#C62828"), 4))
        try:
            outline.setBrush(QBrush(Qt.NoBrush))
        except Exception:
            pass
        try:
            outline.setZValue(10_000)
        except Exception:
            pass
        self.image_view.scene.addItem(outline)
        shape_info["stage_limit_outline"] = outline

    def refresh_stage_limit_overlays(self):
        for shape_info in getattr(self.image_view, "shapes", []):
            if shape_info.get("role") in (
                "sample holder",
                self.ROLE_HOLDER_CIRCLE,
                self.ROLE_CALIBRATION_SQUARE,
            ):
                self.apply_shape_role(shape_info)

    def _shape_center_and_extent(self, shape_info):
        item = shape_info.get("item")
        if item is None:
            return None
        if isinstance(item, ResizableSquareItem):
            cx, cy = item.get_center()
            side = item.get_side()
            rect = QRectF(cx - side / 2.0, cy - side / 2.0, side, side)
        elif isinstance(item, ResizableZoneItem):
            cx, cy = item.get_center()
            diameter = 2.0 * item.get_radius()
            rect = QRectF(cx - diameter / 2.0, cy - diameter / 2.0, diameter, diameter)
        else:
            rect = item.rect() if hasattr(item, "rect") else item.sceneBoundingRect()
            cx = rect.x() + rect.width() / 2.0
            cy = rect.y() + rect.height() / 2.0
        return rect, cx, cy

    def _replace_shape_item(self, shape_info, new_item):
        old_item = shape_info.get("item")
        if old_item is new_item:
            return new_item

        try:
            pen = old_item.pen() if old_item is not None else QPen(QColor("purple"), 2)
        except Exception:
            pen = QPen(QColor("purple"), 2)
        try:
            selected = bool(old_item.isSelected()) if old_item is not None else False
        except Exception:
            selected = False
        if old_item is not None:
            try:
                self.image_view.scene.removeItem(old_item)
            except Exception:
                pass
        new_item.setPen(pen)
        try:
            new_item.setZValue(5)
        except Exception:
            pass
        callback = lambda: self._on_shape_geometry_changed(shape_info)
        try:
            new_item.geometry_changed_callback = callback
        except Exception:
            pass
        self.image_view.scene.addItem(new_item)
        shape_info["item"] = new_item
        if selected:
            try:
                new_item.setSelected(True)
            except Exception:
                pass
        return new_item

    def _on_shape_geometry_changed(self, shape_info):
        try:
            self.apply_shape_role(shape_info)
        except Exception:
            logger.debug("Failed to reapply shape role after geometry change", exc_info=True)
        try:
            self.update_shape_table()
        except Exception:
            logger.debug("Failed to update shape table after geometry change", exc_info=True)
        refresh_points = getattr(self, "update_points_table", None)
        if callable(refresh_points):
            try:
                refresh_points()
            except Exception:
                logger.debug("Failed to update points table after shape geometry change", exc_info=True)

    def _prompt_physical_size_mm(self, *, role: str, current_value: float | None = None) -> float | None:
        self._ensure_shape_calibration_defaults()
        is_square = role == self.ROLE_CALIBRATION_SQUARE
        default_value = (
            self.sample_photo_calibration_square_mm_default
            if is_square
            else self.sample_photo_holder_circle_mm_default
        )
        title = "Calibration Square Size" if is_square else "Holder Circle Diameter"
        label = (
            "Square side in mm:"
            if is_square
            else "Circle diameter in mm:"
        )
        value, ok = QInputDialog.getDouble(
            self,
            title,
            label,
            float(current_value if current_value is not None else default_value),
            0.01,
            500.0,
            2,
        )
        if not ok:
            return None
        value = float(value)
        if is_square:
            self.sample_photo_calibration_square_mm_default = value
        else:
            self.sample_photo_holder_circle_mm_default = value
        return value

    def _refresh_sample_photo_calibration(self):
        self._ensure_shape_calibration_defaults()
        square_info = None
        circle_info = None
        has_calibration_geometry = False
        for shape_info in getattr(self.image_view, "shapes", []):
            role = str(shape_info.get("role", "") or "").lower()
            if role == self.ROLE_CALIBRATION_SQUARE and square_info is None:
                square_info = shape_info
            elif role == self.ROLE_HOLDER_CIRCLE and circle_info is None:
                circle_info = shape_info
        has_calibration_geometry = bool(square_info is not None or circle_info is not None)

        ratio = 0.0
        ratio_source = ""
        if square_info is not None:
            payload = self._shape_center_and_extent(square_info)
            if payload is not None:
                rect, cx, cy = payload
                side_mm = float(square_info.get("physical_size_mm") or self.sample_photo_calibration_square_mm_default)
                if side_mm > 0:
                    ratio = float(min(rect.width(), rect.height())) / side_mm
                    ratio_source = self.ROLE_CALIBRATION_SQUARE
                square_info["center_px"] = (float(cx), float(cy))
        if circle_info is not None:
            payload = self._shape_center_and_extent(circle_info)
            if payload is not None:
                rect, cx, cy = payload
                circle_info["center_px"] = (float(cx), float(cy))
                if ratio <= 0.0:
                    diameter_mm = float(circle_info.get("physical_size_mm") or self.sample_photo_holder_circle_mm_default)
                    if diameter_mm > 0:
                        ratio = float(max(rect.width(), rect.height())) / diameter_mm
                        ratio_source = self.ROLE_HOLDER_CIRCLE

        if ratio > 0.0:
            self.pixel_to_mm_ratio = float(ratio)
        if circle_info is not None:
            center = circle_info.get("center_px")
            if center is not None:
                self.include_center = (float(center[0]), float(center[1]))
                self.sample_holder_center_px = tuple(self.include_center)
        elif square_info is not None:
            center = square_info.get("center_px")
            if center is not None:
                self.include_center = (float(center[0]), float(center[1]))
                self.sample_holder_center_px = tuple(self.include_center)
        if ratio_source:
            self.sample_photo_scale_source = ratio_source
        self._sample_photo_has_explicit_holder_circle = bool(has_calibration_geometry)
        self._update_sample_photo_rotation_ui()
        if hasattr(self, "update_conversion_label"):
            self.update_conversion_label()
        if hasattr(self, "update_coordinates"):
            try:
                self.update_coordinates()
            except Exception:
                logger.debug("Failed to update coordinates after calibration refresh", exc_info=True)
        self._maybe_prompt_sample_photo_rotation()

    def _prioritize_holder_circle_shape(self, shape_info):
        if not shape_info:
            return
        shapes = getattr(self.image_view, "shapes", None)
        if not isinstance(shapes, list):
            return
        try:
            shapes.remove(shape_info)
        except ValueError:
            return
        shapes.insert(0, shape_info)

    def _delete_shape_info(self, shape_info):
        if not shape_info:
            return
        self._remove_shape_overlay_items(shape_info)
        item = shape_info.get("item")
        if item is not None:
            try:
                self.image_view.scene.removeItem(item)
            except Exception:
                pass
        try:
            self.image_view.shapes.remove(shape_info)
        except Exception:
            pass

    def _remove_conflicting_calibration_shapes(self, keep_shape_info, target_role: str):
        if target_role not in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
            return
        for shape_info in list(getattr(self.image_view, "shapes", []) or []):
            if shape_info is keep_shape_info:
                continue
            role = str(shape_info.get("role", "") or "").lower()
            if role in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
                self._delete_shape_info(shape_info)

    def _maybe_prompt_sample_photo_rotation(self, *, force_prompt: bool = False):
        self._ensure_shape_calibration_defaults()
        if self._sample_photo_rotation_prompted and not force_prompt:
            return
        if not bool(getattr(self, "_sample_photo_has_explicit_holder_circle", False)):
            return
        if getattr(self, "pixel_to_mm_ratio", 0.0) in (0, 0.0):
            return
        holder_center = getattr(self, "sample_holder_center_px", None)
        if holder_center is None:
            return
        if bool(getattr(self, "sample_photo_rotation_confirmed", False)):
            self.sample_photo_workspace_image_type = "sample_rotated"
            self._update_sample_photo_rotation_ui()
            return
        reply = QMessageBox.question(
            self,
            "Rotate Sample Holder",
            "Pixel-to-mm calibration is now available.\n\n"
            "Rotate sample holder physically by 180° before measurement and use the same 180° mapping in DIFRA?\n\n"
            "DIFRA will keep the raw photo and also store a rotated working image in the session container for point placement.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        self.sample_photo_rotation_confirmed = reply == QMessageBox.Yes
        self.sample_photo_rotation_deg = 180 if self.sample_photo_rotation_confirmed else 0
        self.sample_photo_workspace_image_type = (
            "sample_rotated" if self.sample_photo_rotation_confirmed else "sample"
        )
        self._sample_photo_rotation_prompted = True
        self._apply_sample_photo_rotation_to_workspace(
            rotate_geometry=bool(self.sample_photo_rotation_confirmed)
        )
        self._update_sample_photo_rotation_ui()
        if hasattr(self, "update_points_table"):
            try:
                self.update_points_table()
            except Exception:
                logger.debug("Failed to refresh points after rotation confirmation", exc_info=True)
        sync_workspace = getattr(self, "sync_workspace_to_session_container", None)
        get_shapes = getattr(self, "_get_shapes", None)
        get_points = getattr(self, "_get_zone_points", None)
        if callable(sync_workspace) and callable(get_shapes) and callable(get_points):
            try:
                sync_workspace(
                    state={
                        "shapes": get_shapes(),
                        "zone_points": get_points(),
                    }
                )
            except Exception:
                logger.debug("Failed to persist sample photo rotation confirmation", exc_info=True)

    def create_shape_table(self):
        self._ensure_shape_calibration_defaults()
        self.shapeDock = QDockWidget("Shapes", self)
        self.shapeDock.setObjectName("ShapesDock")
        # Increase the column count to include a "Role" column.
        self.shapeTable = QTableWidget(0, 7, self)
        self.shapeTable.setHorizontalHeaderLabels(
            ["ID", "Type", "X", "Y", "Width", "Height", "Role"]
        )
        self.shapeTable.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
        )
        self.shapeTable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.shapeTable.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.shapeTable.cellChanged.connect(self.onShapeTableCellChanged)
        self.shapeDock.setWidget(self.shapeTable)
        self.addDockWidget(Qt.RightDockWidgetArea, self.shapeDock)
        self.setupShapeTableContextMenu()
        self.setupDeleteShortcut()

    def setupShapeTableContextMenu(self):
        self.shapeTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.shapeTable.customContextMenuRequested.connect(self.onShapeTableContextMenu)

    def onShapeTableContextMenu(self, pos):
        item = self.shapeTable.itemAt(pos)
        if not item:
            return

        row = item.row()
        model_index = self.shapeTable.model().index(row, 0)
        if not self.shapeTable.selectionModel().isRowSelected(row, model_index):
            self.shapeTable.selectRow(row)

        menu = QMenu(self.shapeTable)
        includeAct = menu.addAction("Include")
        excludeAct = menu.addAction("Exclude")
        calibSquareAct = menu.addAction("Define As Calibration Square...")
        holderCircleAct = menu.addAction("Define As Holder Circle...")
        editPhysicalSizeAct = menu.addAction("Edit Physical Size...")
        menu.addSeparator()
        deleteAct = menu.addAction("Delete")
        action = menu.exec_(self.shapeTable.viewport().mapToGlobal(pos))
        if action == includeAct:
            self.update_shape_role(row, "include")
        elif action == excludeAct:
            self.update_shape_role(row, "exclude")
        elif action == calibSquareAct:
            self.update_shape_role(row, self.ROLE_CALIBRATION_SQUARE)
        elif action == holderCircleAct:
            self.update_shape_role(row, self.ROLE_HOLDER_CIRCLE)
        elif action == editPhysicalSizeAct:
            self.edit_shape_physical_size(row)
        elif action == deleteAct:
            self.delete_shapes_from_table()

    def edit_shape_physical_size(self, row):
        item = self.shapeTable.item(row, 0)
        shape_uid = (
            str(item.data(Qt.UserRole)).strip()
            if item is not None and item.data(Qt.UserRole) is not None
            else ""
        )
        if not shape_uid:
            return
        for shape_info in self.image_view.shapes:
            if str(shape_info.get("uid", "")).strip() != shape_uid:
                continue
            role = str(shape_info.get("role", "") or "").lower()
            if role not in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
                QMessageBox.information(
                    self,
                    "No Physical Size",
                    "This shape is not a calibration square or holder circle.",
                )
                return
            current_value = float(shape_info.get("physical_size_mm") or 0.0) or None
            value = self._prompt_physical_size_mm(role=role, current_value=current_value)
            if value is None:
                return
            shape_info["physical_size_mm"] = float(value)
            self.apply_shape_role(shape_info)
            self.update_shape_table()
            return

    def edit_shape_physical_size_by_info(self, shape_info):
        if not shape_info:
            return
        role = str(shape_info.get("role", "") or "").lower()
        if role not in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
            QMessageBox.information(
                self,
                "No Physical Size",
                "This shape is not a calibration square or holder circle.",
            )
            return
        current_value = float(shape_info.get("physical_size_mm") or 0.0) or None
        value = self._prompt_physical_size_mm(role=role, current_value=current_value)
        if value is None:
            return
        shape_info["physical_size_mm"] = float(value)
        self.apply_shape_role(shape_info)
        self.update_shape_table()

    def define_shape_as_calibration_role(self, shape_info, role: str):
        if not shape_info:
            return
        current_value = shape_info.get("physical_size_mm")
        if role in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
            prompted = self._prompt_physical_size_mm(role=role, current_value=current_value)
            if prompted is None:
                return
            shape_info["physical_size_mm"] = float(prompted)
            self._remove_conflicting_calibration_shapes(shape_info, role)
        shape_info["role"] = role
        if role == self.ROLE_HOLDER_CIRCLE:
            self._prioritize_holder_circle_shape(shape_info)
        shape_info.pop("isNew", None)
        self.apply_shape_role(shape_info)
        self.update_shape_table()

    def apply_shape_role(self, shape_info):
        """Update the appearance of the shape based on its role."""
        self._ensure_shape_calibration_defaults()
        role = shape_info.get("role", "include")
        item = shape_info["item"]

        # Remove any previous extra items if present.
        self._remove_shape_overlay_items(shape_info)

        if role == "include":
            pen = QPen(QColor("green"), 2)
            item.setPen(pen)
        elif role == "exclude":
            pen = QPen(QColor("red"), 2)
            item.setPen(pen)
        elif role == "sample holder":
            rect = item.rect() if hasattr(item, "rect") else item.boundingRect()
            cx = rect.x() + rect.width() / 2
            cy = rect.y() + rect.height() / 2
            side = min(rect.width(), rect.height())
            new_side = side
            new_x = cx - new_side / 2
            new_y = cy - new_side / 2
            if hasattr(item, "setRect"):
                item.setRect(new_x, new_y, new_side, new_side)
            pen = QPen(QColor("purple"), 2)
            item.setPen(pen)
            diag1 = QGraphicsLineItem(new_x, new_y, new_x + new_side, new_y + new_side)
            diag2 = QGraphicsLineItem(new_x + new_side, new_y, new_x, new_y + new_side)
            diag_pen = QPen(QColor("purple"), 1)
            diag1.setPen(diag_pen)
            diag2.setPen(diag_pen)
            self.image_view.scene.addItem(diag1)
            self.image_view.scene.addItem(diag2)
            center_radius = 3
            center_point = QGraphicsEllipseItem(
                cx - center_radius,
                cy - center_radius,
                2 * center_radius,
                2 * center_radius,
            )
            center_point.setBrush(QColor("purple"))
            center_point.setPen(QPen(Qt.NoPen))
            self.image_view.scene.addItem(center_point)
            shape_info["diagonals"] = [diag1, diag2]
            shape_info["center_marker"] = center_point
            pixels_per_mm = new_side / 18.0
            shape_info["pixels_per_mm"] = pixels_per_mm
            self.pixel_to_mm_ratio = pixels_per_mm
            self.include_center = (cx, cy)
            self.update_conversion_label()
            self._draw_stage_limit_outline(shape_info, cx, cy)
        elif role == self.ROLE_CALIBRATION_SQUARE:
            payload = self._shape_center_and_extent(shape_info)
            rect, cx, cy = payload if payload is not None else (item.rect(), 0.0, 0.0)
            side = min(rect.width(), rect.height())
            new_side = max(10.0, side)
            if not isinstance(item, ResizableSquareItem):
                item = self._replace_shape_item(shape_info, ResizableSquareItem(cx, cy, new_side))
            callback = getattr(item, "geometry_changed_callback", None)
            item.geometry_changed_callback = None
            item.set_side(new_side)
            item.geometry_changed_callback = callback
            pen = QPen(QColor("purple"), 2)
            item.setPen(pen)
            shape_info["type"] = "Square"
            shape_info["center_px"] = (float(cx), float(cy))
            shape_info["physical_size_mm"] = float(
                shape_info.get("physical_size_mm") or self.sample_photo_calibration_square_mm_default
            )

            new_x = cx - new_side / 2
            new_y = cy - new_side / 2
            diag1 = QGraphicsLineItem(new_x, new_y, new_x + new_side, new_y + new_side)
            diag2 = QGraphicsLineItem(new_x + new_side, new_y, new_x, new_y + new_side)
            diag_pen = QPen(QColor("purple"), 1)
            diag1.setPen(diag_pen)
            diag2.setPen(diag_pen)
            self.image_view.scene.addItem(diag1)
            self.image_view.scene.addItem(diag2)

            # Draw center marker.
            center_radius = 3
            center_point = QGraphicsEllipseItem(
                cx - center_radius,
                cy - center_radius,
                2 * center_radius,
                2 * center_radius,
            )
            center_point.setBrush(QColor("purple"))
            center_point.setPen(QPen(Qt.NoPen))
            self.image_view.scene.addItem(center_point)

            shape_info["diagonals"] = [diag1, diag2]
            shape_info["center_marker"] = center_point
            pixels_per_mm = new_side / float(shape_info["physical_size_mm"])
            shape_info["pixels_per_mm"] = pixels_per_mm
            self._draw_stage_limit_outline(shape_info, cx, cy)
            self._refresh_sample_photo_calibration()
        elif role == self.ROLE_HOLDER_CIRCLE:
            payload = self._shape_center_and_extent(shape_info)
            rect, cx, cy = payload if payload is not None else (item.rect(), 0.0, 0.0)
            diameter = max(rect.width(), rect.height())
            radius = max(5.0, diameter / 2.0)
            if not isinstance(item, ResizableZoneItem):
                item = self._replace_shape_item(shape_info, ResizableZoneItem(cx, cy, radius))
            callback = getattr(item, "geometry_changed_callback", None)
            item.geometry_changed_callback = None
            item.set_radius(radius)
            item.geometry_changed_callback = callback
            pen = QPen(QColor("#1565C0"), 2)
            item.setPen(pen)
            shape_info["type"] = "Circle"
            shape_info["role"] = self.ROLE_HOLDER_CIRCLE
            shape_info["center_px"] = (float(cx), float(cy))
            shape_info["physical_size_mm"] = float(
                shape_info.get("physical_size_mm") or self.sample_photo_holder_circle_mm_default
            )
            diameter = 2.0 * float(item.get_radius())
            shape_info["pixels_per_mm"] = diameter / float(shape_info["physical_size_mm"])
            horiz = QGraphicsLineItem(cx - diameter / 2.0, cy, cx + diameter / 2.0, cy)
            vert = QGraphicsLineItem(cx, cy - diameter / 2.0, cx, cy + diameter / 2.0)
            cross_pen = QPen(QColor("#1565C0"), 1)
            horiz.setPen(cross_pen)
            vert.setPen(cross_pen)
            self.image_view.scene.addItem(horiz)
            self.image_view.scene.addItem(vert)
            center_radius = 3
            center_point = QGraphicsEllipseItem(
                cx - center_radius,
                cy - center_radius,
                2 * center_radius,
                2 * center_radius,
            )
            center_point.setBrush(QColor("#1565C0"))
            center_point.setPen(QPen(Qt.NoPen))
            self.image_view.scene.addItem(center_point)
            shape_info["diagonals"] = [horiz, vert]
            shape_info["center_marker"] = center_point
            self._draw_stage_limit_outline(shape_info, cx, cy)
            self._refresh_sample_photo_calibration()
        else:
            pen = QPen(QColor("black"), 2)
            item.setPen(pen)

        # Always update the scene to reflect changes.
        self.image_view.scene.update()

    def update_shape_role(self, row, role):
        try:
            item = self.shapeTable.item(row, 0)
            shape_uid = (
                str(item.data(Qt.UserRole)).strip()
                if item is not None and item.data(Qt.UserRole) is not None
                else ""
            )
            if not shape_uid:
                return
            # Update the role in shape_info and apply appearance changes.
            for shape_info in self.image_view.shapes:
                if not shape_info.get("uid"):
                    shape_info["uid"] = f"sh_{uuid.uuid4().hex}"
                if shape_uid and str(shape_info.get("uid", "")).strip() == shape_uid:
                    current_value = None
                    if role in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
                        current_value = shape_info.get("physical_size_mm")
                        prompted = self._prompt_physical_size_mm(role=role, current_value=current_value)
                        if prompted is None:
                            return
                        shape_info["physical_size_mm"] = float(prompted)
                        self._remove_conflicting_calibration_shapes(shape_info, role)
                    shape_info["role"] = role
                    if role == self.ROLE_HOLDER_CIRCLE:
                        self._prioritize_holder_circle_shape(shape_info)
                    # Remove "isNew" flag if present.
                    shape_info.pop("isNew", None)
                    # Update appearance using the new helper method.
                    self.apply_shape_role(shape_info)
                    self._refresh_sample_photo_calibration()
                    break
            self.update_shape_table()  # Refresh the table to display the updated role.
        except Exception as e:
            logger.warning("Error updating shape role: %s", e, exc_info=True)

    def update_shape_table(self):
        shapes = getattr(self.image_view, "shapes", [])
        self.shapeTable.blockSignals(True)
        self.shapeTable.setRowCount(len(shapes))
        for row, shape_info in enumerate(shapes):
            # If a role has already been defined, enforce the appearance.
            if "role" in shape_info:
                self.apply_shape_role(shape_info)
            else:
                # Default role is "include" if none is set.
                shape_info["role"] = "include"

            role = shape_info["role"]
            item = shape_info.get("item")
            rect = item.rect() if hasattr(item, "rect") else item.boundingRect()
            if not shape_info.get("uid"):
                shape_info["uid"] = f"sh_{uuid.uuid4().hex}"

            # Update table cells.
            id_item = QTableWidgetItem(str(shape_info.get("id", "")))
            id_item.setData(Qt.UserRole, str(shape_info.get("uid")))
            self.shapeTable.setItem(row, 0, id_item)
            self.shapeTable.setItem(
                row, 1, QTableWidgetItem(shape_info.get("type", ""))
            )
            self.shapeTable.setItem(row, 2, QTableWidgetItem(f"{rect.x():.2f}"))
            self.shapeTable.setItem(row, 3, QTableWidgetItem(f"{rect.y():.2f}"))
            self.shapeTable.setItem(row, 4, QTableWidgetItem(f"{rect.width():.2f}"))
            self.shapeTable.setItem(row, 5, QTableWidgetItem(f"{rect.height():.2f}"))
            self.shapeTable.setItem(row, 6, QTableWidgetItem(role))

            # Set the row background color based on the role:
            if shape_info.get("isNew", False):
                color = QColor("gray")
            else:
                if role == "include":
                    color = QColor("lightgreen")
                elif role == "exclude":
                    color = QColor("lightcoral")
                elif role == "sample holder":
                    color = QColor("purple")
                elif role == self.ROLE_CALIBRATION_SQUARE:
                    color = QColor("#E1BEE7")
                elif role == self.ROLE_HOLDER_CIRCLE:
                    color = QColor("#BBDEFB")
                else:
                    color = QColor("white")
            for col in range(7):
                self.shapeTable.item(row, col).setBackground(QBrush(color))
        self.shapeTable.blockSignals(False)

    def onShapeTableCellChanged(self, row, column):
        try:
            # Only allow editing for geometry columns.
            if column in [2, 3, 4, 5]:
                item0 = self.shapeTable.item(row, 0)
                shape_uid = (
                    str(item0.data(Qt.UserRole)).strip()
                    if item0 is not None and item0.data(Qt.UserRole) is not None
                    else ""
                )
                if not shape_uid:
                    return
                for shape_info in self.image_view.shapes:
                    if not shape_info.get("uid"):
                        shape_info["uid"] = f"sh_{uuid.uuid4().hex}"
                    if shape_uid and str(shape_info.get("uid", "")).strip() == shape_uid:
                        item = shape_info["item"]
                        x = float(self.shapeTable.item(row, 2).text())
                        y = float(self.shapeTable.item(row, 3).text())
                        w = float(self.shapeTable.item(row, 4).text())
                        h = float(self.shapeTable.item(row, 5).text())
                        if isinstance(item, ResizableSquareItem):
                            item._center_x = x + min(w, h) / 2.0
                            item._center_y = y + min(w, h) / 2.0
                            callback = getattr(item, "geometry_changed_callback", None)
                            item.geometry_changed_callback = None
                            item.set_side(min(w, h))
                            item.geometry_changed_callback = callback
                        elif isinstance(item, ResizableZoneItem):
                            item._center_x = x + max(w, h) / 2.0
                            item._center_y = y + max(w, h) / 2.0
                            callback = getattr(item, "geometry_changed_callback", None)
                            item.geometry_changed_callback = None
                            item.set_radius(max(w, h) / 2.0)
                            item.geometry_changed_callback = callback
                        elif hasattr(item, "setRect"):
                            item.setRect(x, y, w, h)
                        if shape_info.get("role") in (
                            "sample holder",
                            self.ROLE_HOLDER_CIRCLE,
                            self.ROLE_CALIBRATION_SQUARE,
                        ):
                            self.apply_shape_role(shape_info)
                        break
        except Exception as e:
            logger.warning("Error updating shape from table: %s", e, exc_info=True)

    def setupDeleteShortcut(self):
        # Override the keyPressEvent for the shape table to capture the Delete key.
        self.shapeTable.keyPressEvent = self.shapeTableKeyPressEvent

    def shapeTableKeyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.delete_shapes_from_table()
        else:
            # Call the default handler.
            QTableWidget.keyPressEvent(self.shapeTable, event)

    def delete_shapes_from_table(self):
        # Delete selected rows from the table and remove corresponding shapes from the image view.
        selected_rows = sorted(
            {index.row() for index in self.shapeTable.selectionModel().selectedRows()},
            reverse=True,
        )
        if not selected_rows:
            selected_rows = sorted(
                {index.row() for index in self.shapeTable.selectedIndexes()},
                reverse=True,
            )
        for row in selected_rows:
            try:
                item = self.shapeTable.item(row, 0)
                shape_uid = (
                    str(item.data(Qt.UserRole)).strip()
                    if item is not None and item.data(Qt.UserRole) is not None
                    else ""
                )
            except Exception:
                continue
            if not shape_uid:
                continue
            for shape_info in list(self.image_view.shapes):
                if not shape_info.get("uid"):
                    shape_info["uid"] = f"sh_{uuid.uuid4().hex}"
                if shape_uid and str(shape_info.get("uid", "")).strip() == shape_uid:
                    shape_item = shape_info.get("item")
                    if shape_item is not None:
                        self.image_view.scene.removeItem(shape_item)
                    self._remove_shape_overlay_items(shape_info)
                    if shape_info in self.image_view.shapes:
                        self.image_view.shapes.remove(shape_info)
                    break
        self._refresh_sample_photo_calibration()
        self.update_shape_table()

    def delete_all_shapes_from_table(self):
        # Delete all rows from the table and remove all corresponding shapes from the image view.
        shapes_to_delete = list(self.image_view.shapes)
        for shape_info in shapes_to_delete:
            item = shape_info.get("item")
            if item is not None:
                try:
                    self.image_view.scene.removeItem(item)
                except RuntimeError:
                    pass
            for extra_key in ["diagonals", "center_marker", "stage_limit_outline"]:
                extra_items = shape_info.get(extra_key)
                if isinstance(extra_items, list):
                    for extra_item in extra_items:
                        try:
                            self.image_view.scene.removeItem(extra_item)
                        except RuntimeError:
                            pass
                elif extra_items is not None:
                    try:
                        self.image_view.scene.removeItem(extra_items)
                    except RuntimeError:
                        pass
        self.image_view.shapes.clear()
        self._refresh_sample_photo_calibration()
        self.update_shape_table()
