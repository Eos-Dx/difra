import logging

from difra.gui.qt_compat import QPointF, QRectF, Qt
from difra.gui.qt_compat import QColor, QImage, QPixmap
from difra.gui.qt_compat import (
    QColorDialog,
    QDialog,
    QGridLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from .shape_calibration_catch_auto_detection import (
    build_catch_auto_contrast_rgba,
    detect_catch_auto_outer_geometry,
)
from .shape_calibration_catch_auto_assistant import ShapeCatchAutoAssistantMixin


logger = logging.getLogger(__name__)


class ShapeCatchAutoMixin(ShapeCatchAutoAssistantMixin):
    """Shape calibration behavior split from ShapeCalibrationMixin."""

    def _get_selected_calibration_shape_info(self):
        image_view = getattr(self, "image_view", None)
        scene = getattr(image_view, "scene", None)
        if scene is None:
            return None
        selected_items = [
            item
            for item in scene.selectedItems()
            if item is not getattr(image_view, "image_item", None)
        ]
        for shape_info in getattr(image_view, "shapes", []) or []:
            role = str(shape_info.get("role", "") or "").lower()
            if role not in (self.ROLE_HOLDER_CIRCLE, self.ROLE_CALIBRATION_SQUARE):
                continue
            item = shape_info.get("item")
            extras = list(shape_info.get("diagonals") or [])
            center_marker = shape_info.get("center_marker")
            if center_marker is not None:
                extras.append(center_marker)
            if any(sel is item or sel in extras for sel in selected_items):
                return shape_info
        for shape_info in getattr(image_view, "shapes", []) or []:
            role = str(shape_info.get("role", "") or "").lower()
            if role in (self.ROLE_HOLDER_CIRCLE, self.ROLE_CALIBRATION_SQUARE):
                return shape_info
        return None

    def _select_calibration_shape_for_editing(self, shape_info):
        if not shape_info:
            return
        image_view = getattr(self, "image_view", None)
        scene = getattr(image_view, "scene", None)
        item = shape_info.get("item")
        if scene is None or item is None:
            return
        try:
            scene.clearSelection()
        except Exception:
            logger.debug(
                "Failed to clear scene selection before selecting shape", exc_info=True
            )
        try:
            item.setSelected(True)
        except Exception:
            logger.debug(
                "Failed to select calibration shape for editing", exc_info=True
            )
        handles_visible = getattr(item, "_set_handles_visible", None)
        if callable(handles_visible):
            try:
                handles_visible(True)
            except Exception:
                logger.debug("Failed to show calibration shape handles", exc_info=True)
        set_mode = getattr(image_view, "set_drawing_mode", None)
        if callable(set_mode):
            try:
                set_mode(None)
            except Exception:
                logger.debug(
                    "Failed to switch image view back to select mode", exc_info=True
                )

    def catch_auto_selected_calibration_shape(self):
        shape_info = self._get_selected_calibration_shape_info()
        if not shape_info:
            QMessageBox.information(
                self,
                "Catch Auto",
                "Select a holder circle or calibration square first.",
            )
            return False
        return self.open_catch_auto_assistant_for_shape(shape_info)

    def _ensure_catch_auto_defaults(self):
        if not hasattr(self, "catch_auto_holder_rgb"):
            self.catch_auto_holder_rgb = tuple(self.DEFAULT_CATCH_AUTO_HOLDER_RGB)
        if not hasattr(self, "catch_auto_background_rgb"):
            self.catch_auto_background_rgb = tuple(
                self.DEFAULT_CATCH_AUTO_BACKGROUND_RGB
            )

    @staticmethod
    def _rgb_to_qcolor(rgb_value):
        try:
            red, green, blue = [int(max(0, min(255, channel))) for channel in rgb_value]
        except Exception:
            red, green, blue = 0, 0, 0
        return QColor(red, green, blue)

    def _prompt_catch_auto_colors(self):
        self._ensure_catch_auto_defaults()

        class _ColorSwatchButton(QPushButton):
            def __init__(self, initial_rgb, label_text, parent=None):
                super().__init__(label_text, parent)
                self._rgb = tuple(int(max(0, min(255, value))) for value in initial_rgb)
                self._sync_style()
                self.clicked.connect(self._pick_color)

            def _sync_style(self):
                color = ShapeCatchAutoMixin._rgb_to_qcolor(self._rgb)
                text_color = "#000000" if color.lightness() > 140 else "#FFFFFF"
                self.setText(
                    f"{self.text().split(':')[0]}: {self._rgb[0]}, {self._rgb[1]}, {self._rgb[2]}"
                )
                self.setStyleSheet(
                    "QPushButton {"
                    f"background-color: {color.name()};"
                    f"color: {text_color};"
                    "padding: 6px 10px;"
                    "font-weight: 600;"
                    "text-align: left;"
                    "}"
                )

            def _pick_color(self):
                chosen = QColorDialog.getColor(
                    ShapeCatchAutoMixin._rgb_to_qcolor(self._rgb),
                    self.window(),
                    "Choose Catch Auto Color",
                )
                if chosen.isValid():
                    self._rgb = (chosen.red(), chosen.green(), chosen.blue())
                    self._sync_style()

            @property
            def rgb(self):
                return tuple(self._rgb)

        dialog = QDialog(self)
        dialog.setWindowTitle("Catch Auto Colors")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Choose the two reference colors for contrast detection.\n\n"
            "1. Holder color: the gold-like sample holder.\n"
            "2. Background color: the green surrounding area.\n\n"
            "Catch Auto will refine only the outer shape from this contrast."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        holder_button = _ColorSwatchButton(
            self.catch_auto_holder_rgb, "Holder Color", dialog
        )
        background_button = _ColorSwatchButton(
            self.catch_auto_background_rgb, "Background Color", dialog
        )
        grid.addWidget(QLabel("Holder (gold-like):"), 0, 0)
        grid.addWidget(holder_button, 0, 1)
        grid.addWidget(QLabel("Background (green):"), 1, 0)
        grid.addWidget(background_button, 1, 1)
        layout.addLayout(grid)

        defaults_row = QWidget(dialog)
        defaults_layout = QGridLayout(defaults_row)
        defaults_layout.setContentsMargins(0, 0, 0, 0)
        use_defaults = QPushButton("Use Defaults", defaults_row)
        use_defaults.clicked.connect(
            lambda: (
                setattr(
                    holder_button, "_rgb", tuple(self.DEFAULT_CATCH_AUTO_HOLDER_RGB)
                ),
                holder_button._sync_style(),
                setattr(
                    background_button,
                    "_rgb",
                    tuple(self.DEFAULT_CATCH_AUTO_BACKGROUND_RGB),
                ),
                background_button._sync_style(),
            )
        )
        defaults_layout.addWidget(use_defaults, 0, 0)
        layout.addWidget(defaults_row)

        buttons_row = QWidget(dialog)
        buttons_layout = QGridLayout(buttons_row)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        ok_button = QPushButton("OK", buttons_row)
        cancel_button = QPushButton("Cancel", buttons_row)
        ok_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        buttons_layout.addWidget(ok_button, 0, 0)
        buttons_layout.addWidget(cancel_button, 0, 1)
        layout.addWidget(buttons_row)

        if dialog.exec_() != QDialog.Accepted:
            return None

        self.catch_auto_holder_rgb = tuple(holder_button.rgb)
        self.catch_auto_background_rgb = tuple(background_button.rgb)
        return {
            "holder_rgb": tuple(holder_button.rgb),
            "background_rgb": tuple(background_button.rgb),
        }

    @staticmethod
    def _rgba_to_qpixmap(rgba_array):
        try:
            import numpy as np
        except Exception:
            return None
        arr = np.asarray(rgba_array, dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[2] != 4:
            return None
        image = QImage(
            arr.data,
            int(arr.shape[1]),
            int(arr.shape[0]),
            int(arr.strides[0]),
            QImage.Format_RGBA8888,
        ).copy()
        return QPixmap.fromImage(image)

    def _build_catch_auto_contrast_rgba(self, holder_rgb, background_rgb, rgba=None):
        source = rgba if rgba is not None else self._extract_workspace_rgba_array()
        if source is None:
            return None
        return build_catch_auto_contrast_rgba(source, holder_rgb, background_rgb)

    def _apply_catch_auto_preview_image(self, holder_rgb, background_rgb):
        image_view = getattr(self, "image_view", None)
        image_item = getattr(image_view, "image_item", None)
        if image_view is None or image_item is None:
            return False

        self._ensure_catch_auto_defaults()
        self._ensure_catch_auto_source_image()

        contrast_rgba = self._build_catch_auto_contrast_rgba(
            holder_rgb,
            background_rgb,
            rgba=getattr(self, "_catch_auto_source_rgba", None),
        )
        if contrast_rgba is None:
            return False
        contrast_pixmap = self._rgba_to_qpixmap(contrast_rgba)
        if contrast_pixmap is None:
            return False

        try:
            image_item.setPixmap(contrast_pixmap)
            image_view.current_pixmap = contrast_pixmap
            image_view.scene.update()
            self._catch_auto_preview_active = True
            return True
        except Exception:
            logger.debug("Failed to apply catch auto contrast preview", exc_info=True)
            return False

    def _ensure_catch_auto_source_image(self):
        image_view = getattr(self, "image_view", None)
        if image_view is None:
            return False
        if (
            getattr(self, "_catch_auto_source_pixmap", None) is not None
            and getattr(self, "_catch_auto_source_rgba", None) is not None
        ):
            return True
        self._catch_auto_source_pixmap = getattr(image_view, "current_pixmap", None)
        self._catch_auto_source_rgba = self._extract_workspace_rgba_array()
        return (
            getattr(self, "_catch_auto_source_pixmap", None) is not None
            and getattr(self, "_catch_auto_source_rgba", None) is not None
        )

    def _restore_catch_auto_preview_image(self):
        image_view = getattr(self, "image_view", None)
        image_item = getattr(image_view, "image_item", None)
        source_pixmap = getattr(self, "_catch_auto_source_pixmap", None)
        if image_view is None or image_item is None or source_pixmap is None:
            self._catch_auto_preview_active = False
            self._catch_auto_source_pixmap = None
            self._catch_auto_source_rgba = None
            return
        try:
            image_item.setPixmap(source_pixmap)
            image_view.current_pixmap = source_pixmap
            image_view.scene.update()
        except Exception:
            logger.debug("Failed to restore catch auto source image", exc_info=True)
        self._catch_auto_preview_active = False
        self._catch_auto_source_pixmap = None
        self._catch_auto_source_rgba = None
        if image_view is not None:
            try:
                image_view.image_click_sample_callback = None
            except Exception:
                logger.debug("Failed to clear catch auto image sampler", exc_info=True)
            try:
                image_view.viewport().unsetCursor()
            except Exception:
                logger.debug(
                    "Failed to restore viewport cursor after catch auto", exc_info=True
                )

    def _extract_workspace_rgba_array(self, *, prefer_catch_auto_source: bool = False):
        if (
            prefer_catch_auto_source
            and getattr(self, "_catch_auto_source_rgba", None) is not None
        ):
            try:
                return getattr(self, "_catch_auto_source_rgba").copy()
            except Exception:
                logger.debug("Failed to copy catch auto source image", exc_info=True)
        image_view = getattr(self, "image_view", None)
        current_pixmap = getattr(image_view, "current_pixmap", None)
        if current_pixmap is None:
            return None
        try:
            import numpy as np
            from difra.gui.qt_compat import QImage

            image = current_pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
            width = image.width()
            height = image.height()
            ptr = image.bits()
            ptr.setsize(image.byteCount())
            array = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4))
            return array.copy()
        except Exception:
            logger.debug(
                "Failed to extract workspace image for catch auto", exc_info=True
            )
            return None

    def _scene_rect_to_image_rect(self, scene_rect: QRectF):
        image_item = getattr(getattr(self, "image_view", None), "image_item", None)
        if image_item is None:
            return None
        try:
            return image_item.mapRectFromScene(scene_rect).normalized()
        except Exception:
            logger.debug("Failed to map scene rect to image rect", exc_info=True)
            return None

    def _scene_point_to_image_point(self, scene_x: float, scene_y: float):
        image_item = getattr(getattr(self, "image_view", None), "image_item", None)
        if image_item is None:
            return None
        try:
            point = image_item.mapFromScene(QPointF(float(scene_x), float(scene_y)))
            return (float(point.x()), float(point.y()))
        except Exception:
            logger.debug("Failed to map scene point to image point", exc_info=True)
            return None

    def _image_point_to_scene_point(self, image_x: float, image_y: float):
        image_item = getattr(getattr(self, "image_view", None), "image_item", None)
        if image_item is None:
            return None
        try:
            point = image_item.mapToScene(QPointF(float(image_x), float(image_y)))
            return (float(point.x()), float(point.y()))
        except Exception:
            logger.debug("Failed to map image point to scene point", exc_info=True)
            return None

    def _detect_outer_geometry_in_shape(self, shape_info, holder_rgb, background_rgb):
        payload = self._shape_center_and_extent(shape_info)
        if payload is None:
            return None
        rect, scene_cx, scene_cy = payload
        image_rect = self._scene_rect_to_image_rect(rect)
        center_image = self._scene_point_to_image_point(scene_cx, scene_cy)
        rgba = self._extract_workspace_rgba_array(prefer_catch_auto_source=True)
        if image_rect is None or center_image is None or rgba is None:
            return None

        role = str((shape_info or {}).get("role", "") or "").lower()
        geometry = detect_catch_auto_outer_geometry(
            shape_role=role,
            holder_role=self.ROLE_HOLDER_CIRCLE,
            image_rect_bounds=(
                float(image_rect.left()),
                float(image_rect.top()),
                float(image_rect.right()),
                float(image_rect.bottom()),
            ),
            center_image_xy=(float(center_image[0]), float(center_image[1])),
            rgba=rgba,
            holder_rgb=holder_rgb,
            background_rgb=background_rgb,
        )
        if geometry is None:
            return None

        center_x, center_y = geometry["center_image"]
        outer_left, outer_top, outer_right, outer_bottom = geometry["rect_image"]
        scene_center = self._image_point_to_scene_point(center_x, center_y)
        scene_top_left = self._image_point_to_scene_point(outer_left, outer_top)
        scene_bottom_right = self._image_point_to_scene_point(outer_right, outer_bottom)
        if scene_center is None or scene_top_left is None or scene_bottom_right is None:
            return None
        scene_rect = QRectF(
            float(scene_top_left[0]),
            float(scene_top_left[1]),
            float(scene_bottom_right[0]) - float(scene_top_left[0]),
            float(scene_bottom_right[1]) - float(scene_top_left[1]),
        ).normalized()
        return {
            "rect": scene_rect,
            "center": scene_center,
        }

    def _apply_scene_rect_to_shape(self, shape_info, scene_rect: QRectF):
        if not shape_info or scene_rect is None:
            return False
        item = shape_info.get("item")
        if item is None or not hasattr(item, "setRect") or not hasattr(item, "rect"):
            return False
        current_rect = (
            item.mapRectToScene(item.rect())
            if hasattr(item, "mapRectToScene")
            else item.sceneBoundingRect()
        )
        if (
            abs(current_rect.x() - scene_rect.x()) < 0.01
            and abs(current_rect.y() - scene_rect.y()) < 0.01
            and abs(current_rect.width() - scene_rect.width()) < 0.01
            and abs(current_rect.height() - scene_rect.height()) < 0.01
        ):
            return False
        callback = getattr(item, "geometry_changed_callback", None)
        if hasattr(item, "geometry_changed_callback"):
            item.geometry_changed_callback = None
        try:
            item.setRect(scene_rect)
            updater = getattr(item, "_update_handle_positions", None)
            if callable(updater):
                updater()
        finally:
            if hasattr(item, "geometry_changed_callback"):
                item.geometry_changed_callback = callback
        if callable(callback):
            callback()
        return True

    def _recenter_shape_to_scene_point(self, shape_info, new_scene_center):
        if not shape_info or not new_scene_center:
            return False
        payload = self._shape_center_and_extent(shape_info)
        if payload is None:
            return False
        rect, cx, cy = payload
        dx = float(new_scene_center[0]) - float(cx)
        dy = float(new_scene_center[1]) - float(cy)
        if abs(dx) < 0.01 and abs(dy) < 0.01:
            return False

        item = shape_info.get("item")
        callback = getattr(item, "geometry_changed_callback", None)
        if hasattr(item, "geometry_changed_callback"):
            item.geometry_changed_callback = None
        try:
            if hasattr(item, "setRect") and hasattr(item, "rect"):
                new_rect = QRectF(item.rect())
                new_rect.translate(dx, dy)
                item.setRect(new_rect)
                updater = getattr(item, "_update_handle_positions", None)
                if callable(updater):
                    updater()
            elif hasattr(item, "moveBy"):
                item.moveBy(dx, dy)
        finally:
            if hasattr(item, "geometry_changed_callback"):
                item.geometry_changed_callback = callback

        if callable(callback):
            callback()
        else:
            self._refresh_sample_photo_calibration()
            try:
                self.update_shape_table()
            except Exception:
                logger.debug(
                    "Failed to refresh shape table after catch auto", exc_info=True
                )
        return True

    def catch_auto_for_shape(
        self, shape_info, color_payload=None, prompt_for_colors: bool = True
    ):
        role = str((shape_info or {}).get("role", "") or "").lower()
        if role not in (self.ROLE_HOLDER_CIRCLE, self.ROLE_CALIBRATION_SQUARE):
            QMessageBox.information(
                self,
                "Catch Auto",
                "Catch Auto works only for holder circle or calibration square.",
            )
            return False

        if color_payload is None and prompt_for_colors:
            color_payload = self._prompt_catch_auto_colors()
            if color_payload is None:
                return False
        if color_payload is None:
            self._ensure_catch_auto_defaults()
            color_payload = {
                "holder_rgb": tuple(self.catch_auto_holder_rgb),
                "background_rgb": tuple(self.catch_auto_background_rgb),
            }

        outer_geometry = self._detect_outer_geometry_in_shape(
            shape_info,
            color_payload["holder_rgb"],
            color_payload["background_rgb"],
        )
        if outer_geometry is None:
            QMessageBox.warning(
                self,
                "Catch Auto Failed",
                "Could not detect the outer holder boundary from the selected holder/background colors.\n\n"
                "Adjust the manual shape or choose clearer gold-like and green colors, then try again.",
            )
            return False

        changed = self._apply_scene_rect_to_shape(shape_info, outer_geometry["rect"])
        current_center = outer_geometry["center"]
        changed = (
            self._recenter_shape_to_scene_point(shape_info, current_center) or changed
        )
        if not changed:
            self._select_calibration_shape_for_editing(shape_info)
            QMessageBox.information(
                self,
                "Catch Auto",
                "The detected center is already aligned closely enough.",
            )
            return False
        self._select_calibration_shape_for_editing(shape_info)
        return True
