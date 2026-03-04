"""Procedural table/update helpers extracted from ZonePointsMixin."""


def update_points_table_safe(owner):
    """Minimal safe table update for restore operations (no widgets)."""
    try:
        if not hasattr(owner, "pointsTable") or owner.pointsTable is None:
            print("Info: pointsTable not available for safe update")
            return

        owner._updating_points_table = True
        try:
            owner.pointsTable.blockSignals(True)
            points = owner._build_points_snapshot()
            owner.pointsTable.setRowCount(len(points))

            for idx, (x, y, _ptype, point_id, point_uid) in enumerate(points):
                from PyQt5.QtCore import Qt
                from PyQt5.QtWidgets import QTableWidgetItem

                id_item = QTableWidgetItem("" if point_id is None else str(point_id))
                if point_id is not None:
                    id_item.setData(Qt.UserRole, int(point_id))
                id_item.setData(Qt.UserRole + 1, str(point_uid))
                owner._set_table_item_editable(id_item, editable=False)
                owner.pointsTable.setItem(idx, 0, id_item)
                x_item = QTableWidgetItem(f"{x:.2f}")
                y_item = QTableWidgetItem(f"{y:.2f}")
                x_mm_item = QTableWidgetItem("N/A")
                y_mm_item = QTableWidgetItem("N/A")
                owner._set_table_item_editable(x_item, editable=True)
                owner._set_table_item_editable(y_item, editable=True)
                owner._set_table_item_editable(x_mm_item, editable=True)
                owner._set_table_item_editable(y_mm_item, editable=True)
                owner.pointsTable.setItem(idx, 1, x_item)
                owner.pointsTable.setItem(idx, 2, y_item)
                owner.pointsTable.setItem(idx, 3, x_mm_item)
                owner.pointsTable.setItem(idx, 4, y_mm_item)
        finally:
            owner.pointsTable.blockSignals(False)
            owner._updating_points_table = False

        print(f"Safe table update completed with {len(points)} points")
    except Exception as exc:
        print(f"Error in safe table update: {exc}")


def update_points_table(owner):
    """Update the points table with current point data and measurement widgets."""
    try:
        if not hasattr(owner, "pointsTable") or owner.pointsTable is None:
            print("Info: pointsTable is not initialized, skipping table update")
            return

        if not hasattr(owner, "zonePointsDock"):
            print("Info: Zone points widget not created yet, using safe update")
            owner.update_points_table_safe()
            return

        if not hasattr(owner, "measurement_widgets"):
            owner.measurement_widgets = {}

        owner._updating_points_table = True
        try:
            owner.pointsTable.blockSignals(True)
            points = owner._build_points_snapshot()
            owner._cleanup_deleted_widgets(points)
            owner.pointsTable.setRowCount(len(points))
            owner._populate_table_rows(points)
        finally:
            owner.pointsTable.blockSignals(False)
            owner._updating_points_table = False

        print(
            f"Updated table with {len(points)} points. Widget keys: {list(owner.measurement_widgets.keys())}"
        )
    except Exception as exc:
        print(f"Error updating points table: {exc}")
        import traceback

        traceback.print_exc()


def delete_selected_points(owner):
    """Delete selected points, enforcing measured/skipped rules."""
    selected_rows = sorted(
        {ix.row() for ix in owner.pointsTable.selectedIndexes()},
        reverse=True,
    )
    if not selected_rows:
        return

    active_measurement = owner._measurement_sequence_active()
    skip_reason_for_measured = None
    changed_any = False

    for row in selected_rows:
        point_uid, point_display_id = owner._get_point_identity_from_row(row)
        if not point_uid:
            continue

        measured = owner._is_row_measured(row=row, point_uid=point_uid)
        if measured:
            if skip_reason_for_measured is None:
                skip_reason_for_measured = owner._prompt_skip_reason(
                    "Measured Point",
                    "Measured points cannot be deleted.\n"
                    "Provide reason to mark selected measured point(s) as SKIPPED:",
                )
                if skip_reason_for_measured is None:
                    continue
            changed_any = (
                owner._skip_point_by_row(
                    row=row,
                    reason=skip_reason_for_measured,
                )
                or changed_any
            )
            continue

        changed_any = (
            owner._delete_row_and_container_point(
                row=row,
                point_uid=point_uid,
                point_display_id=point_display_id,
            )
            or changed_any
        )

        if active_measurement and hasattr(owner, "_append_measurement_log"):
            point_label = (
                f"#{point_display_id}"
                if point_display_id is not None
                else str(point_uid)
            )
            owner._append_measurement_log(
                f"[CAPTURE] Point {point_label} deleted from pending plan"
            )

    if changed_any:
        owner.update_points_table()


def delete_all_points(owner):
    """Remove all generated/user points and their measurement widgets."""
    for item in owner.image_view.points_dict["generated"]["points"]:
        owner.safe_remove_item(item)
    for item in owner.image_view.points_dict["generated"]["zones"]:
        owner.safe_remove_item(item)
    owner.image_view.points_dict["generated"]["points"].clear()
    owner.image_view.points_dict["generated"]["zones"].clear()
    for item in owner.image_view.points_dict["user"]["points"]:
        owner.safe_remove_item(item)
    for item in owner.image_view.points_dict["user"]["zones"]:
        owner.safe_remove_item(item)
    owner.image_view.points_dict["user"]["points"].clear()
    owner.image_view.points_dict["user"]["zones"].clear()
    for point_uid in list(getattr(owner, "_measurement_items", {}).keys()):
        owner.remove_measurement_widget_from_panel(point_uid)
    owner.measurement_widgets = {}
    owner.next_point_id = 1
