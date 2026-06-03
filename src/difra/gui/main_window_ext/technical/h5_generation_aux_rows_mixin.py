"""Aux-table row collection for technical H5 generation."""

from . import h5_generation_mixin as _module

logger = _module.logger
os = _module.os
QComboBox = _module.QComboBox
QMessageBox = _module.QMessageBox
QCheckBox = _module.QCheckBox
Qt = _module.Qt


class H5GenerationAuxRowsMixin:
    def _collect_h5_aux_measurements(
        self,
        *,
        rows,
        schema,
        gui_integration_ms,
        gui_n_frames,
    ):
        aux_measurements = {}
        primary_measurements = {}
        try:
            active_aliases = self._get_active_detector_aliases()
        except Exception:
            active_aliases = []

        for row in rows:
            checkbox_widget = self.auxTable.cellWidget(row, 0)
            is_primary = False
            if checkbox_widget:
                checkbox = checkbox_widget.findChild(QCheckBox)
                if checkbox:
                    is_primary = checkbox.isChecked()

            file_item = self.auxTable.item(row, 1)
            if not file_item:
                continue
            file_path = file_item.data(Qt.UserRole)
            source_info = {}
            try:
                source_info = file_item.data(self._aux_source_info_role())
            except (AttributeError, RuntimeError, TypeError):
                source_info = {}
            if isinstance(source_info, dict):
                original_source_path = str(source_info.get("source_path") or "").strip()
                if str(file_path or "").startswith("h5ref://") and original_source_path:
                    file_path = original_source_path
            if not file_path or not os.path.exists(file_path):
                QMessageBox.warning(
                    self,
                    "Missing File",
                    f"Row {row+1}: file path does not exist.",
                )
                return None

            type_cb = self.auxTable.cellWidget(row, 2)
            if (
                not isinstance(type_cb, QComboBox)
                or type_cb.currentText() == self.NO_SELECTION_LABEL
            ):
                QMessageBox.warning(
                    self,
                    "Missing Type",
                    f"Row {row+1}: select measurement type.",
                )
                return None
            typ_ui = type_cb.currentText()
            typ = self._normalize_technical_type(typ_ui)

            cb = self.auxTable.cellWidget(row, 3)
            if (
                not isinstance(cb, QComboBox)
                or cb.currentText() == self.NO_SELECTION_LABEL
            ):
                QMessageBox.warning(
                    self,
                    "Missing Alias",
                    f"Row {row+1}: select an alias.",
                )
                return None
            alias = cb.currentText()

            if typ not in schema.ALL_TECHNICAL_TYPES:
                QMessageBox.warning(
                    self,
                    "Invalid Type",
                    f"Type '{typ_ui}' is not supported for HDF5.\n"
                    f"Supported: {', '.join(schema.ALL_TECHNICAL_TYPES)}",
                )
                return None

            if typ_ui == "SPECIAL":
                self._log_technical_event("Mapping type SPECIAL -> WATER for HDF5")

            if is_primary:
                entry = {"file_path": file_path}
                try:
                    row_metadata = self._get_aux_row_metadata(
                        row,
                        str(file_path),
                        include_filename_fallback=False,
                    )
                except Exception:
                    row_metadata = {}
                if isinstance(row_metadata, dict):
                    for key, value in row_metadata.items():
                        if value is not None:
                            entry[key] = value
                if entry.get("integration_time_ms") is None and gui_integration_ms is not None:
                    entry["integration_time_ms"] = gui_integration_ms
                if entry.get("n_frames") is None and gui_n_frames is not None:
                    entry["n_frames"] = gui_n_frames
                aux_measurements.setdefault(typ, {})[alias] = entry

            pair = (typ, alias)
            if pair not in primary_measurements:
                primary_measurements[pair] = []
            primary_measurements[pair].append(is_primary)

            self._log_technical_event(
                f"Row {row+1}: {typ_ui} for {alias} - "
                f"{'PRIMARY' if is_primary else 'supplementary'}"
            )

        return {
            "aux_measurements": aux_measurements,
            "primary_measurements": primary_measurements,
            "active_aliases": active_aliases,
        }
