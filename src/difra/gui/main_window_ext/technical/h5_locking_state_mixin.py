"""Technical H5 locking responsibilities: H5LockingStateMixin."""

from difra.gui.main_window_ext.technical.h5_locking_common import *


class H5LockingStateMixin:
    PONI_OVERRIDE_PASSWORD = "Ulster2026!"
    CONTAINER_STATE_ATTR = "container_state"
    CONTAINER_STATE_REASON_ATTR = "container_state_reason"
    CONTAINER_STATE_UPDATED_ATTR = "container_state_updated_at"
    STATE_DRAFT = "draft"
    STATE_PENDING_DISTANCES = "pending_distances"
    STATE_PENDING_PONI = "pending_poni"
    STATE_PENDING_PONI_REVIEW = "pending_poni_review"
    STATE_READY_TO_LOCK = "ready_to_lock"
    STATE_REJECTED_BLOCKED = "rejected_blocked"
    STATE_VALIDATION_FAILED = "validation_failed"
    STATE_LOCKED = "locked"
    STATE_ARCHIVED = "archived"
    VALID_CONTAINER_STATES = frozenset(
        {
            STATE_DRAFT,
            STATE_PENDING_DISTANCES,
            STATE_PENDING_PONI,
            STATE_PENDING_PONI_REVIEW,
            STATE_READY_TO_LOCK,
            STATE_REJECTED_BLOCKED,
            STATE_VALIDATION_FAILED,
            STATE_LOCKED,
            STATE_ARCHIVED,
        }
    )
    PONI_REVIEW_STATUS_ATTR = "poni_center_review_status"
    PONI_REVIEW_USER_ATTR = "poni_center_review_user"
    PONI_REVIEW_TS_ATTR = "poni_center_review_timestamp"
    PONI_REVIEW_IN_ZONE_ATTR = "poni_center_in_allowed_zone"
    PONI_REVIEW_NOTES_ATTR = "poni_center_review_notes"
    PONI_REVIEW_REASON_ATTR = "poni_center_review_reason"
    VALID_PONI_REJECT_REASONS = frozenset(
        {
            "user_rejected_preview",
            "center_out_of_zone",
            "review_unavailable",
            "reload_declined_after_reject",
            "other",
        }
    )

    @staticmethod
    def _to_float_or_none(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _decode_attr_text(value, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _technical_alias_candidates(self, *values):
        normalize = getattr(self, "_normalize_technical_alias_candidates", None)
        candidates = set()
        for value in values:
            token = self._decode_attr_text(value, "").strip()
            if not token:
                continue
            if token.lower().startswith("poni_"):
                token = token[5:]
            if callable(normalize):
                candidates.update(normalize(token))
            else:
                upper = str(token or "").strip().upper()
                if not upper:
                    continue
                candidates.add(upper)
                if upper.startswith("DET_"):
                    candidates.add(upper[4:])
                else:
                    candidates.add(f"DET_{upper}")
        return {candidate for candidate in candidates if candidate}

    def _resolve_configured_technical_alias(self, *values):
        source_candidates = self._technical_alias_candidates(*values)
        detector_configs = (
            self.config.get("detectors", [])
            if hasattr(self, "config") and isinstance(self.config, dict)
            else []
        )
        for detector_cfg in detector_configs:
            alias = str(detector_cfg.get("alias") or "").strip()
            if not alias:
                continue
            cfg_candidates = self._technical_alias_candidates(
                detector_cfg.get("alias"),
                detector_cfg.get("id"),
            )
            if source_candidates & cfg_candidates:
                return alias, detector_cfg.get("id"), source_candidates

        preferred = ""
        for value in values:
            token = self._decode_attr_text(value, "").strip()
            if not token:
                continue
            if token.lower().startswith("poni_"):
                token = token[5:]
            if token.lower().startswith("det_"):
                token = token[4:]
            token = token.strip().upper()
            if token:
                preferred = token
                break
        if not preferred:
            for alias in ("PRIMARY", "SECONDARY", "SAXS", "WAXS"):
                if alias in source_candidates:
                    preferred = alias
                    break
        if not preferred and source_candidates:
            preferred = sorted(source_candidates)[0]
        return preferred, None, source_candidates

    @staticmethod
    def _sync_lock_action_overrides():
        """Mirror monkeypatchable module globals into extracted helper actions."""
        h5_management_lock_actions.QMessageBox = QMessageBox
        h5_management_lock_actions.QInputDialog = QInputDialog
        h5_management_lock_actions.get_container_manager = get_container_manager

    @classmethod
    def _is_poni_override_note(cls, notes: str) -> bool:
        return (
            str(notes or "").strip().lower()
            == "accepted_password_override_out_of_zone"
        )

    def _write_container_attrs(self, container_path: Path, attrs: dict) -> bool:
        import h5py

        path = Path(container_path)
        if not path.exists():
            return False

        original_mode = None
        try:
            original_mode = path.stat().st_mode
            if not os.access(path, os.W_OK):
                os.chmod(path, original_mode | 0o200)
        except Exception:
            original_mode = None

        try:
            with h5py.File(path, "a") as h5f:
                for key, value in dict(attrs or {}).items():
                    h5f.attrs[str(key)] = value
            return True
        except Exception as exc:
            logger.warning(
                "Failed to write container attributes for %s: %s",
                path,
                exc,
                exc_info=True,
            )
            return False
        finally:
            if original_mode is not None:
                try:
                    os.chmod(path, original_mode)
                except Exception:
                    logger.debug(
                        "Suppressed exception while restoring container mode after attr write",
                        exc_info=True,
                    )

    def _set_container_state(self, container_path: Path, *, state: str, reason: str = "") -> bool:
        normalized_state = str(state or "").strip().lower()
        if normalized_state not in self.VALID_CONTAINER_STATES:
            logger.warning("Refusing invalid technical container state: %s", state)
            return False

        return self._write_container_attrs(
            Path(container_path),
            {
                self.CONTAINER_STATE_ATTR: normalized_state,
                self.CONTAINER_STATE_REASON_ATTR: str(reason or "").strip(),
                self.CONTAINER_STATE_UPDATED_ATTR: time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

    def _set_active_container_state(self, *, state: str, reason: str = "") -> bool:
        active_path = str(getattr(self, "_active_technical_container_path", "") or "").strip()
        if not active_path:
            return False
        return self._set_container_state(Path(active_path), state=state, reason=reason)

    def _container_has_distance_metadata(self, container_path: Path) -> bool:
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        try:
            with h5py.File(container_path, "r") as h5f:
                distance_attr = h5f.attrs.get(schema.ATTR_DISTANCE_CM)
                if distance_attr is not None:
                    try:
                        float(distance_attr)
                        return True
                    except Exception:
                        pass

                tech_group = h5f.get(schema.GROUP_TECHNICAL)
                if tech_group is None:
                    tech_group = h5f.get(f"{schema.GROUP_CALIBRATION_SNAPSHOT}/events")
                if tech_group is not None:
                    for event_name in tech_group.keys():
                        event_group = tech_group[event_name]
                        for detector_name in event_group.keys():
                            detector_group = event_group[detector_name]
                            distance_attr = detector_group.attrs.get(schema.ATTR_DISTANCE_CM)
                            if distance_attr is not None:
                                try:
                                    float(distance_attr)
                                    return True
                                except Exception:
                                    pass

                poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
                if poni_group is not None:
                    for ds_name in poni_group.keys():
                        distance_attr = poni_group[ds_name].attrs.get(schema.ATTR_DISTANCE_CM)
                        if distance_attr is not None:
                            try:
                                float(distance_attr)
                                return True
                            except Exception:
                                pass
        except Exception:
            return False

        return False

    def _infer_container_state(self, container_path: Path) -> str:
        path = Path(container_path)
        try:
            import h5py

            with h5py.File(path, "r") as h5f:
                persisted_state = self._decode_attr_text(
                    h5f.attrs.get(self.CONTAINER_STATE_ATTR, "")
                ).strip().lower()
                if persisted_state == self.STATE_ARCHIVED:
                    return self.STATE_ARCHIVED
                if persisted_state == self.STATE_VALIDATION_FAILED:
                    return self.STATE_VALIDATION_FAILED
        except Exception:
            pass

        container_manager = get_container_manager(self.config if hasattr(self, "config") else None)
        try:
            if container_manager.is_container_locked(path):
                return self.STATE_LOCKED
        except Exception:
            pass

        aliases = []
        collect_aliases = getattr(self, "_collect_lock_detector_aliases", None)
        if callable(collect_aliases):
            try:
                aliases = [str(alias).strip() for alias in (collect_aliases(path) or []) if str(alias).strip()]
            except Exception:
                aliases = []

        distance_map = {}
        get_distance_map = getattr(self, "_distance_map_by_alias", None)
        if callable(get_distance_map):
            try:
                distance_map = {
                    str(alias).strip(): float(value)
                    for alias, value in (get_distance_map() or {}).items()
                    if str(alias).strip()
                }
            except Exception:
                distance_map = {}

        if aliases:
            missing = [alias for alias in aliases if alias not in distance_map]
            if missing:
                return self.STATE_PENDING_DISTANCES
        elif not self._container_has_distance_metadata(path):
            return self.STATE_PENDING_DISTANCES

        has_poni_datasets = False
        has_poni = getattr(self, "_container_has_poni_datasets", None)
        if callable(has_poni):
            try:
                has_poni_datasets = bool(has_poni(path))
            except Exception:
                has_poni_datasets = False
        if not has_poni_datasets:
            return self.STATE_PENDING_PONI

        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        validation_enabled = isinstance(validation_cfg, dict) and bool(
            validation_cfg.get("enabled", False)
        )
        if not validation_enabled:
            return self.STATE_READY_TO_LOCK

        review_state = self._read_poni_review_state(path)
        if review_state.get("status") == "rejected":
            return self.STATE_REJECTED_BLOCKED
        if (
            review_state.get("status") == "accepted"
            and (
                bool(review_state.get("in_zone", False))
                or self._is_poni_override_note(review_state.get("notes", ""))
            )
        ):
            return self.STATE_READY_TO_LOCK
        return self.STATE_PENDING_PONI_REVIEW

    def _sync_container_state(self, container_path: Path, *, reason: str = "auto_sync") -> str:
        inferred = self._infer_container_state(Path(container_path))
        self._set_container_state(Path(container_path), state=inferred, reason=reason)
        return inferred

