"""Session state helpers for SessionManager."""

from datetime import datetime
from typing import Dict, Optional

import h5py

from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionManagerStateMixin:
    """Workflow state, record counting, and runtime logging helpers."""

    def _set_session_state(self, state: str, reason: str = "") -> bool:
        """Persist session workflow state into the active container attrs."""
        if not self.is_session_active():
            return False

        state_token = str(state or "").strip().lower()
        if state_token not in self.VALID_SESSION_STATES:
            logger.warning(
                "Ignoring invalid session state update",
                requested_state=state_token,
            )
            return False

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with h5py.File(self.session_path, "a") as h5f:
                h5f.attrs[self.SESSION_STATE_ATTR] = state_token
                h5f.attrs[self.SESSION_STATE_REASON_ATTR] = str(reason or "").strip()
                h5f.attrs[self.SESSION_STATE_UPDATED_ATTR] = timestamp
            self.session_state = state_token
            return True
        except Exception as exc:
            logger.warning(
                "Failed to persist session state",
                session_path=str(self.session_path),
                state=state_token,
                reason=str(reason or ""),
                error=str(exc),
                exc_info=True,
            )
            return False

    def _count_measurement_records(self) -> int:
        """Count persisted point-measurement records in active session."""
        self._check_active()
        with h5py.File(self.session_path, "r") as h5f:
            measurements_group = h5f.get(self.schema.GROUP_MEASUREMENTS)
            if measurements_group is None:
                return 0
            total = 0
            for point_group in measurements_group.values():
                try:
                    total += int(len(list(point_group.keys())))
                except Exception:
                    continue
        return int(total)

    def has_point_measurements(self) -> bool:
        """Return True once any point measurement record exists (incl. in-progress)."""
        if not self.is_session_active():
            return False
        if self._pending_measurements:
            return True
        try:
            return self._count_measurement_records() > 0
        except Exception:
            return False

    def _infer_session_state_from_h5(self, h5f) -> str:
        """Infer session workflow state from container contents."""
        raw_state = (
            self._as_text(
                h5f.attrs.get(self.SESSION_STATE_ATTR),
                "",
            )
            .strip()
            .lower()
        )
        if raw_state in self.VALID_SESSION_STATES:
            return raw_state

        locked = bool(self.container_manager.is_container_locked(self.session_path))
        if locked:
            return self.SESSION_STATE_LOCKED

        measurements_group = h5f.get(self.schema.GROUP_MEASUREMENTS)
        has_measurements = False
        has_in_progress = False
        if measurements_group is not None:
            for point_group in measurements_group.values():
                for measurement_group in point_group.values():
                    has_measurements = True
                    status = (
                        self._as_text(
                            measurement_group.attrs.get(
                                self.schema.ATTR_MEASUREMENT_STATUS, ""
                            ),
                            "",
                        )
                        .strip()
                        .lower()
                    )
                    if status == self.schema.STATUS_IN_PROGRESS:
                        has_in_progress = True
                        break
                if has_in_progress:
                    break

        if has_in_progress:
            return self.SESSION_STATE_RECOVERY_REQUIRED
        if has_measurements:
            return self.SESSION_STATE_MEASURING

        points_group = h5f.get(self.schema.GROUP_POINTS)
        has_points = bool(points_group is not None and len(points_group.keys()) > 0)
        zones_group = h5f.get(self.schema.GROUP_IMAGES_ZONES)
        has_zones = bool(zones_group is not None and len(zones_group.keys()) > 0)
        has_mapping = bool(f"{self.schema.GROUP_IMAGES_MAPPING}/mapping" in h5f)
        if has_points or has_zones or has_mapping:
            return self.SESSION_STATE_PREPARED
        return self.SESSION_STATE_DRAFT

    def reset_for_image_reform(
        self,
        *,
        image_data=None,
        reset_attenuation: bool = True,
    ) -> None:
        """Reset session workspace for image reform before first point measurement."""
        self._check_active()
        if self.is_locked():
            raise RuntimeError("Cannot reform image: session container is locked.")
        if self.has_point_measurements():
            raise RuntimeError("Cannot reform image: point measurements already exist.")

        ana_group = getattr(
            self.schema, "GROUP_ANALYTICAL_MEASUREMENTS", "/analytical_measurements"
        )
        groups_to_delete = [
            self.schema.GROUP_IMAGES,
            self.schema.GROUP_POINTS,
            self.schema.GROUP_MEASUREMENTS,
        ]
        if reset_attenuation:
            groups_to_delete.append(ana_group)

        with h5py.File(self.session_path, "a") as h5f:
            for group_path in groups_to_delete:
                if group_path in h5f:
                    del h5f[group_path]

            h5f.require_group(self.schema.GROUP_IMAGES.lstrip("/"))
            h5f.require_group(self.schema.GROUP_IMAGES_ZONES.lstrip("/"))
            h5f.require_group(self.schema.GROUP_IMAGES_MAPPING.lstrip("/"))
            h5f.require_group(self.schema.GROUP_POINTS.lstrip("/"))
            h5f.require_group(self.schema.GROUP_MEASUREMENTS.lstrip("/"))
            if reset_attenuation:
                h5f.require_group(ana_group.lstrip("/"))

            measurement_counter_attr = getattr(
                self.schema, "ATTR_MEASUREMENT_COUNTER", "measurement_counter"
            )
            h5f.attrs[measurement_counter_attr] = int(0)

        self._pending_measurements = {}
        if reset_attenuation:
            self.i0_counter = None
            self.i_counter = None

        if image_data is not None:
            self.writer.add_image(
                file_path=self.session_path,
                image_index=1,
                image_data=image_data,
                image_type="sample",
            )

        self._set_session_state(
            self.SESSION_STATE_DRAFT,
            reason="image_reformed_workspace_reset",
        )
        self.log_event(
            message="Session workspace reset for image reform",
            event_type="workspace_reformed",
            details={"attenuation_reset": bool(reset_attenuation)},
        )

    def log_event(
        self,
        message: str,
        event_type: str = "event",
        level: str = "INFO",
        details: Optional[Dict] = None,
    ) -> None:
        """Append session runtime event to container log dataset."""
        if not self.is_session_active():
            return
        append_runtime_log = getattr(self.writer, "append_runtime_log", None)
        if not callable(append_runtime_log):
            return
        append_runtime_log(
            file_path=self.session_path,
            message=message,
            level=level,
            event_type=event_type,
            source=self.producer_software,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            details=details or {},
        )
