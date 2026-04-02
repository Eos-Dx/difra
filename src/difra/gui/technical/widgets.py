import logging
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class DetectorProfilePreview(QWidget):
    """Compact line-profile preview widget."""

    def __init__(self, line_color: str, parent=None):
        super().__init__(parent)
        self._line_color = QColor(line_color)
        self._profile = []
        self._has_profile = False
        self.setFixedSize(self._cm_to_px(2.0, axis="x"), self._cm_to_px(1.5, axis="y"))

    def _cm_to_px(self, cm: float, axis: str = "x") -> int:
        dpi = float(self.logicalDpiX() if axis == "x" else self.logicalDpiY())
        if dpi <= 0:
            dpi = 96.0
        return max(52, int(round((float(cm) / 2.54) * dpi)))

    def clear_profile(self):
        self._profile = []
        self._has_profile = False
        self.update()

    @staticmethod
    def _normalize_profile_log(values):
        import numpy as np

        arr = np.asarray(values, dtype=float).ravel()
        if arr.size < 2:
            return []

        finite = np.isfinite(arr)
        if not np.any(finite):
            return []
        arr = arr[finite]
        if arr.size < 2:
            return []

        sample_size = min(int(arr.size), 240)
        if arr.size > sample_size:
            idx = np.linspace(0, arr.size - 1, sample_size).astype(int)
            arr = arr[idx]

        positive = arr[arr > 0]
        if positive.size == 0:
            return []

        floor = float(positive.min())
        arr = np.maximum(arr, floor)
        log_arr = np.log10(arr)

        min_v = float(log_arr.min())
        max_v = float(log_arr.max())
        span = max_v - min_v
        if span <= 1e-12:
            normalized = np.full(log_arr.shape, 0.5, dtype=float)
        else:
            normalized = (log_arr - min_v) / span

        return normalized.tolist()

    def set_profile(self, values):
        try:
            normalized = self._normalize_profile_log(values)
            if len(normalized) < 2:
                self.clear_profile()
                return

            self._profile = normalized
            self._has_profile = True
            self.update()
        except Exception:
            self.clear_profile()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(rect, QColor("#f6f7f9"))
        painter.setPen(QPen(QColor("#c8ced6"), 1.0))
        painter.drawRect(rect)

        left = rect.left() + 4
        right = rect.right() - 4
        top = rect.top() + 4
        bottom = rect.bottom() - 4
        mid_y = (top + bottom) / 2.0

        baseline_pen = QPen(QColor("#d4dae3"), 1.0, Qt.DashLine)
        painter.setPen(baseline_pen)
        painter.drawLine(left, int(round(mid_y)), right, int(round(mid_y)))

        if not self._has_profile or len(self._profile) < 2:
            painter.end()
            return

        line_pen = QPen(self._line_color, 1.6)
        painter.setPen(line_pen)
        path = QPainterPath()
        width = max(float(right - left), 1.0)
        height = max(float(bottom - top), 1.0)

        for i, value in enumerate(self._profile):
            x = left + (i / float(len(self._profile) - 1)) * width
            y = bottom - float(value) * height
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.drawPath(path)
        painter.end()


class MeasurementHistoryWidget(QWidget):
    def __init__(self, masks, ponis, parent=None, point_id=None):
        super().__init__(parent)
        self.measurements = []
        self.masks = masks if isinstance(masks, dict) else {}
        self.ponis = ponis if isinstance(ponis, dict) else {}
        self.point_id = point_id  # Store the ID
        self.parent_window = parent
        self.x_mm = None
        self.y_mm = None
        self.layout = QVBoxLayout(self)
        try:
            # Set window title to include point_id and, if available, X:Y (mm)
            self._update_title_with_coordinates()
            self.summary_btn = QPushButton("No measurements")
            self.summary_btn.clicked.connect(self.show_history_dialog)
            self.layout.addWidget(self.summary_btn)
            self._create_detector_profile_previews()
            self.layout.setContentsMargins(0, 0, 0, 0)
            self.setLayout(self.layout)
            self.update_summary()
        except Exception as e:
            logging.getLogger(__name__).exception(
                "Error initializing MeasurementHistoryWidget: %s", e
            )

    def _create_detector_profile_previews(self):
        self.detector_profile_previews = {}
        for alias, color in (("PRIMARY", "#2b7a78"), ("SECONDARY", "#cc5c3c")):
            row = QHBoxLayout()
            row.setSpacing(4)
            label = QLabel(f"{alias} profile:")
            label.setStyleSheet("color: #444; font-size: 10px;")
            label.setFixedWidth(92)
            preview = DetectorProfilePreview(color, self)
            row.addWidget(label)
            row.addWidget(preview)
            row.addStretch(1)
            self.layout.addLayout(row)
            self.detector_profile_previews[alias] = preview

    @staticmethod
    def _normalize_profile_alias(alias: str) -> str:
        key = str(alias or "").strip().upper()
        if key == "SAXS":
            return "PRIMARY"
        if key == "WAXS":
            return "SECONDARY"
        return key

    @staticmethod
    def _as_text(value):
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except Exception:
                return value.decode("latin-1", errors="replace")
        return str(value or "")

    @classmethod
    def _read_poni_dataset_text(cls, dataset) -> str | None:
        try:
            value = dataset[()]
        except Exception:
            return None
        text = cls._as_text(value).strip()
        return text or None

    @classmethod
    def _resolve_poni_text_from_h5ref(cls, measurement_filename: str, alias: str | None = None) -> str | None:
        value = str(measurement_filename or "").strip()
        if not value.startswith("h5ref://"):
            return None

        try:
            import h5py
        except Exception:
            logging.getLogger(__name__).debug(
                "h5py unavailable while resolving PONI from H5 ref",
                exc_info=True,
            )
            return None

        payload = value[len("h5ref://") :]
        container_path, sep, dataset_path = payload.partition("#")
        if not sep or not container_path or not dataset_path:
            return None

        container = Path(container_path)
        if not container.exists():
            return None

        try:
            with h5py.File(container, "r") as h5f:
                if dataset_path not in h5f:
                    return None
                dataset = h5f[dataset_path]
                detector_group = dataset.parent

                for attr_name in ("poni_ref", "poni_path"):
                    ref_path = cls._as_text(detector_group.attrs.get(attr_name, "")).strip()
                    if ref_path and ref_path in h5f:
                        text = cls._read_poni_dataset_text(h5f[ref_path])
                        if text:
                            return text

                alias_candidates = set()
                for candidate in (
                    alias,
                    detector_group.attrs.get("detector_alias"),
                    detector_group.attrs.get("detector_id"),
                    detector_group.name.rsplit("/", 1)[-1],
                ):
                    text = cls._as_text(candidate).strip().upper()
                    if text:
                        alias_candidates.add(text)
                        if text.startswith("DET_"):
                            alias_candidates.add(text.replace("DET_", "", 1))

                poni_group = h5f.get("/technical/poni")
                if poni_group is not None:
                    for _, poni_dataset in poni_group.items():
                        detector_alias = cls._as_text(
                            getattr(poni_dataset, "attrs", {}).get("detector_alias", "")
                        ).strip().upper()
                        detector_id = cls._as_text(
                            getattr(poni_dataset, "attrs", {}).get("detector_id", "")
                        ).strip().upper()
                        if (
                            detector_alias and detector_alias in alias_candidates
                        ) or (
                            detector_id and detector_id in alias_candidates
                        ):
                            text = cls._read_poni_dataset_text(poni_dataset)
                            if text:
                                return text
        except Exception:
            logging.getLogger(__name__).debug(
                "Failed to resolve PONI text from measurement H5 ref '%s'",
                value,
                exc_info=True,
            )
            return None
        return None

    def _resolve_poni_text_for_result(self, alias: str, result: dict) -> str | None:
        result_map = result or {}
        explicit = str(result_map.get("poni_text") or "").strip()
        if explicit:
            return explicit

        filename = str(result_map.get("filename") or "").strip()
        return self._resolve_poni_text_from_h5ref(filename, alias=alias)

    def set_detector_profile(self, alias: str, profile_values):
        preview_map = getattr(self, "detector_profile_previews", {}) or {}
        key = self._normalize_profile_alias(alias)
        preview = preview_map.get(key)
        if preview is None:
            return
        preview.set_profile(profile_values)

    def clear_detector_profiles(self):
        preview_map = getattr(self, "detector_profile_previews", {}) or {}
        for preview in preview_map.values():
            try:
                preview.clear_profile()
            except Exception:
                pass

    def add_measurement(self, results, timestamp):
        try:
            # results: {alias: {'filename':..., 'goodness':...}}
            self.measurements.append({"timestamp": timestamp, "results": results or {}})
            self.update_summary()
        except Exception as e:
            logging.getLogger(__name__).exception("Error adding measurement: %s", e)

    def set_mm_coordinates(self, x_mm: float, y_mm: float):
        """Optionally set coordinates (in mm) and refresh the title."""
        try:
            self.x_mm = x_mm
            self.y_mm = y_mm
            self._update_title_with_coordinates()
        except Exception:
            pass

    def _update_title_with_coordinates(self):
        """Set window title to include point_id and, if known, X:Y in mm.
        Attempts to read coordinates from parent.pointsTable if not already set.
        """
        try:
            title_base = "Measurement History"
            if self.point_id is not None:
                title_base = f"Measurement History: Point #{self.point_id}"
            x_mm = self.x_mm
            y_mm = self.y_mm
            # Try to derive coordinates from parent table if not set yet
            if (
                (x_mm is None or y_mm is None)
                and getattr(self.parent_window, "pointsTable", None) is not None
                and self.point_id is not None
            ):
                table = self.parent_window.pointsTable
                try:
                    from PyQt5.QtWidgets import QTableWidgetItem  # noqa: F401

                    rows = table.rowCount()
                    for r in range(rows):
                        it = table.item(r, 0)
                        if (
                            it is not None
                            and it.text().strip().isdigit()
                            and int(it.text()) == int(self.point_id)
                        ):
                            itx = table.item(r, 3)
                            ity = table.item(r, 4)
                            if itx is not None and ity is not None:
                                try:
                                    x_mm = (
                                        float(itx.text())
                                        if itx.text() not in (None, "", "N/A")
                                        else None
                                    )
                                    y_mm = (
                                        float(ity.text())
                                        if ity.text() not in (None, "", "N/A")
                                        else None
                                    )
                                except Exception:
                                    pass
                            break
                except Exception:
                    pass
            if x_mm is not None and y_mm is not None:
                self.setWindowTitle(f"{title_base} {x_mm:.2f}:{y_mm:.2f} mm")
            else:
                self.setWindowTitle(title_base)
        except Exception:
            # Fallback plain title
            if self.point_id is not None:
                self.setWindowTitle(f"Measurement History: Point #{self.point_id}")
            else:
                self.setWindowTitle("Measurement History")

    def update_summary(self):
        try:
            n = len(self.measurements)
            if n == 0:
                self.summary_btn.setText("No measurements")
            else:
                last = self.measurements[-1]
                ts = last.get("timestamp", "-")
                self.summary_btn.setText(f"{n} measurement(s), last: {ts}")
        except Exception as e:
            logging.getLogger(__name__).exception("Error updating summary: %s", e)

    def show_history_dialog(self):
        try:
            # make the dialog a child of the main window (or None), NOT the cell widget
            parent_for_dialog = (
                self.parent_window if getattr(self, "parent_window", None) else None
            )
            dlg = QDialog(parent_for_dialog)
            # Ensure title reflects current ID and coordinates
            self._update_title_with_coordinates()
            dlg.setWindowTitle(self.windowTitle())
            layout = QVBoxLayout(dlg)
            if not self.measurements:
                return
            all_aliases = sorted(
                {
                    alias
                    for m in self.measurements
                    for alias in (m.get("results") or {}).keys()
                }
            )
            ncols = 1 + 2 * len(all_aliases)
            table = QTableWidget()
            table.setRowCount(len(self.measurements))
            table.setColumnCount(ncols)
            headers = ["Timestamp"]
            for alias in all_aliases:
                headers.append(f"{alias} File")
                headers.append(f"{alias} Goodness")
            table.setHorizontalHeaderLabels(headers)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            for i, m in enumerate(self.measurements):
                table.setItem(i, 0, QTableWidgetItem(str(m.get("timestamp", "-"))))
                res_map = m.get("results") or {}
                for j, alias in enumerate(all_aliases):
                    res = res_map.get(alias, {})
                    filename = res.get("filename", "")
                    goodness = res.get("goodness")
                    table.setItem(i, 1 + 2 * j, QTableWidgetItem(str(filename)))
                    good_str = (
                        f"{goodness:.1f}%"
                        if isinstance(goodness, (int, float))
                        else "-"
                    )
                    table.setItem(i, 2 + 2 * j, QTableWidgetItem(good_str))
            layout.addWidget(table)
            dlg.setLayout(layout)
            dlg.resize(800, 400)

            def cell_double_clicked(row, col):
                try:
                    if col == 0 or (col - 1) % 2 != 0:
                        return
                    alias_idx = (col - 1) // 2
                    try:
                        alias = all_aliases[alias_idx]
                    except IndexError:
                        logging.getLogger(__name__).exception(
                            "Alias index out of range for col=%s", col
                        )
                        return
                    res_map = self.measurements[row].get("results") or {}
                    res = res_map.get(alias, {})
                    filename = res.get("filename")
                    if filename:
                        try:
                            from difra.gui.technical.capture import (
                                show_measurement_window,
                            )

                            show_measurement_window(
                                filename,
                                (self.masks or {}).get(alias),
                                self._resolve_poni_text_for_result(alias, res),
                                self.parent_window,
                            )
                        except Exception as e:
                            logging.getLogger(__name__).exception(
                                "Error opening measurement window: %s", e
                            )
                except Exception as e:
                    logging.getLogger(__name__).exception(
                        "Error handling cell double click: %s", e
                    )

            table.cellDoubleClicked.connect(cell_double_clicked)
            dlg.exec_()
        except Exception as e:
            logging.getLogger(__name__).exception("Error showing history dialog: %s", e)
