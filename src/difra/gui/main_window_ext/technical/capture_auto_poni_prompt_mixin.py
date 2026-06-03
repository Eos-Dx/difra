import logging
from pathlib import Path

from difra.gui.technical.pyfai_calibration_common import pixel_size_um

logger = logging.getLogger(__name__)


def _tm():
    from difra.gui.main_window_ext.technical import capture_auto_poni_config_mixin

    return capture_auto_poni_config_mixin._tm()


class TechnicalCaptureAutoPoniPromptMixin:
    def _confirm_auto_poni_config(self, auto_cfg: dict) -> bool:
        tm = _tm()
        first_visible = auto_cfg.get("first_visible_ring_by_alias", {})
        primary_ring = int(first_visible.get("PRIMARY", 3) or 3)
        secondary_ring = int(first_visible.get("SECONDARY", 5) or 5)
        reply = tm.QMessageBox.question(
            self,
            "Auto PONI",
            "Auto PONI uses first visible AgBh ring indexes from global config.\n\n"
            f"PRIMARY: ring {primary_ring} (rings 1-2 can be hidden by beam stop)\n"
            f"SECONDARY: ring {secondary_ring}\n\n"
            "Config key:\n"
            "auto_poni_calibration.first_visible_ring_by_alias\n\n"
            "Continue automatic PONI generation?",
            tm.QMessageBox.Yes | tm.QMessageBox.No,
            tm.QMessageBox.Yes,
        )
        return reply == tm.QMessageBox.Yes

    def _prompt_auto_poni_settings(self, auto_cfg: dict, aliases):
        defaults = self._auto_poni_default_settings(auto_cfg, aliases)
        sources = {
            str(alias or "").strip().upper(): str(source or "")
            for alias, source in (
                getattr(self, "_pending_auto_poni_sources", {}) or {}
            ).items()
        }
        tm = _tm()
        try:
            from difra.gui.qt_compat import QDialogButtonBox
        except Exception:
            if self._confirm_auto_poni_config(auto_cfg):
                return defaults
            return None

        try:
            dialog = tm.QDialog(self)
            dialog.setWindowTitle("DIFRA Auto PONI setup")
            dialog.setModal(True)
            dialog.resize(980, 620)
            layout = tm.QVBoxLayout(dialog)
            note = tm.QLabel(dialog)
            note.setWordWrap(True)
            container_distance = (
                self._active_technical_container_distance_cm_for_auto_poni()
            )
            if container_distance is None:
                note.setText(
                    "Container distance not found. "
                    "Set distance and visible rings."
                )
            else:
                note.setText(
                    f"Container distance: {float(container_distance):.3f} cm. "
                    "Adjust if needed."
                )
            layout.addWidget(note)

            controls = {}
            energy_row = tm.QHBoxLayout()
            energy_row.addWidget(tm.QLabel("Energy", dialog))
            energy_spin = tm.QDoubleSpinBox(dialog)
            energy_spin.setRange(0.001, 1000.0)
            energy_spin.setDecimals(4)
            energy_spin.setSuffix(" keV")
            energy_spin.setValue(float(defaults.get("energy_kev", 8.04) or 8.04))
            energy_row.addWidget(energy_spin)
            layout.addLayout(energy_row)

            for alias in aliases:
                alias_key = str(alias or "").strip().upper()
                detector_config = self._auto_poni_detector_config_for_alias(alias)
                center_px = self._auto_poni_seed_center_px_from_config(alias, auto_cfg)
                if center_px is None:
                    center_px = self._auto_poni_center_px_for_alias(alias, detector_config)
                if center_px is None:
                    center_px = (128.0, 128.0)
                width, height = 256, 256
                size_cfg = detector_config.get("size", {})
                if isinstance(size_cfg, dict):
                    width = int(size_cfg.get("width", width) or width)
                    height = int(size_cfg.get("height", height) or height)
                pixel1, pixel2 = pixel_size_um(detector_config)

                group = tm.QGroupBox(alias_key, dialog)
                form = tm.QFormLayout(group)

                file_row = tm.QHBoxLayout()
                file_edit = tm.QLineEdit(sources.get(alias_key, ""), group)
                browse_btn = tm.QPushButton("Browse", group)
                file_row.addWidget(file_edit)
                file_row.addWidget(browse_btn)
                form.addRow("AGBH file", file_row)

                distance_spin = tm.QDoubleSpinBox(dialog)
                distance_spin.setRange(0.01, 100000.0)
                distance_spin.setDecimals(3)
                distance_spin.setSuffix(" cm")
                distance_spin.setValue(
                    float(
                        defaults["distance_cm_by_alias"].get(
                            alias_key,
                            17.0,
                        )
                    )
                )
                form.addRow("Distance", distance_spin)

                ring_spin = tm.QSpinBox(dialog)
                ring_spin.setRange(1, 99)
                ring_spin.setValue(
                    int(
                        defaults["first_visible_ring_by_alias"].get(
                            alias_key,
                            1,
                        )
                    )
                )
                form.addRow("First visible ring", ring_spin)

                rings_spin = tm.QSpinBox(dialog)
                rings_spin.setRange(1, 99)
                rings_spin.setValue(
                    int(
                        defaults["rings_to_search_by_alias"].get(
                            alias_key,
                            3,
                        )
                    )
                )
                form.addRow("Rings to search", rings_spin)

                q_hint = tm.QLabel(
                    self._auto_poni_agbh_q_range_text(
                        ring_spin.value(),
                        rings_spin.value(),
                    ),
                    group,
                )
                form.addRow("Hint", q_hint)

                center_row = tm.QHBoxLayout()
                center_r = tm.QDoubleSpinBox(dialog)
                center_r.setRange(-100000.0, 100000.0)
                center_r.setDecimals(3)
                center_r.setValue(float(center_px[0]))
                center_c = tm.QDoubleSpinBox(dialog)
                center_c.setRange(-100000.0, 100000.0)
                center_c.setDecimals(3)
                center_c.setValue(float(center_px[1]))
                center_row.addWidget(tm.QLabel("row", dialog))
                center_row.addWidget(center_r)
                center_row.addWidget(tm.QLabel("col", dialog))
                center_row.addWidget(center_c)
                form.addRow("Expected center", center_row)

                size_row = tm.QHBoxLayout()
                width_spin = tm.QSpinBox(dialog)
                width_spin.setRange(1, 100000)
                width_spin.setValue(int(width))
                height_spin = tm.QSpinBox(dialog)
                height_spin.setRange(1, 100000)
                height_spin.setValue(int(height))
                size_row.addWidget(tm.QLabel("w", dialog))
                size_row.addWidget(width_spin)
                size_row.addWidget(tm.QLabel("h", dialog))
                size_row.addWidget(height_spin)
                form.addRow("Image/detector size", size_row)

                pixel_row = tm.QHBoxLayout()
                pixel1_spin = tm.QDoubleSpinBox(dialog)
                pixel1_spin.setRange(0.001, 100000.0)
                pixel1_spin.setDecimals(3)
                pixel1_spin.setSuffix(" um")
                pixel1_spin.setValue(float(pixel1))
                pixel2_spin = tm.QDoubleSpinBox(dialog)
                pixel2_spin.setRange(0.001, 100000.0)
                pixel2_spin.setDecimals(3)
                pixel2_spin.setSuffix(" um")
                pixel2_spin.setValue(float(pixel2))
                pixel_row.addWidget(tm.QLabel("p1", dialog))
                pixel_row.addWidget(pixel1_spin)
                pixel_row.addWidget(tm.QLabel("p2", dialog))
                pixel_row.addWidget(pixel2_spin)
                form.addRow("Pixel size", pixel_row)

                layout.addWidget(group)
                controls[alias_key] = {
                    "file": file_edit,
                    "distance": distance_spin,
                    "ring": ring_spin,
                    "rings": rings_spin,
                    "q_hint": q_hint,
                    "center_r": center_r,
                    "center_c": center_c,
                    "width": width_spin,
                    "height": height_spin,
                    "pixel1": pixel1_spin,
                    "pixel2": pixel2_spin,
                    "detector_config": detector_config,
                }

                def _browse(_checked=False, *, edit=file_edit):
                    current = str(edit.text() or "").strip()
                    start = str(Path(current).parent) if current else str(Path.cwd())
                    path, _ = tm.QFileDialog.getOpenFileName(
                        dialog,
                        "Select AGBH file",
                        start,
                        "Images (*.npy *.tif *.tiff *.txt *.csv);;All (*)",
                    )
                    if path:
                        edit.setText(path)

                browse_btn.clicked.connect(_browse)

                def _sync_defaults(
                    _value,
                    *,
                    key=alias_key,
                    ring_control=ring_spin,
                    rings_control=rings_spin,
                ):
                    ring_control.setValue(
                        self._auto_poni_default_first_visible_ring(
                            alias=key,
                            distance_cm=controls[key]["distance"].value(),
                            auto_cfg=auto_cfg,
                        )
                    )
                    rings_control.setValue(
                        self._auto_poni_default_rings_to_search(
                            alias=key,
                            distance_cm=controls[key]["distance"].value(),
                            auto_cfg=auto_cfg,
                        )
                    )
                    controls[key]["q_hint"].setText(
                        self._auto_poni_agbh_q_range_text(
                            ring_control.value(),
                            rings_control.value(),
                        )
                    )

                distance_spin.valueChanged.connect(_sync_defaults)

                def _sync_q_hint(
                    _value,
                    *,
                    key=alias_key,
                ):
                    controls[key]["q_hint"].setText(
                        self._auto_poni_agbh_q_range_text(
                            controls[key]["ring"].value(),
                            controls[key]["rings"].value(),
                        )
                    )

                ring_spin.valueChanged.connect(_sync_q_hint)
                rings_spin.valueChanged.connect(_sync_q_hint)

            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                dialog,
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)

            result = dialog.exec_()
            if result != tm.QDialog.Accepted:
                return None

            distance_by_alias = {}
            first_visible = {}
            rings_to_search = {}
            sources_by_alias = {}
            detector_config_by_alias = {}
            center_px_by_alias = {}
            for alias_key, control in controls.items():
                distance_by_alias[alias_key] = float(control["distance"].value())
                first_visible[alias_key] = int(control["ring"].value())
                rings_to_search[alias_key] = int(control["rings"].value())
                sources_by_alias[alias_key] = str(control["file"].text() or "").strip()
                detector_config = dict(control["detector_config"])
                detector_config["alias"] = alias_key
                detector_config["size"] = {
                    "width": int(control["width"].value()),
                    "height": int(control["height"].value()),
                }
                detector_config["pixel_size_um"] = [
                    float(control["pixel1"].value()),
                    float(control["pixel2"].value()),
                ]
                detector_config_by_alias[alias_key] = detector_config
                center_px_by_alias[alias_key] = (
                    float(control["center_r"].value()),
                    float(control["center_c"].value()),
                )
            return {
                "sources_by_alias": sources_by_alias,
                "distance_cm_by_alias": distance_by_alias,
                "first_visible_ring_by_alias": first_visible,
                "rings_to_search_by_alias": rings_to_search,
                "detector_config_by_alias": detector_config_by_alias,
                "center_px_by_alias": center_px_by_alias,
                "energy_kev": float(energy_spin.value()),
            }
        except Exception:
            logger.warning("Failed to show Auto PONI settings dialog", exc_info=True)
            if self._confirm_auto_poni_config(auto_cfg):
                return defaults
            return None
