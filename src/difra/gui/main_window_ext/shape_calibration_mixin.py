import logging
from math import ceil, floor, sqrt

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QPen
from PyQt5.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QInputDialog,
    QMessageBox,
)

from difra.gui.extra.resizable_zone import (
    ResizableEllipseItem,
    ResizableSquareItem,
    ResizableZoneItem,
)

logger = logging.getLogger(__name__)


class ShapeCalibrationMixin:
    DEFAULT_CALIBRATION_SQUARE_SIDE_MM = 18.35
    DEFAULT_HOLDER_CIRCLE_DIAMETER_MM = 15.18
    DEFAULT_SAMPLE_HOLDER_LENGTH_MM = 65.45
    DEFAULT_LOAD_POSITION_MM = (-13.9, -6.0)
    DEFAULT_BEAM_CENTER_MM = (6.15, -9.15)
    AUTO_CENTER_OUTER_WEIGHT = 0.8
    AUTO_CENTER_INNER_WEIGHT = 0.2
    ROLE_CALIBRATION_SQUARE = "calibration square"
    ROLE_HOLDER_CIRCLE = "holder circle"

    def _get_selected_calibration_shape_info(self):
        image_view = getattr(self, "image_view", None)
        scene = getattr(image_view, "scene", None)
        if scene is None:
            return None
        selected_items = [item for item in scene.selectedItems() if item is not getattr(image_view, "image_item", None)]
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
            logger.debug("Failed to clear scene selection before selecting shape", exc_info=True)
        try:
            item.setSelected(True)
        except Exception:
            logger.debug("Failed to select calibration shape for editing", exc_info=True)
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
                logger.debug("Failed to switch image view back to select mode", exc_info=True)

    def catch_auto_selected_calibration_shape(self):
        shape_info = self._get_selected_calibration_shape_info()
        if not shape_info:
            QMessageBox.information(
                self,
                "Catch Auto",
                "Select a holder circle or calibration square first.",
            )
            return False
        return self.catch_auto_for_shape(shape_info)

    def _extract_workspace_rgba_array(self):
        image_view = getattr(self, "image_view", None)
        current_pixmap = getattr(image_view, "current_pixmap", None)
        if current_pixmap is None:
            return None
        try:
            import numpy as np
            from PyQt5.QtGui import QImage

            image = current_pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
            width = image.width()
            height = image.height()
            ptr = image.bits()
            ptr.setsize(image.byteCount())
            array = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4))
            return array.copy()
        except Exception:
            logger.debug("Failed to extract workspace image for catch auto", exc_info=True)
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

    def _detect_inner_hole_center_in_shape(self, shape_info):
        payload = self._shape_center_and_extent(shape_info)
        if payload is None:
            return None
        rect, scene_cx, scene_cy = payload
        image_rect = self._scene_rect_to_image_rect(rect)
        center_image = self._scene_point_to_image_point(scene_cx, scene_cy)
        rgba = self._extract_workspace_rgba_array()
        if image_rect is None or center_image is None or rgba is None:
            return None

        try:
            import numpy as np
        except Exception:
            logger.debug("Catch auto requires numpy", exc_info=True)
            return None
        try:
            import cv2  # type: ignore
        except Exception:
            cv2 = None

        image_h, image_w = rgba.shape[:2]
        left = max(0, int(floor(image_rect.left())))
        top = max(0, int(floor(image_rect.top())))
        right = min(image_w, int(ceil(image_rect.right())))
        bottom = min(image_h, int(ceil(image_rect.bottom())))
        if right - left < 8 or bottom - top < 8:
            return None

        rgb = rgba[:, :, :3].astype(np.float32)
        if cv2 is not None and hasattr(cv2, "cvtColor") and hasattr(cv2, "COLOR_RGB2GRAY"):
            gray = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)
        else:
            gray = np.clip(
                0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2],
                0,
                255,
            ).astype(np.uint8)
        roi = gray[top:bottom, left:right]
        if roi.size == 0:
            return None

        approx_x = int(round(center_image[0])) - left
        approx_y = int(round(center_image[1])) - top
        approx_x = int(max(0, min(roi.shape[1] - 1, approx_x)))
        approx_y = int(max(0, min(roi.shape[0] - 1, approx_y)))

        search_half = max(8, int(min(roi.shape[0], roi.shape[1]) * 0.22))
        sx0 = max(0, approx_x - search_half)
        sx1 = min(roi.shape[1], approx_x + search_half + 1)
        sy0 = max(0, approx_y - search_half)
        sy1 = min(roi.shape[0], approx_y + search_half + 1)
        search = roi[sy0:sy1, sx0:sx1]
        if search.size == 0:
            return None

        if cv2 is not None and hasattr(cv2, "GaussianBlur"):
            blurred = cv2.GaussianBlur(search, (7, 7), 0)
        else:
            kernel = np.ones((5, 5), dtype=np.float32) / 25.0
            padded = np.pad(search.astype(np.float32), 2, mode="edge")
            blurred = np.empty_like(search, dtype=np.float32)
            for row in range(search.shape[0]):
                for col in range(search.shape[1]):
                    window = padded[row:row + 5, col:col + 5]
                    blurred[row, col] = float((window * kernel).sum())
        threshold = float(np.percentile(blurred, 18))
        dark_mask = blurred <= threshold
        if int(dark_mask.sum()) < 12:
            return None

        ys, xs = np.nonzero(dark_mask)
        weights = (threshold - blurred[dark_mask].astype(np.float32)) + 1.0
        if float(weights.sum()) <= 0.0:
            return None
        center_x = float((xs * weights).sum() / weights.sum()) + sx0 + left
        center_y = float((ys * weights).sum() / weights.sum()) + sy0 + top
        return self._image_point_to_scene_point(center_x, center_y)

    def _detect_outer_geometry_in_shape(self, shape_info):
        payload = self._shape_center_and_extent(shape_info)
        if payload is None:
            return None
        rect, scene_cx, scene_cy = payload
        image_rect = self._scene_rect_to_image_rect(rect)
        center_image = self._scene_point_to_image_point(scene_cx, scene_cy)
        rgba = self._extract_workspace_rgba_array()
        if image_rect is None or center_image is None or rgba is None:
            return None

        try:
            import numpy as np
        except Exception:
            logger.debug("Catch auto requires numpy", exc_info=True)
            return None
        try:
            import cv2  # type: ignore
        except Exception:
            cv2 = None

        image_h, image_w = rgba.shape[:2]
        left = max(0, int(floor(image_rect.left())))
        top = max(0, int(floor(image_rect.top())))
        right = min(image_w, int(ceil(image_rect.right())))
        bottom = min(image_h, int(ceil(image_rect.bottom())))
        if right - left < 12 or bottom - top < 12:
            return None

        rgb = rgba[top:bottom, left:right, :3].astype(np.float32)
        roi_h, roi_w = rgb.shape[:2]
        border = max(2, int(min(roi_h, roi_w) * 0.08))
        bg_samples = np.concatenate(
            [
                rgb[:border, :, :].reshape(-1, 3),
                rgb[-border:, :, :].reshape(-1, 3),
                rgb[:, :border, :].reshape(-1, 3),
                rgb[:, -border:, :].reshape(-1, 3),
            ],
            axis=0,
        )
        bg_color = np.median(bg_samples, axis=0)
        dist = np.linalg.norm(rgb - bg_color.reshape(1, 1, 3), axis=2).astype(np.float32)

        if cv2 is not None and hasattr(cv2, "GaussianBlur"):
            dist_blurred = cv2.GaussianBlur(dist, (7, 7), 0)
        else:
            kernel = np.ones((5, 5), dtype=np.float32) / 25.0
            padded = np.pad(dist, 2, mode="edge")
            dist_blurred = np.empty_like(dist, dtype=np.float32)
            for row in range(dist.shape[0]):
                for col in range(dist.shape[1]):
                    window = padded[row:row + 5, col:col + 5]
                    dist_blurred[row, col] = float((window * kernel).sum())

        threshold = max(12.0, float(np.percentile(dist_blurred, 60)))
        foreground = dist_blurred >= threshold
        if int(foreground.sum()) < 20:
            return None

        if cv2 is not None and hasattr(cv2, "morphologyEx") and hasattr(cv2, "MORPH_CLOSE"):
            mask_u8 = (foreground.astype(np.uint8) * 255)
            kernel = np.ones((5, 5), dtype=np.uint8)
            mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel)
            mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_OPEN, kernel)
            foreground = mask_u8 > 0

        if cv2 is not None and hasattr(cv2, "connectedComponentsWithStats"):
            mask_u8 = foreground.astype(np.uint8)
            count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask_u8, 8)
            best_idx = None
            best_score = None
            approx_x = float(center_image[0] - left)
            approx_y = float(center_image[1] - top)
            for idx in range(1, int(count)):
                area = float(stats[idx, cv2.CC_STAT_AREA])
                if area < 20:
                    continue
                cx = float(centroids[idx][0])
                cy = float(centroids[idx][1])
                distance = ((cx - approx_x) ** 2 + (cy - approx_y) ** 2) ** 0.5
                score = area - 4.0 * distance
                if best_score is None or score > best_score:
                    best_score = score
                    best_idx = idx
            if best_idx is not None:
                foreground = labels == best_idx

        ys, xs = np.nonzero(foreground)
        if xs.size < 20 or ys.size < 20:
            return None

        weights = dist_blurred[foreground] + 1.0
        role = str((shape_info or {}).get("role", "") or "").lower()

        bbox_left = float(xs.min())
        bbox_right = float(xs.max())
        bbox_top = float(ys.min())
        bbox_bottom = float(ys.max())
        bbox_width = max(10.0, bbox_right - bbox_left + 1.0)
        bbox_height = max(10.0, bbox_bottom - bbox_top + 1.0)

        if role == self.ROLE_HOLDER_CIRCLE:
            center_x_local = float((xs * weights).sum() / weights.sum())
            center_y_local = float((ys * weights).sum() / weights.sum())
            var_x = float((((xs - center_x_local) ** 2) * weights).sum() / weights.sum())
            var_y = float((((ys - center_y_local) ** 2) * weights).sum() / weights.sum())
            fitted_width = max(10.0, min(float(roi_w), 4.0 * sqrt(max(var_x, 1.0))))
            fitted_height = max(10.0, min(float(roi_h), 4.0 * sqrt(max(var_y, 1.0))))
            outer_width = 0.7 * fitted_width + 0.3 * bbox_width
            outer_height = 0.7 * fitted_height + 0.3 * bbox_height
            outer_left = center_x_local - outer_width / 2.0
            outer_right = center_x_local + outer_width / 2.0
            outer_top = center_y_local - outer_height / 2.0
            outer_bottom = center_y_local + outer_height / 2.0
        else:
            profile_x = foreground.astype(np.float32).mean(axis=0)
            profile_y = foreground.astype(np.float32).mean(axis=1)
            kernel = np.array([1.0, 2.0, 3.0, 2.0, 1.0], dtype=np.float32)
            kernel /= float(kernel.sum())
            profile_x = np.convolve(profile_x, kernel, mode="same")
            profile_y = np.convolve(profile_y, kernel, mode="same")
            mask_x = profile_x >= max(0.08, float(profile_x.max()) * 0.35)
            mask_y = profile_y >= max(0.08, float(profile_y.max()) * 0.35)
            if mask_x.any():
                x_idx = np.nonzero(mask_x)[0]
                outer_left = float(x_idx[0])
                outer_right = float(x_idx[-1])
            else:
                outer_left = bbox_left
                outer_right = bbox_right
            if mask_y.any():
                y_idx = np.nonzero(mask_y)[0]
                outer_top = float(y_idx[0])
                outer_bottom = float(y_idx[-1])
            else:
                outer_top = bbox_top
                outer_bottom = bbox_bottom
            center_x_local = (outer_left + outer_right) / 2.0
            center_y_local = (outer_top + outer_bottom) / 2.0

        center_x = center_x_local + left
        center_y = center_y_local + top
        outer_left += left
        outer_right += left
        outer_top += top
        outer_bottom += top
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
        current_rect = item.mapRectToScene(item.rect()) if hasattr(item, "mapRectToScene") else item.sceneBoundingRect()
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
                logger.debug("Failed to refresh shape table after catch auto", exc_info=True)
        return True

    def catch_auto_for_shape(self, shape_info):
        role = str((shape_info or {}).get("role", "") or "").lower()
        if role not in (self.ROLE_HOLDER_CIRCLE, self.ROLE_CALIBRATION_SQUARE):
            QMessageBox.information(
                self,
                "Catch Auto",
                "Catch Auto works only for holder circle or calibration square.",
            )
            return False

        outer_geometry = self._detect_outer_geometry_in_shape(shape_info)
        if outer_geometry is None:
            QMessageBox.warning(
                self,
                "Catch Auto Failed",
                "Could not detect the outer shape boundary by contrast.\n\n"
                "Keep the manual shape or adjust it slightly and try again.",
            )
            return False

        changed = self._apply_scene_rect_to_shape(shape_info, outer_geometry["rect"])
        current_center = outer_geometry["center"]

        detected_center = self._detect_inner_hole_center_in_shape(shape_info)
        if detected_center is not None:
            outer_weight = float(self.AUTO_CENTER_OUTER_WEIGHT)
            inner_weight = float(self.AUTO_CENTER_INNER_WEIGHT)
            target_center = (
                outer_weight * float(current_center[0]) + inner_weight * float(detected_center[0]),
                outer_weight * float(current_center[1]) + inner_weight * float(detected_center[1]),
            )
        else:
            target_center = current_center

        changed = self._recenter_shape_to_scene_point(shape_info, target_center) or changed
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
        status_label = (
            getattr(self, "sample_photo_rotation_status", None)
            or getattr(self, "rotationStatusLabel", None)
        )
        rotate_button = (
            getattr(self, "rotate_sample_photo_btn", None)
            or getattr(self, "rotateSamplePhotoBtn", None)
        )
        is_rotated = bool(getattr(self, "sample_photo_rotation_confirmed", False))
        can_rotate = bool(getattr(self, "_sample_photo_has_explicit_holder_circle", False)) and float(
            getattr(self, "pixel_to_mm_ratio", 0.0) or 0.0
        ) > 0.0

        if status_label is not None:
            if is_rotated:
                status_label.setText(
                    "Image Rotated 180°. Ensure the sample is physically rotated."
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
                    getattr(self, "sample_photo_beam_center_mm", self.DEFAULT_BEAM_CENTER_MM)
                )
                beam_x_mm = float(beam_center[0])
                beam_y_mm = float(beam_center[1])
                return (
                    (float(x_mm) - beam_x_mm) * px_per_mm + holder_x,
                    (float(y_mm) - beam_y_mm) * px_per_mm + holder_y,
                )
            except Exception:
                logger.debug("Failed to convert stage mm to holder-centered pixels", exc_info=True)

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

        a = self._stage_mm_to_overlay_pixels(float(x_min), float(y_min), (float(cx), float(cy)))
        b = self._stage_mm_to_overlay_pixels(float(x_max), float(y_max), (float(cx), float(cy)))
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
        role = str((shape_info or {}).get("role", "") or "").lower()
        if role in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
            self._clear_sample_photo_dependents(keep_shape_info=shape_info)
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
                logger.debug("Failed to clear profile paths after calibration change", exc_info=True)

        delete_points = getattr(self, "delete_all_points", None)
        if callable(delete_points):
            try:
                delete_points()
            except Exception:
                logger.debug("Failed to clear points after calibration change", exc_info=True)

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
                logger.debug("Failed to refresh points table after rotation prompt", exc_info=True)
