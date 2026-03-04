from __future__ import annotations

from difra.gui.main_window_ext import zone_points_actions


class _FakeSelectionIndex:
    def __init__(self, row: int) -> None:
        self._row = row

    def row(self) -> int:
        return self._row


class _FakePointsTable:
    def __init__(self, rows=None) -> None:
        self._rows = list(rows or [])
        self.blocked = []
        self.row_counts = []

    def selectedIndexes(self):
        return [_FakeSelectionIndex(row) for row in self._rows]

    def blockSignals(self, value: bool) -> None:
        self.blocked.append(value)

    def setRowCount(self, value: int) -> None:
        self.row_counts.append(value)


class _Owner:
    def __init__(self) -> None:
        self.safe_updates = 0
        self.updated = 0
        self.cleanup_calls = []
        self.populated = []
        self.logs = []
        self.skipped = []
        self.deleted = []
        self.pointsTable = _FakePointsTable()

    def update_points_table_safe(self) -> None:
        self.safe_updates += 1

    def _build_points_snapshot(self):
        return [(1.0, 2.0, "point", 1, "uid-1"), (3.0, 4.0, "point", 2, "uid-2")]

    def _cleanup_deleted_widgets(self, points) -> None:
        self.cleanup_calls.append(list(points))

    def _populate_table_rows(self, points) -> None:
        self.populated.append(list(points))

    def update_points_table(self) -> None:
        self.updated += 1

    def _measurement_sequence_active(self) -> bool:
        return True

    def _get_point_identity_from_row(self, row: int):
        return (f"uid-{row}", row + 10)

    def _is_row_measured(self, row: int, point_uid: str) -> bool:
        return row == 2

    def _prompt_skip_reason(self, title: str, text: str):
        self.prompt_args = (title, text)
        return "operator-note"

    def _skip_point_by_row(self, row: int, reason: str) -> bool:
        self.skipped.append((row, reason))
        return True

    def _delete_row_and_container_point(self, row: int, point_uid: str, point_display_id: int) -> bool:
        self.deleted.append((row, point_uid, point_display_id))
        return True

    def _append_measurement_log(self, message: str) -> None:
        self.logs.append(message)


class _ImageView:
    def __init__(self) -> None:
        self.points_dict = {
            "generated": {"points": ["gp1"], "zones": ["gz1"]},
            "user": {"points": ["up1"], "zones": ["uz1"]},
        }


class _DeleteAllOwner:
    def __init__(self) -> None:
        self.image_view = _ImageView()
        self.removed_items = []
        self.removed_widgets = []
        self._measurement_items = {"uid-1": object(), "uid-2": object()}
        self.measurement_widgets = {"uid-1": "widget"}
        self.next_point_id = 99

    def safe_remove_item(self, item) -> None:
        self.removed_items.append(item)

    def remove_measurement_widget_from_panel(self, point_uid: str) -> None:
        self.removed_widgets.append(point_uid)


def test_update_points_table_safe_returns_when_points_table_is_missing(capsys):
    owner = type("NoTableOwner", (), {"pointsTable": None})()

    zone_points_actions.update_points_table_safe(owner)

    assert "pointsTable not available" in capsys.readouterr().out


def test_update_points_table_uses_safe_update_when_zone_widget_not_ready():
    owner = _Owner()

    zone_points_actions.update_points_table(owner)

    assert owner.safe_updates == 1


def test_update_points_table_populates_rows_and_restores_signal_state():
    owner = _Owner()
    owner.zonePointsDock = object()

    zone_points_actions.update_points_table(owner)

    assert owner.pointsTable.blocked == [True, False]
    assert owner.pointsTable.row_counts == [2]
    assert len(owner.cleanup_calls) == 1
    assert len(owner.populated) == 1
    assert owner.measurement_widgets == {}
    assert owner._updating_points_table is False


def test_delete_selected_points_skips_measured_and_deletes_pending_rows():
    owner = _Owner()
    owner.pointsTable = _FakePointsTable(rows=[1, 2])

    zone_points_actions.delete_selected_points(owner)

    assert owner.skipped == [(2, "operator-note")]
    assert owner.deleted == [(1, "uid-1", 11)]
    assert owner.updated == 1
    assert owner.logs == ["[CAPTURE] Point #11 deleted from pending plan"]


def test_delete_all_points_clears_lists_widgets_and_resets_ids():
    owner = _DeleteAllOwner()

    zone_points_actions.delete_all_points(owner)

    assert owner.removed_items == ["gp1", "gz1", "up1", "uz1"]
    assert owner.image_view.points_dict["generated"]["points"] == []
    assert owner.image_view.points_dict["generated"]["zones"] == []
    assert owner.image_view.points_dict["user"]["points"] == []
    assert owner.image_view.points_dict["user"]["zones"] == []
    assert owner.removed_widgets == ["uid-1", "uid-2"]
    assert owner.measurement_widgets == {}
    assert owner.next_point_id == 1
