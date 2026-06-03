"""Main zone points extension functionality."""

import logging
from typing import Optional

from difra.gui.qt_compat import sip
from difra.gui.qt_compat import QEvent, Qt
from difra.gui.qt_compat import QColor
from difra.gui.qt_compat import (
    QMessageBox,
    QTableWidgetItem,
)


from .points.zone_points_constants import ZonePointsConstants

logger = logging.getLogger(__name__)


class ZonePointsTableMixin:
    """Zone points behavior split from ZonePointsMixin."""

    def _set_table_item_editable(self, item: QTableWidgetItem, editable: bool) -> None:
        if item is None:
            return
        try:
            flags = item.flags()
            if editable:
                item.setFlags(flags | Qt.ItemIsEditable)
            else:
                item.setFlags(flags & ~Qt.ItemIsEditable)
        except Exception:
            pass

    def _parse_table_float(self, row: int, column: int) -> Optional[float]:
        if not hasattr(self, "pointsTable") or self.pointsTable is None:
            return None
        item = self.pointsTable.item(row, column)
        if item is None:
            return None
        text = str(item.text() or "").strip()
        if not text or text.upper() == "N/A":
            return None
        try:
            return float(text)
        except Exception:
            return None

    def _point_refs_for_row(self, row: int):
        gp = self.image_view.points_dict["generated"]["points"]
        gz = self.image_view.points_dict["generated"]["zones"]
        up = self.image_view.points_dict["user"]["points"]
        uz = self.image_view.points_dict["user"]["zones"]

        if row < len(gp):
            point_item = gp[row]
            zone_item = gz[row] if row < len(gz) else None
            return point_item, zone_item, "generated"

        user_row = row - len(gp)
        if 0 <= user_row < len(up):
            point_item = up[user_row]
            zone_item = uz[user_row] if user_row < len(uz) else None
            return point_item, zone_item, "user"

        return None, None, None

    def _point_within_allowed_region(self, x_px: float, y_px: float) -> bool:
        include_shape, exclude_shapes = self._get_inclusion_exclusion_shapes()
        if include_shape is None:
            return False

        from difra.gui.qt_compat import QPointF

        point = QPointF(float(x_px), float(y_px))
        try:
            if not include_shape.contains(include_shape.mapFromScene(point)):
                return False
        except Exception:
            return False
        for exclude_shape in exclude_shapes:
            try:
                if exclude_shape.contains(exclude_shape.mapFromScene(point)):
                    return False
            except Exception:
                continue
        return True

    def _move_point_and_zone(
        self, point_item, zone_item, x_px: float, y_px: float
    ) -> None:
        try:
            point_rect = point_item.rect()
            point_center = point_rect.center()
            point_item.setPos(
                float(x_px) - float(point_center.x()),
                float(y_px) - float(point_center.y()),
            )
        except Exception:
            radius = float(ZonePointsConstants.POINT_RADIUS)
            point_item.setRect(
                float(x_px) - radius,
                float(y_px) - radius,
                ZonePointsConstants.POINT_DIAMETER,
                ZonePointsConstants.POINT_DIAMETER,
            )

        if zone_item is None or sip.isdeleted(zone_item):
            return

        if hasattr(zone_item, "get_radius") and hasattr(zone_item, "_center_x"):
            try:
                zone_radius = float(zone_item.get_radius())
            except Exception:
                zone_radius = 0.0
            try:
                zone_item._center_x = float(x_px)
                zone_item._center_y = float(y_px)
                zone_item.setRect(
                    float(x_px) - zone_radius,
                    float(y_px) - zone_radius,
                    2 * zone_radius,
                    2 * zone_radius,
                )
                updater = getattr(zone_item, "_update_handle_positions", None)
                if callable(updater):
                    updater()
                return
            except Exception:
                pass

        try:
            rect = zone_item.rect()
            center = rect.center()
            zone_item.setPos(
                float(x_px) - float(center.x()),
                float(y_px) - float(center.y()),
            )
        except Exception:
            pass

    def _handle_points_table_item_changed(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        if getattr(self, "_updating_points_table", False):
            return
        row = int(item.row())
        column = int(item.column())
        if column not in (1, 2, 3, 4):
            return

        point_uid, _point_display_id = self._get_point_identity_from_row(row)
        if self._is_row_measured(row=row, point_uid=point_uid):
            QMessageBox.warning(
                self,
                "Measured Point",
                "Measured points cannot be moved from the table.",
            )
            self.update_points_table()
            return

        point_item, zone_item, _point_type = self._point_refs_for_row(row)
        if point_item is None or sip.isdeleted(point_item):
            self.update_points_table()
            return

        current_center = point_item.sceneBoundingRect().center()
        new_x_px = float(current_center.x())
        new_y_px = float(current_center.y())

        if column in (1, 2):
            parsed_x = self._parse_table_float(row, 1)
            parsed_y = self._parse_table_float(row, 2)
            if parsed_x is None or parsed_y is None:
                self.update_points_table()
                return
            new_x_px = float(parsed_x)
            new_y_px = float(parsed_y)
        else:
            if not float(getattr(self, "pixel_to_mm_ratio", 0.0)):
                QMessageBox.warning(
                    self,
                    "Missing Conversion",
                    "Cannot move points by mm coordinates until the px/mm conversion is set.",
                )
                self.update_points_table()
                return
            parsed_x_mm = self._parse_table_float(row, 3)
            parsed_y_mm = self._parse_table_float(row, 4)
            if parsed_x_mm is None or parsed_y_mm is None:
                self.update_points_table()
                return
            if hasattr(self, "mm_to_pixels"):
                new_x_px, new_y_px = self.mm_to_pixels(
                    float(parsed_x_mm),
                    float(parsed_y_mm),
                )
            else:
                new_x_px = (
                    self.include_center[0]
                    + (self.real_x_pos_mm.value() - float(parsed_x_mm))
                    * self.pixel_to_mm_ratio
                )
                new_y_px = (
                    self.include_center[1]
                    + (self.real_y_pos_mm.value() - float(parsed_y_mm))
                    * self.pixel_to_mm_ratio
                )

        if not self._point_within_allowed_region(new_x_px, new_y_px):
            QMessageBox.warning(
                self,
                "Invalid Point Position",
                "The updated coordinates place the point outside the allowed include zone.",
            )
            self.update_points_table()
            return

        self._move_point_and_zone(point_item, zone_item, new_x_px, new_y_px)
        self.update_points_table()

    def on_points_table_selection(self, selected, deselected):
        """Handle table row selection by highlighting corresponding points in the scene."""
        # Skip if we're in the middle of updating the table to avoid re-entrancy issues
        if getattr(self, "_updating_points_table", False):
            return
        # Reset all points to their default colors
        self._reset_all_point_styles()

        # Highlight selected points
        self._highlight_selected_points()

    def _reset_all_point_styles(self):
        """Reset all points to their default colors."""

        def reset_point_style(item, point_type: str):
            if sip.isdeleted(item):
                return
            color = self._default_point_color(
                point_type=point_type,
                point_uid=str(item.data(2) or "").strip(),
            )
            item.setBrush(QColor(color))

        # Reset generated points
        for item in self.image_view.points_dict["generated"]["points"]:
            reset_point_style(item, "generated")

        # Reset user points
        for item in self.image_view.points_dict["user"]["points"]:
            reset_point_style(item, "user")

    def _default_point_color(
        self, point_type: str, point_uid: Optional[str] = None
    ) -> str:
        point_uid = str(point_uid or "").strip()
        if point_uid and self._point_has_measurements(point_uid):
            return ZonePointsConstants.POINT_COLOR_MEASURED
        if point_type == "generated":
            return ZonePointsConstants.POINT_COLOR_GENERATED
        return ZonePointsConstants.POINT_COLOR_USER

    def refresh_point_visual_states(self):
        """Reapply default point colors, preserving measured-point state."""
        if not hasattr(self, "image_view") or not hasattr(
            self.image_view, "points_dict"
        ):
            return
        self._reset_all_point_styles()

    def _highlight_selected_points(self):
        """Highlight points corresponding to selected table rows."""
        for index in self.pointsTable.selectionModel().selectedRows():
            row = index.row()
            n_generated = len(self.image_view.points_dict["generated"]["points"])

            if row < n_generated:
                # Selected row corresponds to a generated point
                item = self.image_view.points_dict["generated"]["points"][row]
                item.setBrush(ZonePointsConstants.POINT_COLOR_SELECTED)
            else:
                # Selected row corresponds to a user point
                user_row = row - n_generated
                if user_row < len(self.image_view.points_dict["user"]["points"]):
                    item = self.image_view.points_dict["user"]["points"][user_row]
                    item.setBrush(ZonePointsConstants.POINT_COLOR_SELECTED)

    def eventFilter(self, source, event):
        # Safety check: ensure pointsTable exists before comparing
        if (
            hasattr(self, "pointsTable")
            and source == self.pointsTable
            and event.type() == QEvent.KeyPress
        ):
            if event.key() == Qt.Key_Delete:
                self.delete_selected_points()
                return True
        return super().eventFilter(source, event)
