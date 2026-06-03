import logging

from difra.gui.qt_compat import Qt
from difra.gui.qt_compat import QBrush, QColor, QPen
from difra.gui.qt_compat import (
    QGraphicsRectItem,
    QInputDialog,
)

from difra.gui.extra.resizable_zone import (
    ResizableEllipseItem,
    ResizableSquareItem,
    ResizableZoneItem,
)

logger = logging.getLogger(__name__)


class ShapeCalibrationGeometryMixin:
    """Shape calibration behavior split from ShapeCalibrationMixin."""

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
        # Keep fallback overlay in the same coordinate frame as mm_to_pixels().
        x_widget = getattr(self, "real_x_pos_mm", None)
        y_widget = getattr(self, "real_y_pos_mm", None)
        try:
            ref_x = float(x_widget.value()) if x_widget is not None else 0.0
            ref_y = float(y_widget.value()) if y_widget is not None else 0.0
            return ref_x, ref_y
        except Exception:
            return 0.0, 0.0

    def _stage_mm_to_overlay_pixels(self, x_mm: float, y_mm: float, default_center_px):
        try:
            px_per_mm = float(getattr(self, "pixel_to_mm_ratio", 0.0) or 0.0)
        except Exception:
            px_per_mm = 0.0
        if px_per_mm <= 0.0:
            return None

        holder_center = getattr(self, "sample_holder_center_px", None)
        if holder_center is not None:
            try:
                holder_x = float(holder_center[0])
                holder_y = float(holder_center[1])
                beam_center = tuple(
                    getattr(
                        self, "sample_photo_beam_center_mm", self.DEFAULT_BEAM_CENTER_MM
                    )
                )
                beam_x_mm = float(beam_center[0])
                beam_y_mm = float(beam_center[1])
                return (
                    (float(x_mm) - beam_x_mm) * px_per_mm + holder_x,
                    (float(y_mm) - beam_y_mm) * px_per_mm + holder_y,
                )
            except Exception:
                logger.debug(
                    "Failed to convert stage mm to holder-centered pixels",
                    exc_info=True,
                )

        ref_x_mm, ref_y_mm = self._get_stage_reference_mm()
        center_x, center_y = default_center_px
        return (
            center_x + (ref_x_mm - float(x_mm)) * px_per_mm,
            center_y + (ref_y_mm - float(y_mm)) * px_per_mm,
        )

    def _draw_stage_limit_outline(self, shape_info, cx: float, cy: float) -> None:
        if not hasattr(self, "_get_stage_limits"):
            return

        try:
            limits = self._get_stage_limits()
        except Exception:
            limits = None
        if not limits:
            return

        x_min, x_max = limits["x"]
        y_min, y_max = limits["y"]

        a = self._stage_mm_to_overlay_pixels(
            float(x_min), float(y_min), (float(cx), float(cy))
        )
        b = self._stage_mm_to_overlay_pixels(
            float(x_max), float(y_max), (float(cx), float(cy))
        )
        if a is None or b is None:
            return
        x_a, y_a = a
        x_b, y_b = b

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
            rect = item.mapRectToScene(item.rect())
            cx = rect.x() + rect.width() / 2.0
            cy = rect.y() + rect.height() / 2.0
        elif isinstance(item, ResizableZoneItem):
            rect = item.mapRectToScene(item.rect())
            cx = rect.x() + rect.width() / 2.0
            cy = rect.y() + rect.height() / 2.0
        elif isinstance(item, ResizableEllipseItem):
            rect = item.mapRectToScene(item.rect())
            cx = rect.x() + rect.width() / 2.0
            cy = rect.y() + rect.height() / 2.0
        elif hasattr(item, "mapRectToScene") and hasattr(item, "rect"):
            rect = item.mapRectToScene(item.rect())
            cx = rect.x() + rect.width() / 2.0
            cy = rect.y() + rect.height() / 2.0
        else:
            rect = item.sceneBoundingRect()
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

        def callback():
            self._on_shape_geometry_changed(shape_info)

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
        role = str((shape_info or {}).get("role", "") or "").lower()
        if role in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
            self._clear_sample_photo_dependents(keep_shape_info=shape_info)
        try:
            self.apply_shape_role(shape_info)
        except Exception:
            logger.debug(
                "Failed to reapply shape role after geometry change", exc_info=True
            )
        try:
            self.update_shape_table()
        except Exception:
            logger.debug(
                "Failed to update shape table after geometry change", exc_info=True
            )
        refresh_points = getattr(self, "update_points_table", None)
        if callable(refresh_points):
            try:
                refresh_points()
            except Exception:
                logger.debug(
                    "Failed to update points table after shape geometry change",
                    exc_info=True,
                )

    def _clear_sample_photo_dependents(self, keep_shape_info=None):
        image_view = getattr(self, "image_view", None)
        if image_view is None:
            return

        for shape_info in list(getattr(image_view, "shapes", []) or []):
            if keep_shape_info is not None and shape_info is keep_shape_info:
                continue
            role = str(shape_info.get("role", "") or "").lower()
            if role in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
                continue
            self._delete_shape_info(shape_info)

        clear_profiles = getattr(self, "_clear_profile_paths", None)
        if callable(clear_profiles):
            try:
                clear_profiles()
            except Exception:
                logger.debug(
                    "Failed to clear profile paths after calibration change",
                    exc_info=True,
                )

        delete_points = getattr(self, "delete_all_points", None)
        if callable(delete_points):
            try:
                delete_points()
            except Exception:
                logger.debug(
                    "Failed to clear points after calibration change", exc_info=True
                )

    def _prompt_physical_size_mm(
        self, *, role: str, current_value: float | None = None
    ) -> float | None:
        self._ensure_shape_calibration_defaults()
        is_square = role == self.ROLE_CALIBRATION_SQUARE
        default_value = (
            self.sample_photo_calibration_square_mm_default
            if is_square
            else self.sample_photo_holder_circle_mm_default
        )
        title = "Calibration Square Size" if is_square else "Holder Circle Diameter"
        label = "Square side in mm:" if is_square else "Circle diameter in mm:"
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
        has_calibration_geometry = bool(
            square_info is not None or circle_info is not None
        )

        ratio = 0.0
        ratio_source = ""
        if square_info is not None:
            payload = self._shape_center_and_extent(square_info)
            if payload is not None:
                rect, cx, cy = payload
                side_mm = float(
                    square_info.get("physical_size_mm")
                    or self.sample_photo_calibration_square_mm_default
                )
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
                    diameter_mm = float(
                        circle_info.get("physical_size_mm")
                        or self.sample_photo_holder_circle_mm_default
                    )
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
                logger.debug(
                    "Failed to update coordinates after calibration refresh",
                    exc_info=True,
                )
        self._maybe_prompt_sample_photo_rotation()

    def _prioritize_holder_circle_shape(self, shape_info):
        if not shape_info:
            return
        shapes = getattr(self, "image_view", None)
        shapes = getattr(shapes, "shapes", None)
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
