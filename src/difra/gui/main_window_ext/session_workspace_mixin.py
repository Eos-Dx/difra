"""Workspace/session container synchronization helpers for SessionMixin."""

from . import session_mixin as _session_module

json = _session_module.json
Path = _session_module.Path
get_schema = _session_module.get_schema
get_writer = _session_module.get_writer
logger = _session_module.logger

from difra.gui.main_window_ext import session_workspace_restore


class SessionWorkspaceMixin:
    SAMPLE_PHOTO_DEFAULT_LOAD_POSITION_MM = (-13.9, -6.0)
    SAMPLE_PHOTO_DEFAULT_BEAM_CENTER_MM = (6.15, -9.15)
    SAMPLE_PHOTO_ROTATED_IMAGE_TYPE = "sample_rotated"

    @staticmethod
    def _normalize_workspace_role(role) -> str:
        return str(role or "include").strip().lower()

    def _plan_session_zone_exports(self, shapes):
        exports = []
        first_include_export_idx = None
        has_explicit_sample_holder = False
        holder_circle_role = str(
            getattr(self, "ROLE_HOLDER_CIRCLE", "holder circle")
        ).strip().lower()
        calibration_square_role = str(
            getattr(self, "ROLE_CALIBRATION_SQUARE", "calibration square")
        ).strip().lower()

        for shape in list(shapes or []):
            role = self._normalize_workspace_role(shape.get("role", "include"))
            if role in ("sample holder", holder_circle_role, calibration_square_role):
                zone_role = "sample_holder"
                has_explicit_sample_holder = True
            elif role == "exclude":
                zone_role = "exclude"
            else:
                zone_role = "include"
                if first_include_export_idx is None:
                    first_include_export_idx = len(exports)

            exports.append({"shape": shape, "zone_role": zone_role})

        if not has_explicit_sample_holder and first_include_export_idx is not None:
            exports[first_include_export_idx]["zone_role"] = "sample_holder"

        sample_holder_zone_id = None
        for zone_index, export in enumerate(exports, start=1):
            export["zone_index"] = zone_index
            if sample_holder_zone_id is None and export["zone_role"] == "sample_holder":
                sample_holder_zone_id = f"zone_{zone_index:03d}"

        return exports, sample_holder_zone_id

    @staticmethod
    def _normalize_xy_pair(value):
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return (float(value[0]), float(value[1]))
            except Exception as exc:
                logger.debug(
                    "Failed to normalize XY pair %r: %s",
                    value,
                    exc,
                    exc_info=True,
                )
                return None
        return None

    def _get_sample_photo_beam_center_mm(self):
        pair = self._normalize_xy_pair(getattr(self, "sample_photo_beam_center_mm", None))
        if pair is not None:
            return (float(pair[0]), float(pair[1]))
        return tuple(self.SAMPLE_PHOTO_DEFAULT_BEAM_CENTER_MM)

    def _get_holder_center_px(self):
        pair = self._normalize_xy_pair(getattr(self, "sample_holder_center_px", None))
        if pair is not None:
            return (float(pair[0]), float(pair[1]))
        return None

    def _use_sample_photo_rotated_mapping(self) -> bool:
        if not bool(getattr(self, "sample_photo_rotation_confirmed", False)):
            return False
        if self._get_holder_center_px() is None:
            return False
        try:
            ratio = float(getattr(self, "pixel_to_mm_ratio", 0.0))
        except Exception:
            ratio = 0.0
        return ratio > 0.0

    def _set_spin_value_if_present(self, attr_name: str, value: float):
        widget = getattr(self, attr_name, None)
        if widget is None:
            return
        setter = getattr(widget, "setValue", None)
        if callable(setter):
            try:
                setter(float(value))
            except Exception as exc:
                logger.debug(
                    "Failed to set %s value=%r: %s",
                    attr_name,
                    value,
                    exc,
                    exc_info=True,
                )

    def _get_stage_reference_mm(self):
        ref_x = 0.0
        ref_y = 0.0

        x_widget = getattr(self, "real_x_pos_mm", None)
        y_widget = getattr(self, "real_y_pos_mm", None)
        x_value = getattr(x_widget, "value", None)
        y_value = getattr(y_widget, "value", None)
        if callable(x_value):
            try:
                ref_x = float(x_value())
            except Exception as exc:
                logger.debug(
                    "Failed to read real_x_pos_mm: %s",
                    exc,
                    exc_info=True,
                )
                ref_x = 0.0
        if callable(y_value):
            try:
                ref_y = float(y_value())
            except Exception as exc:
                logger.debug(
                    "Failed to read real_y_pos_mm: %s",
                    exc,
                    exc_info=True,
                )
                ref_y = 0.0

        return ref_x, ref_y

    def _get_include_center_px(self):
        center = self._normalize_xy_pair(getattr(self, "include_center", None))
        if center is not None:
            return center
        return (0.0, 0.0)

    def _pixel_to_physical_mm(self, x_px: float, y_px: float):
        try:
            ratio = float(getattr(self, "pixel_to_mm_ratio", 0.0))
        except Exception as exc:
            logger.debug(
                "Failed to parse pixel_to_mm_ratio for point conversion: %s",
                exc,
                exc_info=True,
            )
            ratio = 0.0
        if ratio == 0.0:
            return (0.0, 0.0)

        if self._use_sample_photo_rotated_mapping():
            center_x, center_y = self._get_holder_center_px()
            beam_x_mm, beam_y_mm = self._get_sample_photo_beam_center_mm()
            x_mm = beam_x_mm + (float(x_px) - center_x) / ratio
            y_mm = beam_y_mm + (float(y_px) - center_y) / ratio
            return (float(x_mm), float(y_mm))

        center_x, center_y = self._get_include_center_px()
        ref_x_mm, ref_y_mm = self._get_stage_reference_mm()
        x_mm = ref_x_mm - (float(x_px) - center_x) / ratio
        y_mm = ref_y_mm - (float(y_px) - center_y) / ratio
        return (float(x_mm), float(y_mm))

    def _build_mapping_conversion_payload(self):
        try:
            ratio = float(getattr(self, "pixel_to_mm_ratio", 0.0))
        except Exception as exc:
            logger.debug(
                "Failed to parse pixel_to_mm_ratio for mapping payload: %s",
                exc,
                exc_info=True,
            )
            ratio = 0.0
        center_x, center_y = self._get_include_center_px()
        ref_x_mm, ref_y_mm = self._get_stage_reference_mm()

        payload = {
            "ratio": ratio,
            "units": "mm/pixel",
            "include_center_px": [float(center_x), float(center_y)],
            "stage_reference_mm": [float(ref_x_mm), float(ref_y_mm)],
            "formula": "x_mm = ref_x_mm - (x_px - center_x_px) / ratio",
        }
        holder_center = self._get_holder_center_px()
        if holder_center is not None:
            payload["holder_circle_center_px"] = [float(holder_center[0]), float(holder_center[1])]
        payload["rotation_deg"] = int(getattr(self, "sample_photo_rotation_deg", 0) or 0)
        payload["rotation_confirmed"] = bool(getattr(self, "sample_photo_rotation_confirmed", False))
        payload["raw_image_type"] = "sample"
        payload["workspace_image_type"] = (
            self.SAMPLE_PHOTO_ROTATED_IMAGE_TYPE
            if bool(getattr(self, "sample_photo_rotation_confirmed", False))
            else "sample"
        )
        beam_center = self._get_sample_photo_beam_center_mm()
        payload["beam_center_mm"] = [float(beam_center[0]), float(beam_center[1])]
        load_position = self._normalize_xy_pair(getattr(self, "sample_photo_load_position_mm", None))
        if load_position is None:
            load_position = tuple(self.SAMPLE_PHOTO_DEFAULT_LOAD_POSITION_MM)
        payload["load_position_mm"] = [float(load_position[0]), float(load_position[1])]
        if self._use_sample_photo_rotated_mapping():
            payload["formula"] = "x_mm = beam_x_mm + (x_px - holder_center_x_px) / ratio"
        return payload

    def _image_file_signature(self):
        if not hasattr(self, "image_view"):
            return None
        image_path = getattr(self.image_view, "current_image_path", None)
        if not image_path:
            return None

        path = Path(str(image_path))
        if not path.exists():
            return {"path": str(path), "missing": True}

        try:
            stat = path.stat()
            return {
                "path": str(path.resolve()),
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        except Exception as exc:
            logger.debug(
                "Failed to read image signature metadata for %s: %s",
                path,
                exc,
                exc_info=True,
            )
            return {"path": str(path)}

    @staticmethod
    def _sync_signature(payload):
        import hashlib

        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.md5(encoded.encode("utf-8")).hexdigest()

    def _load_image_array_from_path(self, image_path):
        """Load image from disk and normalize color channels to RGB/RGBA."""
        if not image_path:
            return None

        try:
            import cv2

            image_array = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
            if image_array is not None:
                if image_array.ndim == 3:
                    channels = int(image_array.shape[2])
                    if channels == 3:
                        image_array = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
                    elif channels == 4:
                        image_array = cv2.cvtColor(image_array, cv2.COLOR_BGRA2RGBA)
                return image_array
        except Exception as exc:
            logger.debug(
                "OpenCV image load failed for %s, falling back to PIL: %s",
                image_path,
                exc,
                exc_info=True,
            )

        try:
            import numpy as np
            from PIL import Image

            return np.array(Image.open(image_path))
        except Exception as exc:
            logger.warning(f"Failed to load image for session sync: {exc}")
            return None

    def _set_image_from_array(self, image_array):
        """Render numpy image array into image_view when available."""
        if not hasattr(self, "image_view"):
            return False

        try:
            import numpy as np
            from PyQt5.QtGui import QImage, QPixmap

            array = np.asarray(image_array)
            if array.ndim == 2:
                if array.dtype != np.uint8:
                    array = np.clip(array, 0, 255).astype(np.uint8)
                height, width = array.shape
                qimage = QImage(
                    array.data, width, height, array.strides[0], QImage.Format_Grayscale8
                ).copy()
            elif array.ndim == 3 and array.shape[2] in (3, 4):
                if array.dtype != np.uint8:
                    array = np.clip(array, 0, 255).astype(np.uint8)
                height, width, channels = array.shape
                fmt = QImage.Format_RGB888 if channels == 3 else QImage.Format_RGBA8888
                qimage = QImage(
                    array.data, width, height, array.strides[0], fmt
                ).copy()
            else:
                return False

            pixmap = QPixmap.fromImage(qimage)
            if pixmap.isNull():
                return False

            self.image_view.set_image(pixmap, image_path=None)
            return True
        except Exception as exc:
            logger.warning(f"Failed to set image from session array: {exc}")
            return False

    def _restore_session_workspace_from_container(
        self,
        session_path: Path,
        *,
        restore_measurement_history: bool = True,
        lock_shapes_if_measured: bool = True,
    ):
        """Restore image/zones/points from an existing session container into GUI."""
        return session_workspace_restore.restore_session_workspace_from_container(
            self,
            session_path,
            restore_measurement_history=restore_measurement_history,
            lock_shapes_if_measured=lock_shapes_if_measured,
        )

    def _restore_measurement_history_from_session(self, session_path: Path):
        """Populate per-point measurement widgets from session container payloads."""
        return session_workspace_restore.restore_measurement_history_from_session(
            self, session_path
        )

    def _extract_current_image_array(self):
        """Read current sample image into numpy array for session sync."""
        if not hasattr(self, "image_view"):
            return None

        image_path = getattr(self.image_view, "current_image_path", None)
        if image_path:
            return self._load_image_array_from_path(image_path)

        current_pixmap = getattr(self.image_view, "current_pixmap", None)
        if current_pixmap is None:
            return None

        try:
            import numpy as np
            from PyQt5.QtGui import QImage

            image = current_pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
            width = image.width()
            height = image.height()
            ptr = image.bits()
            ptr.setsize(image.byteCount())
            array = np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4))
            return array.copy()
        except Exception as exc:
            logger.warning(f"Failed to extract current pixmap for session sync: {exc}")
            return None

    @staticmethod
    def _build_rotated_image_array(image_array, rotation_deg: int):
        if image_array is None:
            return None

        try:
            import numpy as np

            normalized_rotation = int(rotation_deg or 0) % 360
            if normalized_rotation == 0:
                return np.ascontiguousarray(image_array)
            if normalized_rotation == 180:
                return np.ascontiguousarray(np.rot90(image_array, 2))
            quarter_turns = normalized_rotation // 90
            return np.ascontiguousarray(np.rot90(image_array, quarter_turns))
        except Exception as exc:
            logger.warning(f"Failed to rotate sample image for session sync: {exc}")
            return None

    def sync_workspace_to_session_container(self, state=None):
        """Persist image/zones/points snapshot into active unlocked session container."""
        if not hasattr(self, "session_manager"):
            return
        if not self.session_manager.is_session_active():
            return
        if self.session_manager.is_locked():
            return

        if state is None:
            state = getattr(self, "state", None) or {}

        try:
            import h5py
            schema = get_schema(self.config if hasattr(self, "config") else None)
            writer = get_writer(self.config if hasattr(self, "config") else None)

            session_path = self.session_manager.session_path
            session_path_str = str(session_path)
            if getattr(self, "_session_sync_cache_session_path", None) != session_path_str:
                self._session_sync_cache_session_path = session_path_str
                self._session_sync_shapes_sig = None
                self._session_sync_mapping_sig = None
                self._session_sync_points_sig = None
                self._session_sync_last_image_sig = None

            shapes = state.get("shapes", [])
            points = state.get("zone_points", [])
            mapping_conversion = self._build_mapping_conversion_payload()
            image_sig = self._image_file_signature()

            with h5py.File(session_path, "r") as h5f:
                raw_image_exists = False
                rotated_image_exists = False
                images_group = h5f.get(schema.GROUP_IMAGES)
                if images_group:
                    for image_id in sorted(images_group.keys()):
                        image_group = images_group.get(image_id)
                        if image_group is None or "data" not in image_group:
                            continue
                        image_type = image_group.attrs.get(
                            getattr(schema, "ATTR_IMAGE_TYPE", "image_type"),
                            "",
                        )
                        image_type = self._decode_attr(image_type) if hasattr(self, "_decode_attr") else str(image_type)
                        image_type = str(image_type or "").strip().lower()
                        if image_type == "sample":
                            raw_image_exists = True
                        elif image_type == self.SAMPLE_PHOTO_ROTATED_IMAGE_TYPE:
                            rotated_image_exists = True
                zones_exist = schema.GROUP_IMAGES_ZONES in h5f
                mapping_exists = f"{schema.GROUP_IMAGES_MAPPING}/mapping" in h5f
                points_exist = schema.GROUP_POINTS in h5f
                has_measurements = False
                measurements = h5f.get(schema.GROUP_MEASUREMENTS)
                if measurements:
                    for point_group in measurements.values():
                        if len(point_group.keys()) > 0:
                            has_measurements = True
                            break

            shapes_sig = self._sync_signature({"shapes": shapes})
            mapping_sig = self._sync_signature({"mapping": mapping_conversion})
            points_sig = self._sync_signature({"points": points})

            needs_shapes_sync = (not zones_exist) or (shapes_sig != getattr(self, "_session_sync_shapes_sig", None))
            needs_mapping_sync = (not mapping_exists) or (mapping_sig != getattr(self, "_session_sync_mapping_sig", None))
            effective_points_sig = points_sig if not has_measurements else "__points_locked_after_measurements__"
            needs_points_sync = (not has_measurements) and (
                (not points_exist) or (points_sig != getattr(self, "_session_sync_points_sig", None))
            )
            needs_raw_image_sync = not raw_image_exists
            rotation_confirmed = bool(getattr(self, "sample_photo_rotation_confirmed", False))
            rotation_deg = int(getattr(self, "sample_photo_rotation_deg", 0) or 0)
            rotated_image_sig = {
                "image_sig": image_sig,
                "rotation_confirmed": rotation_confirmed,
                "rotation_deg": rotation_deg,
            }
            rotated_image_sig = self._sync_signature(rotated_image_sig)
            needs_rotated_image_sync = rotation_confirmed and (
                (not rotated_image_exists)
                or (rotated_image_sig != getattr(self, "_session_sync_rotated_image_sig", None))
            )

            overall_sig = self._sync_signature(
                {
                    "session": session_path_str,
                    "image_sig": image_sig,
                    "raw_image_exists": raw_image_exists,
                    "rotated_image_sig": rotated_image_sig if rotation_confirmed else None,
                    "shapes_sig": shapes_sig,
                    "mapping_sig": mapping_sig,
                    "effective_points_sig": effective_points_sig,
                }
            )
            if overall_sig == getattr(self, "_session_sync_overall_sig", None):
                return

            did_write = False
            image_array = None
            if needs_raw_image_sync or needs_rotated_image_sync:
                image_array = self._extract_current_image_array()

            if needs_raw_image_sync:
                if image_array is not None:
                    writer.add_image(
                        file_path=session_path,
                        image_index=1,
                        image_data=image_array,
                        image_type="sample",
                    )
                    did_write = True
            else:
                last_image_sig = getattr(self, "_session_sync_last_image_sig", None)
                if (
                    image_sig is not None
                    and last_image_sig is not None
                    and image_sig != last_image_sig
                ):
                    logger.warning(
                        "Session image is immutable after first write; ignoring changed source image path"
                    )

            if needs_rotated_image_sync and image_array is not None:
                rotated_image_array = self._build_rotated_image_array(image_array, rotation_deg)
                if rotated_image_array is not None:
                    writer.add_image(
                        file_path=session_path,
                        image_index=2,
                        image_data=rotated_image_array,
                        image_type=self.SAMPLE_PHOTO_ROTATED_IMAGE_TYPE,
                    )
                    did_write = True

            if needs_shapes_sync:
                with h5py.File(session_path, "a") as h5f:
                    if schema.GROUP_IMAGES_ZONES in h5f:
                        del h5f[schema.GROUP_IMAGES_ZONES]
                    h5f.create_group(schema.GROUP_IMAGES_ZONES)
                did_write = True

                zone_exports, _sample_holder_zone_id = self._plan_session_zone_exports(shapes)
                for export in zone_exports:
                    zone_index = int(export["zone_index"])
                    shape = export["shape"]
                    role = str(shape.get("role", "include")).lower()
                    zone_role = str(export["zone_role"])
                    shape_type = str(shape.get("type", "circle")).lower()
                    geometry = shape.get("geometry", {})
                    geometry_px = [
                        float(geometry.get("x", 0)),
                        float(geometry.get("y", 0)),
                        float(geometry.get("width", 0)),
                        float(geometry.get("height", 0)),
                    ]
                    holder_diameter_mm = None
                    if zone_role == "sample_holder":
                        explicit_size_mm = shape.get("physical_size_mm")
                        try:
                            explicit_size_mm = float(explicit_size_mm)
                        except Exception:
                            explicit_size_mm = 0.0
                        if explicit_size_mm > 0:
                            holder_diameter_mm = explicit_size_mm
                        elif hasattr(self, "pixel_to_mm_ratio"):
                            diameter_px = max(geometry_px[2], geometry_px[3])
                            if getattr(self, "pixel_to_mm_ratio", 0):
                                holder_diameter_mm = diameter_px / float(self.pixel_to_mm_ratio)

                    writer.add_zone(
                        file_path=session_path,
                        zone_index=zone_index,
                        zone_role=zone_role,
                        geometry_px=geometry_px,
                        shape=shape_type,
                        holder_diameter_mm=holder_diameter_mm,
                    )

            if needs_mapping_sync and hasattr(self, "pixel_to_mm_ratio"):
                _zone_exports, sample_holder_zone_id = self._plan_session_zone_exports(shapes)
                if not sample_holder_zone_id:
                    sample_holder_zone_id = "zone_001"
                    logger.warning(
                        "No sample-holder zone found during session sync; falling back to %s for mapping metadata",
                        sample_holder_zone_id,
                    )
                writer.add_image_mapping(
                    file_path=session_path,
                    sample_holder_zone_id=sample_holder_zone_id,
                    pixel_to_mm_conversion=mapping_conversion,
                    orientation="standard",
                    mapping_version=schema.SCHEMA_VERSION,
                )
                did_write = True

            if needs_points_sync:
                with h5py.File(session_path, "a") as h5f:
                    if schema.GROUP_POINTS in h5f:
                        del h5f[schema.GROUP_POINTS]
                    h5f.create_group(schema.GROUP_POINTS)
                did_write = True

                for point_index, point in enumerate(points, start=1):
                    x = float(point.get("x", 0))
                    y = float(point.get("y", 0))
                    x_mm, y_mm = self._pixel_to_physical_mm(x, y)
                    point_path = writer.add_point(
                        file_path=session_path,
                        point_index=point_index,
                        pixel_coordinates=[x, y],
                        physical_coordinates_mm=[x_mm, y_mm],
                        point_status=schema.POINT_STATUS_PENDING,
                    )
                    point_uid = str(point.get("uid") or "").strip()
                    if point_uid:
                        with h5py.File(session_path, "a") as h5f:
                            h5f[point_path].attrs["point_uid"] = point_uid

            self._session_sync_shapes_sig = shapes_sig
            self._session_sync_mapping_sig = mapping_sig
            self._session_sync_points_sig = effective_points_sig
            self._session_sync_last_image_sig = image_sig
            self._session_sync_rotated_image_sig = (
                rotated_image_sig if rotation_confirmed else None
            )
            self._session_sync_overall_sig = overall_sig

            if did_write and hasattr(self, "_append_session_log"):
                self._append_session_log("Session workspace snapshot updated")

        except Exception as exc:
            logger.warning(
                f"Workspace snapshot sync to session failed: {exc}",
                exc_info=True,
            )
            if hasattr(self, "_append_session_log"):
                self._append_session_log(
                    f"Session workspace sync failed: {type(exc).__name__}"
                )
    
