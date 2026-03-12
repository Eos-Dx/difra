import logging
import uuid

from PyQt5.QtCore import Qt
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
)

logger = logging.getLogger(__name__)


class ShapeTableMixin:
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
            if shape_info.get("role") == "sample holder":
                self.apply_shape_role(shape_info)

    def create_shape_table(self):
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
        sampleHolderAct = menu.addAction("Sample Holder")
        menu.addSeparator()
        deleteAct = menu.addAction("Delete")
        action = menu.exec_(self.shapeTable.viewport().mapToGlobal(pos))
        if action == includeAct:
            self.update_shape_role(row, "include")
        elif action == excludeAct:
            self.update_shape_role(row, "exclude")
        elif action == sampleHolderAct:
            self.update_shape_role(row, "sample holder")
        elif action == deleteAct:
            self.delete_shapes_from_table()

    def apply_shape_role(self, shape_info):
        """Update the appearance of the shape based on its role."""
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
            # Convert the shape to a square and update its appearance.
            rect = item.rect() if hasattr(item, "rect") else item.boundingRect()
            cx = rect.x() + rect.width() / 2
            cy = rect.y() + rect.height() / 2
            side = min(rect.width(), rect.height())
            new_side = side
            new_x = cx - new_side / 2
            new_y = cy - new_side / 2

            # Update shape to square.
            item.setRect(new_x, new_y, new_side, new_side)
            pen = QPen(QColor("purple"), 2)
            item.setPen(pen)

            # Draw diagonal lines.

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

            # Store the extra graphics items so they can be removed later.
            shape_info["diagonals"] = [diag1, diag2]
            shape_info["center_marker"] = center_point

            # Conversion calculation.
            pixels_per_mm = new_side / 18.0  # Assuming 18mm per side for a real square.
            shape_info["pixels_per_mm"] = pixels_per_mm
            self.pixel_to_mm_ratio = pixels_per_mm
            self.include_center = (cx, cy)
            self.update_conversion_label()
            self._draw_stage_limit_outline(shape_info, cx, cy)
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
                    shape_info["role"] = role
                    # Remove "isNew" flag if present.
                    shape_info.pop("isNew", None)
                    # Update appearance using the new helper method.
                    self.apply_shape_role(shape_info)
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
                        if hasattr(item, "setRect"):
                            item.setRect(x, y, w, h)
                        if shape_info.get("role") == "sample holder":
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
        self.update_shape_table()
