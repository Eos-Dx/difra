# zone_measurements/logic/utils.py

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPen


class ZoneMeasurementsUtilsMixin:
    def _add_beam_line(self, x1, y1, x2, y2, pen=None):
        """
        Adds a beam cross line to the scene at (x1, y1)-(x2, y2) using the provided pen.
        Returns the QGraphicsLineItem instance.
        """
        from PyQt5.QtWidgets import QGraphicsLineItem

        if pen is None:
            pen = QPen(Qt.black, 5)

        line = QGraphicsLineItem(x1, y1, x2, y2)
        line.setPen(pen)
        self.image_view.scene.addItem(line)
        return line

    def mm_to_pixels(self, x_mm: float, y_mm: float):
        """
        Converts stage X/Y coordinates in mm to image pixel coordinates.
        Uses instance variables for real position, scaling, and center.
        """
        if not hasattr(self, "real_x_pos_mm") or not hasattr(self, "real_y_pos_mm"):
            return -1.0, -1.0
        if not hasattr(self, "pixel_to_mm_ratio") or not hasattr(self, "include_center"):
            return -1.0, -1.0

        use_rotated = False
        holder_center = None
        if hasattr(self, "_use_sample_photo_rotated_mapping"):
            try:
                use_rotated = bool(self._use_sample_photo_rotated_mapping())
            except Exception:
                use_rotated = False
        if use_rotated and hasattr(self, "_get_holder_center_px"):
            try:
                holder_center = self._get_holder_center_px()
            except Exception:
                holder_center = None
        if use_rotated and holder_center is not None and hasattr(self, "_get_sample_photo_beam_center_mm"):
            try:
                beam_x_mm, beam_y_mm = self._get_sample_photo_beam_center_mm()
            except Exception:
                beam_x_mm, beam_y_mm = (0.0, 0.0)
            x = (x_mm - beam_x_mm) * self.pixel_to_mm_ratio + holder_center[0]
            y = (y_mm - beam_y_mm) * self.pixel_to_mm_ratio + holder_center[1]
            return x, y

        x = (
            self.real_x_pos_mm.value() - x_mm
        ) * self.pixel_to_mm_ratio + self.include_center[0]
        y = (
            self.real_y_pos_mm.value() - y_mm
        ) * self.pixel_to_mm_ratio + self.include_center[1]
        return x, y

    # Add other reusable utilities as staticmethods or instance methods here if needed.
