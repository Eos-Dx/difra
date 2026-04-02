"""State saver I/O and serialization responsibilities."""

import logging

from . import state_saver_extension as _module

base64 = _module.base64
hashlib = _module.hashlib
json = _module.json
os = _module.os
shutil = _module.shutil
string = _module.string
Path = _module.Path
unquote = _module.unquote
urlparse = _module.urlparse

QRectF = _module.QRectF
QTimer = _module.QTimer
QColor = _module.QColor
QPen = _module.QPen
QPixmap = _module.QPixmap
QGraphicsEllipseItem = _module.QGraphicsEllipseItem
QGraphicsRectItem = _module.QGraphicsRectItem

null_dict = _module.null_dict

logger = logging.getLogger(__name__)


class StateSaverIOMixin:
    @staticmethod
    def _get_autosave_drive():
        drives = [
            f"{d}:/"
            for d in string.ascii_uppercase
            if os.path.exists(f"{d}:/") and d.lower() not in ("a", "b")
        ]
        return (
            Path(
                drives[
                    (1 if len(drives) > 1 and drives[0].lower().startswith("c") else 0)
                ]
            )
            if drives
            else Path.cwd()
        )

    _AUTOSAVE_DRIVE = _get_autosave_drive.__func__()
    AUTO_STATE_FILE = _AUTOSAVE_DRIVE / "autosave_state.json"
    PREV_STATE_FILE = _AUTOSAVE_DRIVE / "autosave_state_prev.json"

    def auto_save_state(self):
        # Autosave is container-first: keep runtime state in session container, not sidecar files.
        self._save_state(target_file=None, is_auto=True)

    def setup_auto_save(self, interval=2000):
        self.autoSaveTimer = QTimer(self)
        self.autoSaveTimer.timeout.connect(self.auto_save_state)
        self.autoSaveTimer.start(interval)

    def _save_state(self, target_file, is_auto):
        img_path = getattr(self.image_view, "current_image_path", None)
        try:
            if img_path:
                # Persist absolute normalized path for robustness
                img_path = str(Path(img_path).resolve())
        except Exception:
            logger.debug(
                "Suppressed exception in state_saver_io_mixin.py",
                exc_info=True,
            )
        state = {
            "measurement_points": self.generate_measurement_points(),
            "image": img_path,
            "rotation_angle": getattr(self.image_view, "rotation_angle", 0),
            "crop_rect": self._get_crop_rect(),
            "shapes": self._get_shapes(),
            "zone_points": self._get_zone_points(),
            "dock_geometry": self._get_dock_geometry(),
        }
        if not is_auto:
            rx = getattr(self, "real_x_pos_mm", None)
            ry = getattr(self, "real_y_pos_mm", None)
            state["real_center"] = (
                rx.value() if rx is not None else None,
                ry.value() if ry is not None else None,
            )
            state["pixel_to_mm_ratio"] = getattr(self, "pixel_to_mm_ratio", 1)

        # --- Include active detectors and their PONI meta in the state ---
        try:
            active_aliases = self.hardware_controller.active_detector_aliases
        except Exception:
            # Fallback based on config
            dev_mode = self.config.get("DEV", False)
            active_ids = (
                self.config.get("dev_active_detectors", [])
                if dev_mode
                else self.config.get("active_detectors", [])
            )
            aliases = []
            for det_cfg in self.config.get("detectors", []):
                if det_cfg.get("id") in active_ids:
                    aliases.append(det_cfg.get("alias"))
            active_aliases = aliases

        state["active_detectors_aliases"] = active_aliases

        ponis = getattr(self, "ponis", {}) or {}
        # Try to get current settings from UI if available
        try:
            current_settings = self.get_current_poni_settings()
        except Exception:
            current_settings = {}

        det_info = {}
        for alias in active_aliases:
            # Prefer current UI settings, fallback to stored settings
            if alias in current_settings:
                settings = current_settings[alias]
                det_info[alias] = {
                    "poni_filename": settings.get("name"),
                    "poni_path": settings.get("path"),
                    "poni_value": settings.get("value", ""),
                }
            else:
                # Fallback to stored poni_files data
                poni_files = getattr(self, "poni_files", {}) or {}
                meta = poni_files.get(alias, {})
                pth = meta.get("path")
                det_info[alias] = {
                    "poni_filename": meta.get("name"),
                    "poni_path": str(pth) if pth is not None else None,
                    "poni_value": ponis.get(alias, ""),
                }
        state["detector_poni"] = det_info

        # Include technical aux table rows if available
        try:
            if hasattr(self, "build_aux_state"):
                state["technical_aux"] = self.build_aux_state()
        except Exception as exc:
            logger.warning("Failed to collect technical aux rows: %s", exc, exc_info=True)

        self.state = state
        if target_file:
            if is_auto and os.path.exists(self.AUTO_STATE_FILE):
                try:
                    shutil.copyfile(self.AUTO_STATE_FILE, self.PREV_STATE_FILE)
                except Exception as exc:
                    logger.warning("Failed to copy autosave file: %s", exc, exc_info=True)
            try:
                with open(target_file, "w") as f:
                    json.dump(state, f, indent=4)
            except Exception as exc:
                logger.error("Failed to save state file %s: %s", target_file, exc, exc_info=True)

        # Keep active unlocked session containers in sync with latest workspace state
        # so crash recovery can restore from container content.
        try:
            if hasattr(self, "sync_workspace_to_session_container"):
                self.sync_workspace_to_session_container(state=state)
        except Exception as exc:
            logger.warning(
                "Failed to sync workspace to session container: %s",
                exc,
                exc_info=True,
            )

    def _get_crop_rect(self):
        r = getattr(self.image_view, "crop_rect", None)
        return (
            {"x": r.x(), "y": r.y(), "width": r.width(), "height": r.height()}
            if r
            else None
        )

    def _get_shapes(self):
        result = []
        for s in getattr(self.image_view, "shapes", []):
            item = s.get("item")
            if item:
                rect = item.sceneBoundingRect()
                result.append(
                    {
                        "id": s.get("id"),
                        "uid": s.get("uid"),
                        "type": s.get("type"),
                        "role": s.get("role", "include"),
                        "isNew": bool(s.get("isNew", False)),
                        "locked_after_measurements": bool(
                            s.get("locked_after_measurements", False)
                        ),
                        "physical_size_mm": s.get("physical_size_mm"),
                        "center_px": list(s.get("center_px")) if s.get("center_px") else None,
                        "geometry": {
                            "x": rect.x(),
                            "y": rect.y(),
                            "width": rect.width(),
                            "height": rect.height(),
                        },
                    }
                )
        return result

    def _get_zone_points(self):
        out = []
        for t in ("generated", "user"):
            points = self.image_view.points_dict[t]["points"]
            zones = self.image_view.points_dict[t]["zones"]
            for idx, pt in enumerate(points):
                center = pt.sceneBoundingRect().center()
                # Get corresponding zone radius if available
                radius = None
                try:
                    if idx < len(zones):
                        zone = zones[idx]
                        # Try to get radius from zone's data (key 99)
                        radius = zone.data(99)
                        # Fallback: calculate from zone geometry
                        if radius is None:
                            rect = zone.rect()
                            radius = rect.width() / 2.0
                except Exception:
                    logger.debug(
                        "Suppressed exception in state_saver_io_mixin.py",
                        exc_info=True,
                    )
                out.append(
                    {
                        "x": center.x(),
                        "y": center.y(),
                        "type": t,
                        "id": pt.data(1),
                        "uid": pt.data(2),
                        "radius": radius,
                    }
                )
        return out

    def _get_dock_geometry(self):
        """Save dock widget layout and sizes."""
        try:
            # Save QMainWindow state (includes all dock positions and sizes)
            return {
                "window_geometry": self.saveGeometry().toBase64().data().decode('ascii'),
                "window_state": self.saveState().toBase64().data().decode('ascii'),
            }
        except Exception as exc:
            logger.warning("Failed to save dock geometry: %s", exc, exc_info=True)
            return None

    def _restore_dock_geometry(self, dock_geometry):
        """Restore dock widget layout and sizes."""
        if not dock_geometry:
            return
        try:
            from PyQt5.QtCore import QByteArray
            # Restore window geometry (size and position)
            if "window_geometry" in dock_geometry:
                geom_bytes = dock_geometry["window_geometry"].encode('ascii')
                self.restoreGeometry(QByteArray.fromBase64(geom_bytes))
            # Restore dock widget state (positions and sizes)
            if "window_state" in dock_geometry:
                state_bytes = dock_geometry["window_state"].encode('ascii')
                self.restoreState(QByteArray.fromBase64(state_bytes))
        except Exception as exc:
            logger.warning("Failed to restore dock geometry: %s", exc, exc_info=True)
