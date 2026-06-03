"""Runtime technical row collection helpers."""

from pathlib import Path
import hashlib
import json

import numpy as np

from . import h5_management_mixin as _module

os = _module.os
logger = _module.logger
QMessageBox = _module.QMessageBox
get_schema = _module.get_schema


class H5LoadingRuntimeRowCollectionMixin:
    def _load_aux_entry_array(self, entry):
        source_ref = str(entry.get("source_ref") or "").strip()
        source_path = str(entry.get("source_path") or "").strip()

        container_path, dataset_path = self._parse_h5ref(source_ref)
        if container_path and dataset_path:
            import h5py

            with h5py.File(container_path, "r") as h5f:
                if dataset_path not in h5f:
                    raise KeyError(f"Dataset not found: {container_path}#{dataset_path}")
                data = h5f[dataset_path][()]
                return np.asarray(data)

        if source_path and os.path.exists(source_path):
            return np.asarray(np.load(source_path))

        if source_ref and os.path.exists(source_ref):
            return np.asarray(np.load(source_ref))

        raise FileNotFoundError(
            f"No readable measurement source for row: {source_path or source_ref}"
        )

    @classmethod
    def _json_safe_runtime_value(cls, value):
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, dict):
            return {
                str(key): cls._json_safe_runtime_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._json_safe_runtime_value(item) for item in value]
        return value

    @classmethod
    def _normalize_runtime_row_for_signature(cls, entry):
        metadata = entry.get("metadata", {}) if isinstance(entry.get("metadata"), dict) else {}
        normalized_metadata = {
            "integration_time_ms": cls._json_safe_runtime_value(
                metadata.get("integration_time_ms")
            ),
            "n_frames": cls._json_safe_runtime_value(metadata.get("n_frames")),
            "thickness": cls._json_safe_runtime_value(metadata.get("thickness")),
        }
        return {
            "alias": cls._json_safe_runtime_value(str(entry.get("alias") or "")),
            "technical_type": cls._json_safe_runtime_value(
                str(entry.get("technical_type") or "")
            ),
            "is_primary": cls._json_safe_runtime_value(bool(entry.get("is_primary"))),
            "source_ref": cls._json_safe_runtime_value(
                str(entry.get("source_ref") or "")
            ),
            "source_path": cls._json_safe_runtime_value(
                str(entry.get("source_path") or "")
            ),
            "row_id": cls._json_safe_runtime_value(str(entry.get("row_id") or "")),
            "metadata": normalized_metadata,
        }

    @classmethod
    def _runtime_rows_signature(cls, runtime_rows):
        payload = [
            cls._normalize_runtime_row_for_signature(entry)
            for entry in (runtime_rows or [])
        ]
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _collect_runtime_rows_from_table(self, show_errors: bool = False):
        runtime_rows = []
        for row in range(self.auxTable.rowCount() if hasattr(self, "auxTable") else 0):
            file_item = self.auxTable.item(row, self.AUX_COL_FILE)
            if file_item is None:
                continue

            source_ref = str(file_item.data(self._aux_metadata_role() - 1) or "").strip()
            source_info = file_item.data(self._aux_source_info_role())
            if not isinstance(source_info, dict):
                source_info = {}
            source_path = str(source_info.get("source_path") or "").strip()

            type_cb = self.auxTable.cellWidget(row, self.AUX_COL_TYPE)
            technical_type = None
            if type_cb is not None and hasattr(type_cb, "currentText"):
                value = type_cb.currentText().strip()
                if value and value != self.NO_SELECTION_LABEL:
                    technical_type = self._normalize_technical_type(value)

            alias_cb = self.auxTable.cellWidget(row, self.AUX_COL_ALIAS)
            alias = None
            if alias_cb is not None and hasattr(alias_cb, "currentText"):
                value = alias_cb.currentText().strip()
                if value and value != self.NO_SELECTION_LABEL:
                    alias = value

            primary_widget = self.auxTable.cellWidget(row, self.AUX_COL_PRIMARY)
            is_primary = False
            if primary_widget is not None:
                try:
                    from difra.gui.qt_compat import QCheckBox

                    checkbox = primary_widget.findChild(QCheckBox)
                except (ImportError, AttributeError, RuntimeError, TypeError):
                    checkbox = None
                if checkbox is not None:
                    is_primary = bool(checkbox.isChecked())

            metadata = self._get_aux_row_metadata(row, source_path or source_ref)

            try:
                data = self._load_aux_entry_array(
                    {
                        "source_ref": source_ref,
                        "source_path": source_path,
                    }
                )
            except (FileNotFoundError, KeyError, OSError, TypeError, ValueError) as exc:
                if show_errors:
                    QMessageBox.warning(
                        self,
                        "Technical Sync",
                        f"Skipping row {row + 1}: {exc}",
                    )
                continue

            runtime_rows.append(
                {
                    "index": row,
                    "alias": alias,
                    "technical_type": technical_type,
                    "is_primary": is_primary,
                    "data": data,
                    "source_ref": source_ref,
                    "source_path": source_path,
                    "row_id": str(source_info.get("row_id") or ""),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                }
            )
        return runtime_rows

    def _collect_runtime_rows_from_container(self, container_path):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        h5_path = str(container_path)

        with h5py.File(h5_path, "r") as h5f:
            original_runtime_rows = self._extract_rows_from_runtime_group(
                h5f,
                schema,
                h5_path,
            )
            canonical_rows = []
            if original_runtime_rows:
                canonical_rows = self._extract_rows_from_canonical_group(
                    h5f, schema, h5_path
                )
                if self._should_prefer_canonical_rows(
                    original_runtime_rows, canonical_rows
                ):
                    rows = canonical_rows
                else:
                    rows = self._backfill_runtime_rows_from_canonical(
                        original_runtime_rows,
                        canonical_rows,
                    )
            else:
                rows = self._extract_rows_from_canonical_group(h5f, schema, h5_path)

        normalized_rows = []
        for idx, row in enumerate(rows):
            source_kind = str(row.get("source_kind") or "").strip().lower()
            source_path = str(row.get("source_path") or "").strip()
            source_container = str(row.get("source_container") or h5_path).strip()
            source_dataset = str(row.get("source_dataset") or "").strip()
            source_ref = ""
            if source_kind == "container" and source_container and source_dataset:
                source_ref = f"h5ref://{source_container}#{source_dataset}"
            elif source_path:
                source_ref = source_path

            try:
                data = self._load_aux_entry_array(
                    {
                        "source_ref": source_ref,
                        "source_path": source_path,
                    }
                )
            except (FileNotFoundError, KeyError, OSError, TypeError, ValueError):
                logger.warning(
                    "Skipping unreadable runtime row while collecting container-backed rows: %s",
                    row,
                    exc_info=True,
                )
                continue

            metadata = row.get("capture_metadata", {})
            normalized_rows.append(
                {
                    "index": idx,
                    "alias": row.get("alias"),
                    "technical_type": row.get("technical_type"),
                    "is_primary": bool(row.get("is_primary")),
                    "data": data,
                    "source_ref": source_ref,
                    "source_path": source_path,
                    "row_id": str(row.get("source_row_id") or f"row_{idx + 1:06d}"),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                }
            )
        return normalized_rows
