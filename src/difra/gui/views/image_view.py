from difra.gui.image_view_ext.drawing_extension import DrawingMixin
from difra.gui.image_view_ext.point_editing_extension import (
    PointEditingMixin,
)
from difra.gui.image_view_ext.zoom_extension import ZoomMixin
from difra.gui.views.image_view_basic import ImageViewBasic
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QGraphicsEllipseItem, QMenu


class ImageView(ZoomMixin, DrawingMixin, PointEditingMixin, ImageViewBasic):

    def __init__(self, parent=None):
        super().__init__(parent)
        # Initialize drawing functionality.
        self.init_drawing()
        # Initialize point editing functionality.
        self.init_point_editing()
        # rotation_angle is already set in ImageViewBasic's __init__

    def rotate_image(self, angle):
        if self.image_item:
            # Update the rotation of the image_item.
            new_angle = self.image_item.rotation() + angle
            self.image_item.setRotation(new_angle)
            # Keep track of the cumulative rotation.
            self.rotation_angle += angle
        else:
            print("No image to rotate.")

    def delete_selected_shapes(self):
        selected_items = self.scene.selectedItems()
        if not selected_items:
            return

        shapes_to_delete = []
        for shape_info in list(self.shapes):
            shape_item = shape_info.get("item")
            diagonals = shape_info.get("diagonals") or []
            center_marker = shape_info.get("center_marker")
            for item in selected_items:
                if item is self.image_item:
                    continue
                if item is shape_item or item is center_marker or item in diagonals:
                    shapes_to_delete.append(shape_info)
                    break

        for shape_info in shapes_to_delete:
            if (
                main_window is not None
                and hasattr(main_window, "_delete_shape_infos")
            ):
                try:
                    main_window._delete_shape_infos(shapes_to_delete)
                    shapes_to_delete = []
                    break
                except Exception:
                    pass
            shape_item = shape_info.get("item")
            if shape_item is not None:
                self.scene.removeItem(shape_item)

            for extra_item in shape_info.get("diagonals") or []:
                self.scene.removeItem(extra_item)

            center_marker = shape_info.get("center_marker")
            if center_marker is not None:
                self.scene.removeItem(center_marker)

            if shape_info in self.shapes:
                self.shapes.remove(shape_info)

        main_window = self.window()
        if main_window is not None and hasattr(main_window, "_refresh_sample_photo_calibration"):
            try:
                main_window._refresh_sample_photo_calibration()
            except Exception:
                pass
        if self.shape_updated_callback:
            self.shape_updated_callback()

    def delete_selected_points(self) -> bool:
        main_window = self.window()
        if main_window is None:
            return False
        has_uid_api = hasattr(main_window, "_request_delete_point_by_uid")
        has_id_api = hasattr(main_window, "_request_delete_point_by_id")
        if not has_uid_api and not has_id_api:
            return False

        selected_items = list(self.scene.selectedItems() or [])
        point_refs = []
        seen_refs = set()
        for item in selected_items:
            if item is self.image_item:
                continue
            if not isinstance(item, QGraphicsEllipseItem):
                continue
            try:
                point_type = item.data(0)
                point_uid = str(item.data(2) or "").strip()
                point_id = item.data(1)
            except Exception:
                continue
            if point_type not in ("generated", "user"):
                continue
            if point_uid:
                key = ("uid", point_uid)
                if key not in seen_refs:
                    seen_refs.add(key)
                    point_refs.append(key)
                continue
            if point_id is not None:
                try:
                    key = ("id", int(point_id))
                except Exception:
                    continue
                if key not in seen_refs:
                    seen_refs.add(key)
                    point_refs.append(key)

        if not point_refs:
            return False

        changed = False
        for ref_type, ref_value in point_refs:
            try:
                if ref_type == "uid" and has_uid_api:
                    changed = bool(main_window._request_delete_point_by_uid(ref_value)) or changed
                elif has_id_api:
                    changed = bool(main_window._request_delete_point_by_id(ref_value)) or changed
            except Exception:
                continue
        return changed

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if not self.delete_selected_points():
                self.delete_selected_shapes()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        selected_items = [item for item in self.scene.selectedItems() if item is not self.image_item]
        if not selected_items:
            item_at_pos = self.itemAt(event.pos())
            if item_at_pos is not None and item_at_pos is not self.image_item:
                try:
                    item_at_pos.setSelected(True)
                except Exception:
                    pass
                selected_items = [item_at_pos]
        if not selected_items:
            super().contextMenuEvent(event)
            return

        menu = QMenu(self)
        selected_shape_infos = []
        main_window = self.window()
        if main_window is not None:
            for shape_info in getattr(main_window.image_view, "shapes", []):
                shape_item = shape_info.get("item")
                extras = list(shape_info.get("diagonals") or [])
                center_marker = shape_info.get("center_marker")
                if center_marker is not None:
                    extras.append(center_marker)
                if any(item is shape_item or item in extras for item in selected_items):
                    selected_shape_infos.append(shape_info)
        calibration_square_action = None
        holder_circle_action = None
        catch_auto_action = None
        edit_physical_size_action = None
        clear_profile_action = None
        if len(selected_shape_infos) == 1 and main_window is not None:
            calibration_square_action = menu.addAction("Define As Calibration Square...")
            holder_circle_action = menu.addAction("Define As Holder Circle...")
            catch_auto_action = menu.addAction("Catch Auto")
            edit_physical_size_action = menu.addAction("Edit Physical Size...")
            menu.addSeparator()
        selected_profile_info = None
        for profile_info in getattr(self, "profile_paths", []) or []:
            profile_item = profile_info.get("item")
            if any(item is profile_item for item in selected_items):
                selected_profile_info = profile_info
                break
        if selected_profile_info is not None:
            clear_profile_action = menu.addAction("Delete Profile Path")
            menu.addSeparator()
        delete_points_action = menu.addAction("Delete Selected Point(s)")
        delete_action = menu.addAction("Delete Selected Shape(s)")
        chosen = menu.exec_(event.globalPos())
        if chosen == calibration_square_action and selected_shape_infos:
            if hasattr(main_window, "define_shape_as_calibration_role"):
                main_window.define_shape_as_calibration_role(
                    selected_shape_infos[0],
                    "calibration square",
                )
        elif chosen == holder_circle_action and selected_shape_infos:
            if hasattr(main_window, "define_shape_as_calibration_role"):
                main_window.define_shape_as_calibration_role(
                    selected_shape_infos[0],
                    "holder circle",
                )
        elif chosen == catch_auto_action and selected_shape_infos:
            if hasattr(main_window, "catch_auto_for_shape"):
                main_window.catch_auto_for_shape(selected_shape_infos[0])
        elif chosen == edit_physical_size_action and selected_shape_infos:
            if hasattr(main_window, "edit_shape_physical_size_by_info"):
                main_window.edit_shape_physical_size_by_info(selected_shape_infos[0])
        elif chosen == clear_profile_action and selected_profile_info is not None:
            try:
                self.scene.removeItem(selected_profile_info.get("item"))
            except Exception:
                pass
            try:
                self.profile_paths.remove(selected_profile_info)
            except Exception:
                pass
        elif chosen == delete_points_action:
            self.delete_selected_points()
        elif chosen == delete_action:
            self.delete_selected_shapes()
