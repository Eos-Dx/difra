"""Main zone points extension functionality."""

from difra.gui.qt_compat import exec_dialog
import logging
from typing import Optional, Tuple

from difra.gui.qt_compat import sip
from difra.gui.qt_compat import Qt
from difra.gui.qt_compat import QColor
from difra.gui.qt_compat import (
    QInputDialog,
    QMenu,
    QMessageBox,
)


from . import zone_points_actions

logger = logging.getLogger(__name__)


class ZonePointsSkipDeleteMixin:
    """Zone points behavior split from ZonePointsMixin."""

    def _measurement_sequence_active(self) -> bool:
        try:
            return (
                int(getattr(self, "total_points", 0)) > 0
                and hasattr(self, "start_btn")
                and not self.start_btn.isEnabled()
            )
        except Exception:
            return False

    def _show_points_table_context_menu(self, pos):
        if not hasattr(self, "pointsTable") or self.pointsTable is None:
            return

        menu = QMenu(self.pointsTable)
        delete_action = menu.addAction("Delete Selected Point(s)")
        skip_action = menu.addAction("Mark Selected as Skipped...")
        chosen = exec_dialog(menu, self.pointsTable.viewport().mapToGlobal(pos))
        if chosen == delete_action:
            self.delete_selected_points()
        elif chosen == skip_action:
            self.mark_selected_points_skipped()

    def _prompt_skip_reason(self, title: str, prompt: str) -> Optional[str]:
        reason, ok = QInputDialog.getText(self, title, prompt)
        if not ok:
            return None
        return str(reason or "").strip() or "user_skipped"

    def _find_sorted_position_for_row(self, row: int) -> Optional[int]:
        sorted_indices = list(getattr(self, "sorted_indices", []) or [])
        for pos, idx in enumerate(sorted_indices):
            if int(idx) == int(row):
                return pos
        return None

    def _session_point_index_for_row(self, row: int) -> int:
        pos = self._find_sorted_position_for_row(row)
        mapped = getattr(self, "_session_point_indices", None)
        if pos is not None and isinstance(mapped, (list, tuple)) and pos < len(mapped):
            try:
                return int(mapped[pos])
            except Exception:
                pass
        return int(row) + 1

    @staticmethod
    def _display_id_from_uid(point_uid: Optional[str]) -> Optional[int]:
        uid = str(point_uid or "").strip()
        if not uid:
            return None
        prefix = uid.split("_", 1)[0]
        try:
            return int(prefix)
        except Exception:
            return None

    def _get_point_identity_from_row(
        self,
        row: int,
    ) -> Tuple[Optional[str], Optional[int]]:
        point_uid: Optional[str] = None
        point_display_id: Optional[int] = None
        point_item = None

        if hasattr(self, "pointsTable") and self.pointsTable is not None:
            id_item = self.pointsTable.item(row, 0)
            if id_item is not None:
                uid_data = id_item.data(Qt.UserRole + 1)
                if uid_data is not None:
                    uid_txt = str(uid_data).strip()
                    if uid_txt:
                        point_uid = uid_txt
                display_role = id_item.data(Qt.UserRole)
                if display_role is not None:
                    try:
                        point_display_id = int(display_role)
                    except Exception:
                        point_display_id = None
                if point_display_id is None:
                    try:
                        txt = str(id_item.text() or "").strip()
                        if txt:
                            point_display_id = int(txt)
                    except Exception:
                        point_display_id = None

        try:
            gp = self.image_view.points_dict["generated"]["points"]
            up = self.image_view.points_dict["user"]["points"]
            if row < len(gp):
                point_item = gp[row]
            else:
                urow = row - len(gp)
                if 0 <= urow < len(up):
                    point_item = up[urow]
        except Exception:
            point_item = None

        if point_item is not None and not sip.isdeleted(point_item):
            if point_display_id is None:
                try:
                    pid = point_item.data(1)
                    if pid is not None:
                        point_display_id = int(pid)
                except Exception:
                    point_display_id = None

            if not point_uid:
                try:
                    uid_data = point_item.data(2)
                    if uid_data is not None:
                        uid_txt = str(uid_data).strip()
                        if uid_txt:
                            point_uid = uid_txt
                except Exception:
                    point_uid = None

        if point_display_id is None and point_uid:
            point_display_id = self._display_id_from_uid(point_uid)

        if not point_uid and point_display_id is not None:
            point_uid = self._new_point_uid(point_display_id)
            try:
                if point_item is not None and not sip.isdeleted(point_item):
                    point_item.setData(2, point_uid)
            except Exception:
                pass

        return point_uid, point_display_id

    def _point_has_measurements(self, point_uid: Optional[str]) -> bool:
        point_uid = str(point_uid or "").strip()
        if not point_uid:
            return False
        widget = getattr(self, "measurement_widgets", {}).get(point_uid)
        if widget is None:
            return False
        try:
            return len(getattr(widget, "measurements", []) or []) > 0
        except Exception:
            return False

    def _is_row_measured(self, row: int, point_uid: Optional[str]) -> bool:
        if self._point_has_measurements(point_uid):
            return True

        sorted_pos = self._find_sorted_position_for_row(row)
        if sorted_pos is not None:
            try:
                return sorted_pos < int(
                    getattr(self, "current_measurement_sorted_index", 0)
                )
            except Exception:
                return False
        return False

    def _append_skipped_point_record(
        self,
        row: int,
        point_uid: Optional[str],
        point_display_id: Optional[int],
        reason: str,
    ) -> None:
        x_mm = None
        y_mm = None
        try:
            x_item = self.pointsTable.item(row, 3)
            y_item = self.pointsTable.item(row, 4)
            if x_item is not None:
                txt = str(x_item.text() or "").strip()
                if txt and txt != "N/A":
                    x_mm = float(txt)
            if y_item is not None:
                txt = str(y_item.text() or "").strip()
                if txt and txt != "N/A":
                    y_mm = float(txt)
        except Exception:
            pass

        payload = {
            "point_index": int(row),
            "point_uid": str(point_uid or "").strip() or None,
            "point_id": int(point_display_id) if point_display_id is not None else None,
            "x": x_mm,
            "y": y_mm,
            "reason": str(reason),
        }
        for container_name in ("state", "state_measurements"):
            container = getattr(self, container_name, None)
            if not isinstance(container, dict):
                continue
            skipped = list(container.get("skipped_points", []) or [])
            skipped = [
                item for item in skipped if int(item.get("point_index", -1)) != int(row)
            ]
            skipped.append(dict(payload))
            container["skipped_points"] = skipped

    def _apply_skipped_visual(self, point_uid: Optional[str]) -> None:
        point_uid = str(point_uid or "").strip()
        if not point_uid:
            return

        skip_point_color = QColor(255, 165, 0)
        skip_zone_color = QColor(255, 165, 0)
        skip_zone_color.setAlphaF(0.18)

        gp = self.image_view.points_dict["generated"]["points"]
        gz = self.image_view.points_dict["generated"]["zones"]
        up = self.image_view.points_dict["user"]["points"]
        uz = self.image_view.points_dict["user"]["zones"]

        for i, item in enumerate(gp):
            if sip.isdeleted(item):
                continue
            if str(item.data(2) or "").strip() == point_uid:
                item.setBrush(skip_point_color)
                if i < len(gz) and not sip.isdeleted(gz[i]):
                    gz[i].setBrush(skip_zone_color)
                return

        for i, item in enumerate(up):
            if sip.isdeleted(item):
                continue
            if str(item.data(2) or "").strip() == point_uid:
                item.setBrush(skip_point_color)
                if i < len(uz) and not sip.isdeleted(uz[i]):
                    uz[i].setBrush(skip_zone_color)
                return

    def _remove_row_from_active_measurement_plan(self, row: int) -> None:
        sorted_indices = list(getattr(self, "sorted_indices", []) or [])
        pos = self._find_sorted_position_for_row(row)
        if pos is not None and 0 <= pos < len(sorted_indices):
            sorted_indices.pop(pos)
        for idx, value in enumerate(sorted_indices):
            if int(value) > int(row):
                sorted_indices[idx] = int(value) - 1
        self.sorted_indices = sorted_indices

        mapped = getattr(self, "_session_point_indices", None)
        if isinstance(mapped, list) and pos is not None and 0 <= pos < len(mapped):
            mapped.pop(pos)

        for container_name in ("state", "state_measurements"):
            container = getattr(self, container_name, None)
            if not isinstance(container, dict):
                continue
            points = container.get("measurement_points", None)
            if not isinstance(points, list):
                continue
            if pos is not None and 0 <= pos < len(points):
                points.pop(pos)
            for point in points:
                try:
                    pidx = int(point.get("point_index", -1))
                    if pidx > int(row):
                        point["point_index"] = pidx - 1
                except Exception:
                    continue

        current_idx = int(getattr(self, "current_measurement_sorted_index", 0))
        if pos is not None and pos < current_idx:
            current_idx -= 1
        if current_idx < 0:
            current_idx = 0
        self.current_measurement_sorted_index = current_idx
        self.total_points = len(self.sorted_indices)

        try:
            self.progressBar.setMaximum(self.total_points)
            self.progressBar.setValue(
                min(self.current_measurement_sorted_index, self.total_points)
            )
        except Exception:
            pass

        if self.total_points <= 0:
            if hasattr(self, "_append_capture_log"):
                self._append_capture_log("Measurement sequence complete")
            if hasattr(self, "_set_measurement_controls_idle"):
                self._set_measurement_controls_idle()
            elif hasattr(self, "start_btn"):
                self.start_btn.setEnabled(True)

    def _skip_point_by_row(self, row: int, reason: str) -> bool:
        reason = str(reason or "").strip() or "user_skipped"
        sorted_pos = self._find_sorted_position_for_row(row)
        current_idx = int(getattr(self, "current_measurement_sorted_index", 0))
        is_current = sorted_pos is not None and sorted_pos == current_idx

        capture_thread = getattr(self, "capture_thread", None)
        if (
            is_current
            and capture_thread is not None
            and hasattr(capture_thread, "isRunning")
        ):
            try:
                if capture_thread.isRunning():
                    QMessageBox.warning(
                        self,
                        "Skip Busy Point",
                        "Current point capture is already running. Skip it after capture finishes.",
                    )
                    return False
            except Exception:
                pass

        point_uid, point_display_id = self._get_point_identity_from_row(row)

        session_point_index = self._session_point_index_for_row(row)
        session_manager = getattr(self, "session_manager", None)
        if (
            session_manager is not None
            and hasattr(session_manager, "is_session_active")
            and session_manager.is_session_active()
            and hasattr(session_manager, "mark_point_skipped")
        ):
            try:
                session_manager.mark_point_skipped(
                    point_index=session_point_index,
                    reason=reason,
                )
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Skip Failed",
                    f"Failed to mark point as skipped in session container:\n{exc}",
                )
                return False

        self._append_skipped_point_record(
            row=row,
            point_uid=point_uid,
            point_display_id=point_display_id,
            reason=reason,
        )
        self._apply_skipped_visual(point_uid)

        if self._measurement_sequence_active():
            self._remove_row_from_active_measurement_plan(row)
            if (
                is_current
                and not bool(getattr(self, "paused", False))
                and not bool(getattr(self, "stopped", False))
                and int(getattr(self, "total_points", 0))
                > int(getattr(self, "current_measurement_sorted_index", 0))
                and hasattr(self, "measure_next_point")
            ):
                self.measure_next_point()

        if hasattr(self, "_append_measurement_log"):
            self._append_measurement_log(f"[CAPTURE] Point skipped (reason: {reason})")
        return True

    def _delete_row_and_container_point(
        self,
        row: int,
        point_uid: Optional[str],
        point_display_id: Optional[int],
    ) -> bool:
        point_uid = str(point_uid or "").strip()
        if not point_uid:
            return False

        sorted_pos = self._find_sorted_position_for_row(row)
        current_idx = int(getattr(self, "current_measurement_sorted_index", 0))
        is_current = sorted_pos is not None and sorted_pos == current_idx
        capture_thread = getattr(self, "capture_thread", None)
        if (
            is_current
            and capture_thread is not None
            and hasattr(capture_thread, "isRunning")
        ):
            try:
                if capture_thread.isRunning():
                    QMessageBox.warning(
                        self,
                        "Delete Busy Point",
                        "Current point capture is already running and cannot be deleted now.",
                    )
                    return False
            except Exception:
                pass

        session_manager = getattr(self, "session_manager", None)
        if (
            session_manager is not None
            and hasattr(session_manager, "is_session_active")
            and session_manager.is_session_active()
            and hasattr(session_manager, "delete_point")
        ):
            session_point_index = self._session_point_index_for_row(row)
            try:
                deleted = bool(
                    session_manager.delete_point(point_index=session_point_index)
                )
                if not deleted:
                    # Point may not be seeded into container yet (e.g. before first Start).
                    pass
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Delete Failed",
                    f"Cannot delete this point from the session container:\n{exc}",
                )
                return False

        if self._measurement_sequence_active():
            self._remove_row_from_active_measurement_plan(row)
            if (
                is_current
                and not bool(getattr(self, "paused", False))
                and not bool(getattr(self, "stopped", False))
                and int(getattr(self, "total_points", 0))
                > int(getattr(self, "current_measurement_sorted_index", 0))
                and hasattr(self, "measure_next_point")
            ):
                self.measure_next_point()

        self._remove_point_items_by_uid(point_uid, point_display_id=point_display_id)
        self.remove_measurement_widget_from_panel(point_uid)
        return True

    def _request_delete_point_by_uid(self, point_uid: str) -> bool:
        target_uid = str(point_uid or "").strip()
        if not target_uid:
            return False

        row = None
        if hasattr(self, "pointsTable") and self.pointsTable is not None:
            for idx in range(self.pointsTable.rowCount()):
                uid, _display = self._get_point_identity_from_row(idx)
                if uid == target_uid:
                    row = idx
                    break

        if row is None:
            return False

        point_uid, point_display_id = self._get_point_identity_from_row(row)
        measured = self._is_row_measured(row=row, point_uid=point_uid)
        if measured:
            reason = self._prompt_skip_reason(
                "Point Already Measured",
                "This point is already measured and cannot be deleted.\n"
                "Provide skip reason to mark it as SKIPPED:",
            )
            if reason is None:
                return False
            changed = self._skip_point_by_row(row=row, reason=reason)
            if changed:
                self.update_points_table()
            return changed

        changed = self._delete_row_and_container_point(
            row=row,
            point_uid=point_uid,
            point_display_id=point_display_id,
        )
        if changed:
            self.update_points_table()
        return changed

    def _request_delete_point_by_id(self, point_id: int) -> bool:
        row = None
        if hasattr(self, "pointsTable") and self.pointsTable is not None:
            for idx in range(self.pointsTable.rowCount()):
                _uid, display_id = self._get_point_identity_from_row(idx)
                try:
                    if display_id is not None and int(display_id) == int(point_id):
                        row = idx
                        break
                except Exception:
                    continue

        if row is None:
            return False

        point_uid, point_display_id = self._get_point_identity_from_row(row)
        measured = self._is_row_measured(row=row, point_uid=point_uid)
        if measured:
            reason = self._prompt_skip_reason(
                "Point Already Measured",
                "This point is already measured and cannot be deleted.\n"
                "Provide skip reason to mark it as SKIPPED:",
            )
            if reason is None:
                return False
            changed = self._skip_point_by_row(row=row, reason=reason)
            if changed:
                self.update_points_table()
            return changed

        changed = self._delete_row_and_container_point(
            row=row,
            point_uid=point_uid,
            point_display_id=point_display_id,
        )
        if changed:
            self.update_points_table()
        return changed

    def mark_selected_points_skipped(self):
        selected_rows = sorted(
            {ix.row() for ix in self.pointsTable.selectedIndexes()},
            reverse=True,
        )
        if not selected_rows:
            return

        reason = self._prompt_skip_reason("Skip Selected Points", "Skip reason:")
        if reason is None:
            return

        changed_any = False
        for row in selected_rows:
            changed_any = self._skip_point_by_row(row=row, reason=reason) or changed_any

        if changed_any:
            self.update_points_table()

    def delete_selected_points(self):
        """Delete selected points, enforcing measured/skipped rules."""
        return zone_points_actions.delete_selected_points(self)

    def delete_all_points(self):
        return zone_points_actions.delete_all_points(self)
        self.update_points_table()

    def _remove_point_items_by_uid(
        self, point_uid: str, point_display_id: Optional[int] = None
    ):
        point_uid = str(point_uid or "").strip()
        if not point_uid and point_display_id is None:
            return

        # Try generated first
        gp = self.image_view.points_dict["generated"]["points"]
        gz = self.image_view.points_dict["generated"]["zones"]
        for i, item in enumerate(gp):
            if sip.isdeleted(item):
                continue
            uid_match = (
                str(item.data(2) or "").strip() == point_uid if point_uid else False
            )
            id_match = False
            if point_display_id is not None:
                try:
                    id_match = int(item.data(1)) == int(point_display_id)
                except Exception:
                    id_match = False
            if uid_match or id_match:
                # remove both point and its matching zone
                point_item = gp.pop(i)
                zone_item = gz.pop(i) if i < len(gz) else None
                if zone_item:
                    self.safe_remove_item(zone_item)
                self.safe_remove_item(point_item)
                return

        # Then user points
        up = self.image_view.points_dict["user"]["points"]
        uz = self.image_view.points_dict["user"]["zones"]
        for i, item in enumerate(up):
            if sip.isdeleted(item):
                continue
            uid_match = (
                str(item.data(2) or "").strip() == point_uid if point_uid else False
            )
            id_match = False
            if point_display_id is not None:
                try:
                    id_match = int(item.data(1)) == int(point_display_id)
                except Exception:
                    id_match = False
            if uid_match or id_match:
                point_item = up.pop(i)
                zone_item = uz.pop(i) if i < len(uz) else None
                if zone_item:
                    self.safe_remove_item(zone_item)
                self.safe_remove_item(point_item)
                return

    def _remove_point_items_by_id(self, point_id):
        try:
            point_display_id = int(point_id)
        except Exception:
            return
        self._remove_point_items_by_uid("", point_display_id=point_display_id)
