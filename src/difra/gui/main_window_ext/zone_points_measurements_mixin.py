"""Main zone points extension functionality."""

import logging
import uuid
from typing import Any, List, Optional, Tuple

from difra.gui.qt_compat import sip
from difra.gui.qt_compat import Qt
from difra.gui.qt_compat import (
    QTableWidgetItem,
    QTreeWidgetItem,
)

from difra.gui.technical.widgets import MeasurementHistoryWidget

from . import zone_points_actions

logger = logging.getLogger(__name__)


class ZonePointsMeasurementsMixin:
    """Zone points behavior split from ZonePointsMixin."""

    def update_points_table_safe(self):
        """Minimal safe table update for restore operations (no widgets)."""
        return zone_points_actions.update_points_table_safe(self)

    def update_points_table(self):
        """Update the points table with current point data and measurement widgets."""
        return zone_points_actions.update_points_table(self)

    def _normalize_point_item_identity(
        self,
        point_item,
        fallback_display_id: int,
        used_uids: set,
    ) -> Tuple[Optional[int], str]:
        point_display_id: Optional[int] = None
        point_uid = ""

        try:
            pid = point_item.data(1)
            if pid is not None:
                point_display_id = int(pid)
        except Exception:
            point_display_id = None

        if point_display_id is None:
            point_display_id = int(fallback_display_id)
            try:
                point_item.setData(1, point_display_id)
            except Exception:
                pass

        try:
            uid_data = point_item.data(2)
            if uid_data is not None:
                point_uid = str(uid_data).strip()
        except Exception:
            point_uid = ""

        if not point_uid:
            point_uid = self._new_point_uid(point_display_id)

        while point_uid in used_uids:
            point_uid = self._new_point_uid(point_display_id)
        used_uids.add(point_uid)

        try:
            point_item.setData(2, point_uid)
        except Exception:
            pass

        try:
            self.next_point_id = max(
                int(getattr(self, "next_point_id", 1)), int(point_display_id) + 1
            )
        except Exception:
            pass

        return point_display_id, point_uid

    def _build_points_snapshot(
        self,
    ) -> List[Tuple[float, float, str, Optional[int], str]]:
        """Build a snapshot of all current points with their data."""
        points = []
        used_uids = set()
        fallback_display_id = 1

        # Safety check - ensure image_view and points_dict exist
        if not hasattr(self, "image_view") or not hasattr(
            self.image_view, "points_dict"
        ):
            logger.warning(
                "image_view or points_dict not available while building points snapshot"
            )
            return points

        try:
            # Generated points
            for item in self.image_view.points_dict["generated"]["points"]:
                try:
                    if item is None or sip.isdeleted(item):
                        continue
                    c = item.sceneBoundingRect().center()
                    pid, point_uid = self._normalize_point_item_identity(
                        item,
                        fallback_display_id=fallback_display_id,
                        used_uids=used_uids,
                    )
                    points.append(
                        (
                            c.x(),
                            c.y(),
                            "generated",
                            int(pid) if pid is not None else None,
                            point_uid,
                        )
                    )
                    fallback_display_id = max(
                        fallback_display_id + 1,
                        int(pid) + 1 if pid is not None else fallback_display_id + 1,
                    )
                except Exception as e:
                    logger.warning(
                        "Error processing generated point: %s", e, exc_info=True
                    )
                    continue

            # User points
            for item in self.image_view.points_dict["user"]["points"]:
                try:
                    if item is None or sip.isdeleted(item):
                        continue
                    c = item.sceneBoundingRect().center()
                    pid, point_uid = self._normalize_point_item_identity(
                        item,
                        fallback_display_id=fallback_display_id,
                        used_uids=used_uids,
                    )
                    points.append(
                        (
                            c.x(),
                            c.y(),
                            "user",
                            int(pid) if pid is not None else None,
                            point_uid,
                        )
                    )
                    fallback_display_id = max(
                        fallback_display_id + 1,
                        int(pid) + 1 if pid is not None else fallback_display_id + 1,
                    )
                except Exception as e:
                    logger.warning("Error processing user point: %s", e, exc_info=True)
                    continue

        except Exception as e:
            logger.warning("Error building points snapshot: %s", e, exc_info=True)

        return points

    def _cleanup_deleted_widgets(
        self, points: List[Tuple[float, float, str, Optional[int], str]]
    ):
        """Clean up measurement widgets for points that no longer exist."""
        current_point_uids = {
            str(uid).strip() for (_, _, _, _pid, uid) in points if str(uid).strip()
        }

        # Remove widgets/tree items for deleted points
        stale_uids = set(getattr(self, "measurement_widgets", {}).keys()) | set(
            getattr(self, "_measurement_items", {}).keys()
        )
        for point_uid in list(stale_uids):
            uid_txt = str(point_uid).strip()
            if uid_txt and uid_txt not in current_point_uids:
                self.remove_measurement_widget_from_panel(uid_txt)
                logger.debug("Cleaned up widget for deleted point UID %s", uid_txt)

    def _populate_table_rows(
        self, points: List[Tuple[float, float, str, Optional[int], str]]
    ):
        """Populate table rows with point data and reattach measurement widgets."""
        for idx, (x, y, ptype, point_id, point_uid) in enumerate(points):
            # Set basic point data
            id_item = QTableWidgetItem("" if point_id is None else str(point_id))
            if point_id is not None:
                id_item.setData(Qt.UserRole, int(point_id))
            id_item.setData(Qt.UserRole + 1, str(point_uid))
            self._set_table_item_editable(id_item, editable=False)
            self.pointsTable.setItem(
                idx,
                0,
                id_item,
            )
            x_item = QTableWidgetItem(f"{x:.2f}")
            y_item = QTableWidgetItem(f"{y:.2f}")
            self._set_table_item_editable(x_item, editable=True)
            self._set_table_item_editable(y_item, editable=True)
            self.pointsTable.setItem(idx, 1, x_item)
            self.pointsTable.setItem(idx, 2, y_item)

            # Set coordinate data
            if self.pixel_to_mm_ratio:
                if hasattr(self, "_pixel_to_physical_mm"):
                    x_mm, y_mm = self._pixel_to_physical_mm(x, y)
                else:
                    x_mm = (
                        self.real_x_pos_mm.value()
                        - (x - self.include_center[0]) / self.pixel_to_mm_ratio
                    )
                    y_mm = (
                        self.real_y_pos_mm.value()
                        - (y - self.include_center[1]) / self.pixel_to_mm_ratio
                    )
                x_mm_item = QTableWidgetItem(f"{x_mm:.2f}")
                y_mm_item = QTableWidgetItem(f"{y_mm:.2f}")
            else:
                x_mm_item = QTableWidgetItem("N/A")
                y_mm_item = QTableWidgetItem("N/A")
            self._set_table_item_editable(x_mm_item, editable=True)
            self._set_table_item_editable(y_mm_item, editable=True)
            self.pointsTable.setItem(idx, 3, x_mm_item)
            self.pointsTable.setItem(idx, 4, y_mm_item)

            # Do not attach measurement widgets in the table anymore. They live in the right panel.

    def _attach_measurement_widget(self, row_index: int, point_uid: Optional[str]):
        """Deprecated for table. Measurement widgets are managed in the right panel."""
        if not point_uid:
            return
        # No-op: widgets are added via add_measurement_widget_to_panel

    def _format_point_label(
        self, point_uid: Optional[str], point_display_id: Optional[int]
    ) -> str:
        if point_display_id is not None:
            return f"Point #{point_display_id}"
        parsed = self._display_id_from_uid(point_uid)
        if parsed is not None:
            return f"Point #{parsed}"
        uid_text = str(point_uid or "").strip()
        return f"Point {uid_text[:8]}" if uid_text else "Point"

    def _create_measurement_widget(
        self, point_uid: str, point_display_id: Optional[int]
    ) -> Any:
        """Create a new measurement widget for a point."""
        return MeasurementHistoryWidget(
            masks=getattr(self, "masks", {}),
            ponis=getattr(self, "ponis", {}),
            parent=self,
            point_id=point_display_id if point_display_id is not None else point_uid,
        )

    def add_measurement_widget_to_panel(
        self, point_uid: str, point_display_id: Optional[int] = None
    ):
        """Add a measurement widget for a point to the right tree (if not exists)."""
        if getattr(self, "_restoring_state", False) and not getattr(
            self,
            "_restoring_measurement_history_widgets",
            False,
        ):
            return
        point_uid = str(point_uid or "").strip()
        if not point_uid:
            return
        if point_display_id is None:
            point_display_id = self._display_id_from_uid(point_uid)
        # If already exists, do nothing
        if point_uid in self._measurement_items:
            top_item, child_item, w = self._measurement_items.get(
                point_uid, (None, None, None)
            )
            if w is not None and not sip.isdeleted(w):
                return
        # Create tree items
        top_item = QTreeWidgetItem(
            self.measurementsTree,
            [
                self._format_point_label(
                    point_uid=point_uid, point_display_id=point_display_id
                )
            ],
        )
        child_item = QTreeWidgetItem(top_item, [""])
        self.measurementsTree.addTopLevelItem(top_item)
        top_item.setExpanded(True)
        # Create widget and place into child row, column 0
        w = self._create_measurement_widget(point_uid, point_display_id)
        self.measurementsTree.setItemWidget(child_item, 0, w)
        self.measurement_widgets[point_uid] = w
        self._measurement_items[point_uid] = (top_item, child_item, w)

    def remove_measurement_widget_from_panel(self, point_uid: str):
        """Remove the measurement widget and its items from the tree."""
        point_uid = str(point_uid or "").strip()
        if not point_uid:
            return
        top_item, child_item, w = self._measurement_items.pop(
            point_uid, (None, None, None)
        )
        if w and not sip.isdeleted(w):
            try:
                # Detach from tree cell
                self.measurementsTree.setItemWidget(child_item, 0, None)
            except Exception:
                pass
            w.setParent(None)
            w.deleteLater()
        if top_item is not None:
            try:
                index = self.measurementsTree.indexOfTopLevelItem(top_item)
                if index != -1:
                    self.measurementsTree.takeTopLevelItem(index)
            except Exception:
                pass
        self.measurement_widgets.pop(point_uid, None)

    def _snapshot_history_widgets(self):
        """Return {point_uid: [measurement_dict, ...]} from existing widgets."""
        snap = {}
        for point_uid, w in list(getattr(self, "measurement_widgets", {}).items()):
            if w is not None and not sip.isdeleted(w):
                snap[point_uid] = list(getattr(w, "measurements", []))
        return snap

    @staticmethod
    def _new_point_uid(counter: int) -> str:
        try:
            counter_int = int(counter)
        except Exception:
            counter_int = 0
        return f"{counter_int}_{uuid.uuid4().hex[:8]}"
