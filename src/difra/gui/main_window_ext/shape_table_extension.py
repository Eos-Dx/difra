import logging
import uuid

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QInputDialog,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
)

from difra.gui.extra.resizable_zone import (
    ResizableEllipseItem,
    ResizableRectangleItem,
    ResizableSquareItem,
    ResizableZoneItem,
)
from difra.gui.main_window_ext.shape_calibration_mixin import ShapeCalibrationMixin

logger = logging.getLogger(__name__)


class ShapeTableMixin(ShapeCalibrationMixin):
    CALIBRATION_DELETE_ROLES = {
        "sample holder",
        "holder circle",
        "calibration square",
    }
    ZONE_DELETE_ROLES = {
        "include",
        "exclude",
        "sample holder",
        "holder circle",
        "calibration square",
    }

    def _session_has_measurements_for_shape_lock(self) -> bool:
        session_manager = getattr(self, "session_manager", None)
        has_measurements_fn = getattr(session_manager, "has_point_measurements", None)
        if callable(has_measurements_fn):
            try:
                return bool(has_measurements_fn())
            except Exception as exc:
                logger.debug(
                    "Failed to query session measurement state for shape lock: %s",
                    exc,
                    exc_info=True,
                )
        return False

    def _mark_current_shapes_as_measurement_locked(self):
        for shape_info in getattr(self.image_view, "shapes", []) or []:
            if not isinstance(shape_info, dict):
                continue
            shape_info["locked_after_measurements"] = True
            shape_info["isNew"] = False

    def _shape_can_be_deleted(self, shape_info) -> tuple[bool, str]:
        if not shape_info:
            return False, "Unknown shape"
        if not self._session_has_measurements_for_shape_lock():
            return True, ""

        if bool(shape_info.get("locked_after_measurements", False)):
            role = str(shape_info.get("role", "include") or "include")
            return (
                False,
                f"Shape '{shape_info.get('id', '?')}' ({role}) was already used during measurement setup and cannot be deleted.",
            )
        if not bool(shape_info.get("isNew", False)):
            role = str(shape_info.get("role", "include") or "include")
            return (
                False,
                f"Shape '{shape_info.get('id', '?')}' ({role}) belongs to an existing measured session and cannot be deleted.",
            )
        return True, ""

    def _delete_shape_infos_internal(self, shapes_to_delete, *, clear_dependents: bool):
        for shape_info in list(shapes_to_delete):
            shape_item = shape_info.get("item")
            if shape_item is not None:
                try:
                    self.image_view.scene.removeItem(shape_item)
                except RuntimeError:
                    pass
                except Exception:
                    logger.debug("Failed to remove shape item during deletion", exc_info=True)
            self._remove_shape_overlay_items(shape_info)
            if shape_info in self.image_view.shapes:
                self.image_view.shapes.remove(shape_info)

        if clear_dependents:
            clear_profiles = getattr(self, "_clear_profile_paths", None)
            if callable(clear_profiles):
                try:
                    clear_profiles()
                except Exception:
                    logger.debug("Failed to clear profile paths after shape deletion", exc_info=True)
            delete_points = getattr(self, "delete_all_points", None)
            if callable(delete_points):
                try:
                    delete_points()
                except Exception:
                    logger.debug("Failed to clear points after shape deletion", exc_info=True)

        self._refresh_sample_photo_calibration()
        self.update_shape_table()
        refresh_points = getattr(self, "update_points_table", None)
        if callable(refresh_points):
            try:
                refresh_points()
            except Exception:
                logger.debug("Failed to refresh points table after shape deletion", exc_info=True)

    def _delete_shape_infos(self, shapes_to_delete):
        shape_list = [shape for shape in list(shapes_to_delete or []) if shape in self.image_view.shapes]
        if not shape_list:
            return False

        blocked_messages = []
        allowed_shapes = []
        for shape_info in shape_list:
            can_delete, reason = self._shape_can_be_deleted(shape_info)
            if can_delete:
                allowed_shapes.append(shape_info)
            elif reason:
                blocked_messages.append(reason)

        if blocked_messages:
            QMessageBox.warning(
                self,
                "Shape Deletion Blocked",
                "\n".join(blocked_messages),
            )
        if not allowed_shapes:
            return False

        roles_deleted = {
            str(shape.get("role", "include") or "include").lower()
            for shape in allowed_shapes
        }
        has_measurements = self._session_has_measurements_for_shape_lock()
        if not has_measurements and roles_deleted & self.CALIBRATION_DELETE_ROLES:
            self.delete_all_shapes_from_table(force=True)
            return True

        clear_dependents = (not has_measurements) and bool(roles_deleted & self.ZONE_DELETE_ROLES)
        self._delete_shape_infos_internal(allowed_shapes, clear_dependents=clear_dependents)
        return True

    def create_shape_table(self):
        self._ensure_shape_calibration_defaults()
        self.shapeDock = QDockWidget("Shapes", self)
        self.shapeDock.setObjectName("ShapesDock")
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
        self.apply_shape_role(shape_info)
        self.update_shape_table()

    def apply_shape_role(self, shape_info):
        self._ensure_shape_calibration_defaults()
        role = shape_info.get("role", "include")
        item = shape_info["item"]
        callback = getattr(self, "_on_shape_geometry_changed", None)
        if callable(callback) and hasattr(item, "geometry_changed_callback"):
            try:
                item.geometry_changed_callback = lambda si=shape_info: callback(si)
            except Exception:
                logger.debug("Failed to attach shape geometry callback", exc_info=True)

        self._remove_shape_overlay_items(shape_info)

        if role == "include":
            item.setPen(QPen(QColor("green"), 2))
        elif role == "exclude":
            item.setPen(QPen(QColor("red"), 2))
        elif role == "sample holder":
            rect = item.rect() if hasattr(item, "rect") else item.boundingRect()
            cx = rect.x() + rect.width() / 2
            cy = rect.y() + rect.height() / 2
            side = min(rect.width(), rect.height())
            new_x = cx - side / 2
            new_y = cy - side / 2
            if hasattr(item, "setRect"):
                item.setRect(new_x, new_y, side, side)
            item.setPen(QPen(QColor("purple"), 2))
            diag1 = QGraphicsLineItem(new_x, new_y, new_x + side, new_y + side)
            diag2 = QGraphicsLineItem(new_x + side, new_y, new_x, new_y + side)
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
            pixels_per_mm = side / 18.0
            shape_info["pixels_per_mm"] = pixels_per_mm
            self.pixel_to_mm_ratio = pixels_per_mm
            self.include_center = (cx, cy)
            self.update_conversion_label()
            self._draw_stage_limit_outline(shape_info, cx, cy)
        elif role == self.ROLE_CALIBRATION_SQUARE:
            payload = self._shape_center_and_extent(shape_info)
            rect, cx, cy = payload if payload is not None else (item.rect(), 0.0, 0.0)
            item.setPen(QPen(QColor("purple"), 2))
            shape_info["type"] = "Rectangle"
            shape_info["center_px"] = (float(cx), float(cy))
            shape_info["physical_size_mm"] = float(
                shape_info.get("physical_size_mm") or self.sample_photo_calibration_square_mm_default
            )

            diag1 = QGraphicsLineItem(rect.left(), rect.top(), rect.right(), rect.bottom())
            diag2 = QGraphicsLineItem(rect.right(), rect.top(), rect.left(), rect.bottom())
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
            shape_info["pixels_per_mm"] = min(rect.width(), rect.height()) / float(shape_info["physical_size_mm"])
            self._draw_stage_limit_outline(shape_info, cx, cy)
            self._refresh_sample_photo_calibration()
        elif role == self.ROLE_HOLDER_CIRCLE:
            payload = self._shape_center_and_extent(shape_info)
            rect, cx, cy = payload if payload is not None else (item.rect(), 0.0, 0.0)
            item.setPen(QPen(QColor("#1565C0"), 2))
            shape_info["type"] = "Circle"
            shape_info["role"] = self.ROLE_HOLDER_CIRCLE
            shape_info["center_px"] = (float(cx), float(cy))
            shape_info["physical_size_mm"] = float(
                shape_info.get("physical_size_mm") or self.sample_photo_holder_circle_mm_default
            )
            effective_diameter = max(rect.width(), rect.height())
            shape_info["pixels_per_mm"] = effective_diameter / float(shape_info["physical_size_mm"])
            horiz = QGraphicsLineItem(rect.left(), cy, rect.right(), cy)
            vert = QGraphicsLineItem(cx, rect.top(), cx, rect.bottom())
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
            item.setPen(QPen(QColor("black"), 2))

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
            for shape_info in self.image_view.shapes:
                if not shape_info.get("uid"):
                    shape_info["uid"] = f"sh_{uuid.uuid4().hex}"
                if shape_uid and str(shape_info.get("uid", "")).strip() == shape_uid:
                    if role in (self.ROLE_CALIBRATION_SQUARE, self.ROLE_HOLDER_CIRCLE):
                        current_value = shape_info.get("physical_size_mm")
                        prompted = self._prompt_physical_size_mm(
                            role=role, current_value=current_value
                        )
                        if prompted is None:
                            return
                        shape_info["physical_size_mm"] = float(prompted)
                        self._remove_conflicting_calibration_shapes(shape_info, role)
                    shape_info["role"] = role
                    if role == self.ROLE_HOLDER_CIRCLE:
                        self._prioritize_holder_circle_shape(shape_info)
                    self.apply_shape_role(shape_info)
                    self._refresh_sample_photo_calibration()
                    break
            self.update_shape_table()
        except Exception as e:
            logger.warning("Error updating shape role: %s", e, exc_info=True)

    def update_shape_table(self):
        shapes = getattr(self.image_view, "shapes", [])
        self.shapeTable.blockSignals(True)
        self.shapeTable.setRowCount(len(shapes))
        for row, shape_info in enumerate(shapes):
            if "role" in shape_info:
                self.apply_shape_role(shape_info)
            else:
                shape_info["role"] = "include"

            role = shape_info["role"]
            item = shape_info.get("item")
            rect = item.rect() if hasattr(item, "rect") else item.boundingRect()
            if not shape_info.get("uid"):
                shape_info["uid"] = f"sh_{uuid.uuid4().hex}"

            id_item = QTableWidgetItem(str(shape_info.get("id", "")))
            id_item.setData(Qt.UserRole, str(shape_info.get("uid")))
            self.shapeTable.setItem(row, 0, id_item)
            self.shapeTable.setItem(row, 1, QTableWidgetItem(shape_info.get("type", "")))
            self.shapeTable.setItem(row, 2, QTableWidgetItem(f"{rect.x():.2f}"))
            self.shapeTable.setItem(row, 3, QTableWidgetItem(f"{rect.y():.2f}"))
            self.shapeTable.setItem(row, 4, QTableWidgetItem(f"{rect.width():.2f}"))
            self.shapeTable.setItem(row, 5, QTableWidgetItem(f"{rect.height():.2f}"))
            self.shapeTable.setItem(row, 6, QTableWidgetItem(role))

            if shape_info.get("isNew", False):
                color = QColor("gray")
            elif role == "include":
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
                        elif isinstance(item, ResizableRectangleItem):
                            item.setRect(x, y, max(10.0, w), max(10.0, h))
                            updater = getattr(item, "_update_handle_positions", None)
                            if callable(updater):
                                updater()
                        elif isinstance(item, ResizableZoneItem):
                            item._center_x = x + max(w, h) / 2.0
                            item._center_y = y + max(w, h) / 2.0
                            callback = getattr(item, "geometry_changed_callback", None)
                            item.geometry_changed_callback = None
                            item.set_radius(max(w, h) / 2.0)
                            item.geometry_changed_callback = callback
                        elif isinstance(item, ResizableEllipseItem):
                            item.setRect(x, y, max(10.0, w), max(10.0, h))
                            updater = getattr(item, "_update_handle_positions", None)
                            if callable(updater):
                                updater()
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
        self.shapeTable.keyPressEvent = self.shapeTableKeyPressEvent

    def shapeTableKeyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.delete_shapes_from_table()
        else:
            QTableWidget.keyPressEvent(self.shapeTable, event)

    def delete_shapes_from_table(self):
        selected_rows = sorted(
            {index.row() for index in self.shapeTable.selectionModel().selectedRows()},
            reverse=True,
        )
        if not selected_rows:
            selected_rows = sorted(
                {index.row() for index in self.shapeTable.selectedIndexes()},
                reverse=True,
            )
        selected_shapes = []
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
                    selected_shapes.append(shape_info)
                    break
        self._delete_shape_infos(selected_shapes)

    def delete_all_shapes_from_table(self, force: bool = False):
        shapes_to_delete = list(self.image_view.shapes)
        if not shapes_to_delete:
            return
        if force:
            self._delete_shape_infos_internal(shapes_to_delete, clear_dependents=True)
            return
        self._delete_shape_infos(shapes_to_delete)
