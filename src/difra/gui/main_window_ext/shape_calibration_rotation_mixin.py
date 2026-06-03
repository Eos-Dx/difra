import logging

from difra.gui.qt_compat import (
    QMessageBox,
)

from difra.gui.extra.resizable_zone import (
    ResizableSquareItem,
    ResizableZoneItem,
)

logger = logging.getLogger(__name__)


class ShapeCalibrationRotationMixin:
    """Shape calibration behavior split from ShapeCalibrationMixin."""

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
                logger.debug(
                    "Failed to reset sample photo image rotation", exc_info=True
                )
        if image_view is not None:
            try:
                image_view.rotation_angle = 0
            except Exception:
                logger.debug("Failed to reset image_view rotation angle", exc_info=True)
        self._update_sample_photo_rotation_ui()

    def _has_sample_photo_image_loaded(self) -> bool:
        image_view = getattr(self, "image_view", None)
        if image_view is None:
            return False
        if getattr(image_view, "image_item", None) is not None:
            return True
        return getattr(image_view, "current_pixmap", None) is not None

    def _sample_photo_rotation_required_for_workspace_editing(self) -> bool:
        self._ensure_shape_calibration_defaults()
        return self._has_sample_photo_image_loaded() and not bool(
            getattr(self, "sample_photo_rotation_confirmed", False)
        )

    def _ensure_sample_photo_ready_for_workspace_editing(
        self,
        *,
        show_message: bool = True,
        action_label: str = "draw on the image",
    ) -> bool:
        blocked = self._sample_photo_rotation_required_for_workspace_editing()
        if not blocked:
            return True

        if show_message:
            QMessageBox.information(
                self,
                "Rotate Image First",
                "Rotate the sample image by 180° before you can "
                f"{action_label}.\n\n"
                "Use the 'Rotate 180°' button in the top toolbar. "
                "After rotation is confirmed once, DIFRA will remember that state.",
            )
        self._update_sample_photo_rotation_ui()
        return False

    def _update_sample_photo_rotation_ui(self):
        self._ensure_shape_calibration_defaults()
        status_label = getattr(self, "sample_photo_rotation_status", None) or getattr(
            self, "rotationStatusLabel", None
        )
        rotate_button = getattr(self, "rotate_sample_photo_btn", None) or getattr(
            self, "rotateSamplePhotoBtn", None
        )
        is_rotated = bool(getattr(self, "sample_photo_rotation_confirmed", False))
        has_image = self._has_sample_photo_image_loaded()
        can_rotate = bool(has_image and not is_rotated)

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
            elif has_image:
                status_label.setText(
                    "Image not rotated yet. Rotate 180° before drawing zones or points."
                )
                try:
                    status_label.setStyleSheet(
                        "color: #8D6E63; font-size: 9px; margin: 1px;"
                    )
                except Exception:
                    pass
            else:
                status_label.setText("Load sample image first.")
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

        if not self._has_sample_photo_image_loaded():
            QMessageBox.information(
                self,
                "Load Image First",
                "Load or capture a sample image first, then rotate it by 180°.",
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
    def _rotate_xy_180(
        x_value: float, y_value: float, center_x: float, center_y: float
    ):
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
            point_items = list(
                (points_dict.get(point_type) or {}).get("points", []) or []
            )
            zone_items = list(
                (points_dict.get(point_type) or {}).get("zones", []) or []
            )
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
            logger.debug(
                "Failed to refresh shape table after workspace rotation", exc_info=True
            )
        try:
            self.update_points_table()
        except Exception:
            logger.debug(
                "Failed to refresh points table after workspace rotation", exc_info=True
            )
        refresh_points = getattr(self, "refresh_point_visual_states", None)
        if callable(refresh_points):
            try:
                refresh_points()
            except Exception:
                logger.debug(
                    "Failed to refresh point visuals after workspace rotation",
                    exc_info=True,
                )
        try:
            image_view.scene.update()
        except Exception:
            pass

    def _maybe_prompt_sample_photo_rotation(self, *, force_prompt: bool = False):
        self._ensure_shape_calibration_defaults()
        if (
            bool(getattr(self, "_suppress_sample_photo_rotation_prompt", False))
            and not force_prompt
        ):
            return
        if self._sample_photo_rotation_prompted and not force_prompt:
            return
        if not self._has_sample_photo_image_loaded():
            return
        if bool(getattr(self, "sample_photo_rotation_confirmed", False)):
            self.sample_photo_workspace_image_type = "sample_rotated"
            self._update_sample_photo_rotation_ui()
            return
        reply = QMessageBox.question(
            self,
            "Rotate Sample Holder",
            "The sample photo has been loaded.\n\n"
            "Rotate the physical sample holder by 180° and use the same 180° working image in DIFRA now?\n\n"
            "You can answer 'No' and do it later with the 'Rotate 180°' toolbar button, "
            "but drawing zones and points will stay blocked until the image is rotated.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        self.sample_photo_rotation_confirmed = reply == QMessageBox.Yes
        self.sample_photo_rotation_deg = (
            180 if self.sample_photo_rotation_confirmed else 0
        )
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
                logger.debug(
                    "Failed to refresh points table after rotation prompt",
                    exc_info=True,
                )
