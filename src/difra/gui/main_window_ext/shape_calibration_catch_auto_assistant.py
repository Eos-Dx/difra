import logging

from difra.gui.qt_compat import Qt
from difra.gui.qt_compat import QColor, QColorDialog, QDialog, QLabel, QPushButton, QVBoxLayout


logger = logging.getLogger(__name__)


def _rgb_to_qcolor(rgb_value):
    try:
        red, green, blue = [int(max(0, min(255, channel))) for channel in rgb_value]
    except Exception:
        red, green, blue = 0, 0, 0
    return QColor(red, green, blue)


class ShapeCatchAutoAssistantMixin:
    def open_catch_auto_assistant_for_shape(self, shape_info):
        if not shape_info:
            return False
        self._ensure_catch_auto_defaults()

        existing = getattr(self, "_catch_auto_assistant_dialog", None)
        if existing is not None:
            try:
                existing.raise_()
                existing.activateWindow()
            except Exception:
                logger.debug(
                    "Failed to focus existing catch auto assistant", exc_info=True
                )
            self._select_calibration_shape_for_editing(shape_info)
            return True

        class _ColorSwatchButton(QPushButton):
            def __init__(self, title_text, initial_rgb, parent=None):
                super().__init__(parent)
                self._title_text = str(title_text)
                self._rgb = tuple(int(max(0, min(255, value))) for value in initial_rgb)
                self._sync_style()

            def _sync_style(self):
                color = _rgb_to_qcolor(self._rgb)
                text_color = "#000000" if color.lightness() > 140 else "#FFFFFF"
                self.setText(
                    f"{self._title_text}: {self._rgb[0]}, {self._rgb[1]}, {self._rgb[2]}"
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

            @property
            def rgb(self):
                return tuple(self._rgb)

            def set_rgb(self, rgb_value):
                self._rgb = tuple(int(max(0, min(255, value))) for value in rgb_value)
                self._sync_style()

        dialog = QDialog(self, Qt.Window)
        dialog.setModal(False)
        dialog.setWindowTitle("Catch Auto Assistant")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Catch Auto is now in contrast mode.\n\n"
            "Pick holder and background colors, then edit the outer ellipse/rectangle "
            "directly on the contrast image if needed. When the boundary looks right, "
            "click Apply Auto."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        holder_button = _ColorSwatchButton(
            "Holder (gold-like)", self.catch_auto_holder_rgb, dialog
        )
        background_button = _ColorSwatchButton(
            "Background (green)", self.catch_auto_background_rgb, dialog
        )
        layout.addWidget(holder_button)
        layout.addWidget(background_button)

        status_label = QLabel(
            "1. Click Pick Holder and choose a gold-like area on the image.\n"
            "2. Click Pick Background and choose a green background area.\n"
            "3. Adjust the shape directly on the contrast image.\n"
            "4. Click Apply Auto to refine the outer boundary."
        )
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        pick_holder_button = QPushButton("Pick Holder From Image", dialog)
        pick_background_button = QPushButton("Pick Background From Image", dialog)
        refresh_button = QPushButton("Refresh Contrast", dialog)
        apply_button = QPushButton("Apply Auto", dialog)
        close_button = QPushButton("Close Contrast Mode", dialog)
        manual_holder_button = QPushButton("Manual Holder Color...", dialog)
        manual_background_button = QPushButton("Manual Background Color...", dialog)
        layout.addWidget(pick_holder_button)
        layout.addWidget(pick_background_button)
        layout.addWidget(manual_holder_button)
        layout.addWidget(manual_background_button)
        layout.addWidget(refresh_button)
        layout.addWidget(apply_button)
        layout.addWidget(close_button)

        def _current_colors():
            return {
                "holder_rgb": tuple(holder_button.rgb),
                "background_rgb": tuple(background_button.rgb),
            }

        def _set_status_text(text):
            try:
                status_label.setText(str(text))
            except Exception:
                logger.debug("Failed to update catch auto status label", exc_info=True)

        def _apply_preview():
            colors = _current_colors()
            self.catch_auto_holder_rgb = colors["holder_rgb"]
            self.catch_auto_background_rgb = colors["background_rgb"]
            ok = self._apply_catch_auto_preview_image(
                colors["holder_rgb"],
                colors["background_rgb"],
            )
            if ok:
                _set_status_text(
                    "Contrast mode active. Drag the white handles to align the outer shape, "
                    "or click Apply Auto to refine it from the highlighted boundary."
                )
                self._select_calibration_shape_for_editing(
                    self._get_selected_calibration_shape_info() or shape_info
                )
            return ok

        def _sample_average_rgb_at_scene_point(scene_point):
            self._ensure_catch_auto_source_image()
            rgba = getattr(self, "_catch_auto_source_rgba", None)
            image_point = self._scene_point_to_image_point(
                scene_point.x(), scene_point.y()
            )
            if rgba is None or image_point is None:
                return None
            try:
                import numpy as np
            except Exception:
                return None
            px = int(round(float(image_point[0])))
            py = int(round(float(image_point[1])))
            if px < 0 or py < 0 or py >= rgba.shape[0] or px >= rgba.shape[1]:
                return None
            radius = 3
            left = max(0, px - radius)
            right = min(int(rgba.shape[1]), px + radius + 1)
            top = max(0, py - radius)
            bottom = min(int(rgba.shape[0]), py + radius + 1)
            window = np.asarray(rgba[top:bottom, left:right, :3], dtype=np.float32)
            if window.size == 0:
                return None
            mean_rgb = window.mean(axis=(0, 1))
            return tuple(int(round(float(channel))) for channel in mean_rgb)

        def _start_pick_mode(target: str):
            image_view = getattr(self, "image_view", None)
            if image_view is None:
                return

            def _consume_pick(scene_point):
                rgb_value = _sample_average_rgb_at_scene_point(scene_point)
                try:
                    image_view.image_click_sample_callback = None
                    image_view.viewport().unsetCursor()
                except Exception:
                    logger.debug("Failed to exit catch auto pick mode", exc_info=True)
                if rgb_value is None:
                    _set_status_text("Could not sample that image position. Try again.")
                    return
                if target == "holder":
                    holder_button.set_rgb(rgb_value)
                    _set_status_text(
                        f"Holder sampled from image: {rgb_value[0]}, {rgb_value[1]}, {rgb_value[2]}. "
                        "Now pick background or refresh/apply."
                    )
                else:
                    background_button.set_rgb(rgb_value)
                    _set_status_text(
                        f"Background sampled from image: {rgb_value[0]}, {rgb_value[1]}, {rgb_value[2]}. "
                        "Now adjust the shape and apply auto."
                    )
                _apply_preview()

            try:
                image_view.image_click_sample_callback = _consume_pick
                image_view.viewport().setCursor(Qt.CrossCursor)
            except Exception:
                logger.debug("Failed to enter catch auto pick mode", exc_info=True)
                return
            _set_status_text(
                "Click on the image to sample "
                + ("holder gold-like" if target == "holder" else "green background")
                + " color."
            )

        def _choose_manual(target: str):
            button = holder_button if target == "holder" else background_button
            chosen = QColorDialog.getColor(
                self._rgb_to_qcolor(button.rgb),
                dialog,
                "Choose Catch Auto Color",
            )
            if not chosen.isValid():
                return
            button.set_rgb((chosen.red(), chosen.green(), chosen.blue()))
            _apply_preview()

        def _apply_auto():
            active_shape = self._get_selected_calibration_shape_info() or shape_info
            self.catch_auto_for_shape(
                active_shape,
                color_payload=_current_colors(),
                prompt_for_colors=False,
            )

        def _close_assistant():
            if getattr(dialog, "_closing_catch_auto_assistant", False):
                return
            dialog._closing_catch_auto_assistant = True
            image_view = getattr(self, "image_view", None)
            if image_view is not None:
                try:
                    image_view.image_click_sample_callback = None
                    image_view.viewport().unsetCursor()
                except Exception:
                    logger.debug(
                        "Failed to clear catch auto pick mode on close", exc_info=True
                    )
            self._restore_catch_auto_preview_image()
            self._catch_auto_assistant_dialog = None
            try:
                dialog.hide()
            except Exception:
                logger.debug("Failed to hide catch auto assistant", exc_info=True)

        pick_holder_button.clicked.connect(lambda: _start_pick_mode("holder"))
        pick_background_button.clicked.connect(lambda: _start_pick_mode("background"))
        manual_holder_button.clicked.connect(lambda: _choose_manual("holder"))
        manual_background_button.clicked.connect(lambda: _choose_manual("background"))
        refresh_button.clicked.connect(_apply_preview)
        apply_button.clicked.connect(_apply_auto)
        close_button.clicked.connect(_close_assistant)
        dialog.finished.connect(lambda *_args: _close_assistant())

        self._catch_auto_assistant_dialog = dialog
        _apply_preview()
        dialog.show()
        try:
            dialog.raise_()
            dialog.activateWindow()
        except Exception:
            logger.debug("Failed to raise catch auto assistant", exc_info=True)
        return True

