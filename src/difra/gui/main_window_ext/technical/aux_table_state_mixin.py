import logging


def _tm():
    from difra.gui.main_window_ext.technical import aux_table_mixin

    return aux_table_mixin._tm()


logger = logging.getLogger(__name__)


class TechnicalAuxTableStateMixin:
    def build_aux_state(self):
        tm = _tm()
        rows = []
        try:
            if not hasattr(self, "auxTable") or self.auxTable is None:
                return rows
            for r in range(self.auxTable.rowCount()):
                file_item = self.auxTable.item(r, self.AUX_COL_FILE)
                file_path = file_item.data(tm.Qt.UserRole) if file_item is not None else None
                source_info = (
                    file_item.data(self._aux_source_info_role())
                    if file_item is not None
                    else {}
                )

                is_primary = False
                primary_widget = self.auxTable.cellWidget(r, self.AUX_COL_PRIMARY)
                try:
                    if primary_widget is not None:
                        primary_checkbox = primary_widget.findChild(tm.QCheckBox)
                        if primary_checkbox is not None:
                            is_primary = bool(primary_checkbox.isChecked())
                except (AttributeError, RuntimeError, TypeError):
                    logger.debug(
                        "Suppressed exception in aux_table_mixin.py",
                        exc_info=True,
                    )

                type_cb = self.auxTable.cellWidget(r, self.AUX_COL_TYPE)
                type_text = None
                try:
                    if type_cb is not None:
                        t = type_cb.currentText()
                        if t and t != self.NO_SELECTION_LABEL:
                            type_text = t
                except (AttributeError, RuntimeError, TypeError):
                    logger.debug(
                        "Suppressed exception in aux_table_mixin.py",
                        exc_info=True,
                    )

                alias_cb = self.auxTable.cellWidget(r, self.AUX_COL_ALIAS)
                alias_text = None
                try:
                    if alias_cb is not None:
                        a = alias_cb.currentText()
                        if a and a != self.NO_SELECTION_LABEL:
                            alias_text = a
                except (AttributeError, RuntimeError, TypeError):
                    logger.debug(
                        "Suppressed exception in aux_table_mixin.py",
                        exc_info=True,
                    )
                rows.append(
                    {
                        "file_path": file_path,
                        "type": type_text,
                        "alias": alias_text,
                        "is_primary": is_primary,
                        "capture_metadata": self._get_aux_row_metadata(r, str(file_path or "")),
                        "source_info": source_info if isinstance(source_info, dict) else {},
                    }
                )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            logger.warning("Error building aux state: %s", e, exc_info=True)
        return rows

    def restore_technical_aux_rows(self, rows):
        tm = _tm()
        try:
            if not hasattr(self, "auxTable") or self.auxTable is None:
                return
            self._restoring_aux_table = True
            self.auxTable.setRowCount(0)
            for row in rows or []:
                fpath = row.get("file_path")
                alias = row.get("alias") or self._infer_alias_from_filename(fpath or "")
                source_info = row.get("source_info") if isinstance(row, dict) else {}
                if not isinstance(source_info, dict):
                    source_info = {}
                self._add_aux_item_to_list(
                    alias or "",
                    fpath or "",
                    source_kind=source_info.get("source_kind", "file"),
                    source_container=source_info.get("container_path", ""),
                    source_dataset=source_info.get("dataset_path", ""),
                    technical_type=row.get("type"),
                    is_primary=bool(row.get("is_primary")),
                    source_row_id=source_info.get("row_id", ""),
                    explicit_metadata=row.get("capture_metadata")
                    if isinstance(row.get("capture_metadata"), dict)
                    else None,
                )
                try:
                    rix = self.auxTable.rowCount() - 1
                    type_cb = self.auxTable.cellWidget(rix, self.AUX_COL_TYPE)
                    if type_cb is not None and row.get("type"):
                        idx = type_cb.findText(row["type"]) if hasattr(type_cb, "findText") else -1
                        if idx >= 0:
                            type_cb.setCurrentIndex(idx)

                    if row.get("is_primary"):
                        primary_widget = self.auxTable.cellWidget(rix, self.AUX_COL_PRIMARY)
                        if primary_widget is not None:
                            primary_checkbox = primary_widget.findChild(tm.QCheckBox)
                            if primary_checkbox is not None:
                                primary_checkbox.setChecked(True)
                    capture_metadata = row.get("capture_metadata")
                    if isinstance(capture_metadata, dict):
                        file_item = self.auxTable.item(rix, self.AUX_COL_FILE)
                        if file_item is not None:
                            file_item.setData(self._aux_metadata_role(), capture_metadata)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    logger.debug(
                        "Suppressed exception in aux_table_mixin.py",
                        exc_info=True,
                    )
            self._restoring_aux_table = False
            self._on_aux_row_updated()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            self._restoring_aux_table = False
            logger.warning("Error restoring aux rows: %s", e, exc_info=True)

    def delete_selected_aux_rows(self):
        try:
            if not hasattr(self, "auxTable") or self.auxTable is None:
                return
            sel_model = self.auxTable.selectionModel()
            if not sel_model:
                return
            rows = sorted({ix.row() for ix in sel_model.selectedRows()}, reverse=True)
            if not rows:
                return
            for r in rows:
                try:
                    self.auxTable.removeRow(r)
                except (AttributeError, RuntimeError):
                    logger.debug(
                        "Suppressed exception in aux_table_mixin.py",
                        exc_info=True,
                    )
            self._on_aux_row_updated()
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            logger.warning("Error deleting selected aux rows: %s", e, exc_info=True)

    def eventFilter(self, source, event):
        tm = _tm()
        if source is getattr(self, "auxTable", None) and event.type() == tm.QEvent.KeyPress:
            try:
                if event.key() == tm.Qt.Key_Delete:
                    self.delete_selected_aux_rows()
                    return True
            except (AttributeError, RuntimeError):
                logger.debug(
                    "Suppressed exception in aux_table_mixin.py",
                    exc_info=True,
                )
        return super().eventFilter(source, event)

