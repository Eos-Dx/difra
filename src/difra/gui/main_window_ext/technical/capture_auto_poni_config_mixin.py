import logging
import os
from pathlib import Path

from difra.gui.main_window_ext.technical.capture_auto_poni_prompt_mixin import (
    TechnicalCaptureAutoPoniPromptMixin,
)

logger = logging.getLogger(__name__)


def _tm():
    from difra.gui.main_window_ext.technical import capture_mixin

    return capture_mixin._tm()


class TechnicalCaptureAutoPoniConfigMixin(TechnicalCaptureAutoPoniPromptMixin):
    def _resolve_auto_poni_pyfai_calib2_env(self) -> str:
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        auto_cfg = cfg.get("auto_poni_calibration", {})
        if isinstance(auto_cfg, dict):
            explicit = str(auto_cfg.get("pyfai_calib2_env") or "").strip()
            if explicit:
                return explicit

        env_explicit = str(os.environ.get("DIFRA_PYFAI_CALIB2_ENV") or "").strip()
        if env_explicit:
            return env_explicit

        sidecar_env = str(os.environ.get("SIDECAR_ENV") or os.environ.get("DIFRA_SIDECAR_ENV") or "").strip()
        if sidecar_env:
            return sidecar_env

        return self._resolve_pyfai_conda_env()
    def _auto_poni_metadata_validation_config(self):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_metadata_validation", {})
        if not isinstance(validation_cfg, dict) or not bool(validation_cfg.get("enabled", False)):
            return {}
        if bool(cfg.get("DEV", False)) and not bool(validation_cfg.get("apply_in_dev_mode", False)):
            return {}
        return validation_cfg
    def _auto_poni_metadata_validation_errors(self, reviews: dict) -> list[str]:
        validation_cfg = self._auto_poni_metadata_validation_config()
        if not validation_cfg:
            return []
        try:
            from difra.gui.main_window_ext.technical.poni_center_validation import (
                validate_poni_metadata,
            )
        except Exception as exc:
            return [f"PONI metadata validator unavailable: {exc}"]

        return validate_poni_metadata(
            poni_text_by_alias={
                str(alias): str(getattr(review, "poni_text", "") or "")
                for alias, review in dict(reviews or {}).items()
            },
            validation_config=validation_cfg,
        )
    def _auto_poni_config(self) -> dict:
        from difra.gui.technical.pyfai_calibration import normalized_auto_poni_config

        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        return normalized_auto_poni_config(cfg)
    def _auto_poni_rule_alias(self, alias: str) -> str:
        try:
            from difra.gui.main_window_ext.technical.poni_center_validation import (
                resolve_poni_rule_alias,
            )

            detector_cfgs = self.config.get("detectors", []) if hasattr(self, "config") else []
            return resolve_poni_rule_alias(alias, detector_cfgs)
        except Exception:
            return str(alias or "").strip().upper()
    @staticmethod
    def _auto_poni_distance_key(distance_cm) -> str:
        try:
            from difra.gui.technical.pyfai_calibration import auto_poni_distance_key

            return auto_poni_distance_key(distance_cm)
        except Exception:
            try:
                value = float(distance_cm)
            except (TypeError, ValueError):
                return ""
            rounded = round(value)
            if abs(value - rounded) <= 0.55:
                return str(int(rounded))
            return f"{value:.3f}".rstrip("0").rstrip(".")
    def _active_technical_container_distance_cm_for_auto_poni(self):
        active_path = None
        active_getter = getattr(self, "_active_technical_container_path_obj", None)
        if callable(active_getter):
            try:
                active_path = active_getter()
            except Exception:
                active_path = None
        if active_path is None:
            raw_path = str(
                getattr(self, "_active_technical_container_path", "") or ""
            ).strip()
            active_path = Path(raw_path) if raw_path else None
        if active_path is None:
            return None

        reader = getattr(self, "_read_technical_container_distance_cm", None)
        if callable(reader):
            try:
                value = reader(Path(active_path))
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                return None
            except Exception:
                logger.debug(
                    "Failed to read Auto PONI container distance",
                    exc_info=True,
                )

        try:
            import h5py

            with h5py.File(active_path, "r") as h5f:
                value = h5f.attrs.get("distance_cm")
                return None if value is None else float(value)
        except Exception:
            logger.debug("Failed to read Auto PONI root distance", exc_info=True)
            return None
    def _auto_poni_seed_distance_cm(self, *, alias: str, nominal_distance_cm, auto_cfg: dict):
        try:
            from difra.gui.technical.pyfai_calibration import auto_poni_seed_distance_cm

            return auto_poni_seed_distance_cm(
                auto_cfg,
                alias=alias,
                nominal_distance_cm=nominal_distance_cm,
            )
        except Exception:
            return nominal_distance_cm
    def _auto_poni_seed_center_px_from_config(self, alias: str, auto_cfg: dict | None = None):
        try:
            from difra.gui.technical.pyfai_calibration import auto_poni_seed_center_px

            cfg = auto_cfg if isinstance(auto_cfg, dict) else self._auto_poni_config()
            return auto_poni_seed_center_px(cfg, alias=alias)
        except Exception:
            return None
    def _auto_poni_default_distance_cm_by_alias(self, aliases, auto_cfg: dict | None = None) -> dict:
        distance_cm = self._active_technical_container_distance_cm_for_auto_poni()
        if distance_cm is not None:
            return {
                str(alias).strip().upper(): float(
                    self._auto_poni_seed_distance_cm(
                        alias=str(alias).strip().upper(),
                        nominal_distance_cm=distance_cm,
                        auto_cfg=auto_cfg or {},
                    )
                    or distance_cm
                )
                for alias in aliases
            }

        result = {}
        for alias in aliases:
            alias_key = str(alias or "").strip().upper()
            detector_config = self._detector_config_for_alias(alias)
            distance_m = self._distance_m_for_detector_alias(alias, detector_config)
            if distance_m is not None:
                result[alias_key] = float(distance_m) * 100.0
        return result
    def _auto_poni_default_first_visible_ring(
        self,
        *,
        alias: str,
        distance_cm,
        auto_cfg: dict,
    ) -> int:
        alias_key = str(alias or "").strip().upper()
        rule_key = str(self._auto_poni_rule_alias(alias) or "").strip().upper()
        by_distance = auto_cfg.get("first_visible_ring_by_distance_cm", {})
        distance_key = self._auto_poni_distance_key(distance_cm)
        if isinstance(by_distance, dict):
            distance_rules = by_distance.get(distance_key, {})
            if isinstance(distance_rules, dict):
                for key in (alias_key, rule_key):
                    try:
                        ring = int(distance_rules.get(key))
                    except (TypeError, ValueError):
                        continue
                    if ring > 0:
                        return ring

        configured = auto_cfg.get("first_visible_ring_by_alias", {})
        if isinstance(configured, dict):
            for key in (alias_key, rule_key):
                try:
                    ring = int(configured.get(key))
                except (TypeError, ValueError):
                    continue
                if ring > 0:
                    return ring
        return 1
    def _auto_poni_default_rings_to_search(
        self,
        *,
        alias: str,
        distance_cm,
        auto_cfg: dict,
    ) -> int:
        alias_key = str(alias or "").strip().upper()
        rule_key = str(self._auto_poni_rule_alias(alias) or "").strip().upper()
        by_distance = auto_cfg.get("rings_to_search_by_distance_cm", {})
        distance_key = self._auto_poni_distance_key(distance_cm)
        if isinstance(by_distance, dict):
            distance_rules = by_distance.get(distance_key, {})
            if isinstance(distance_rules, dict):
                for key in (alias_key, rule_key):
                    try:
                        count = int(distance_rules.get(key))
                    except (TypeError, ValueError):
                        continue
                    if count > 0:
                        return count

        configured = auto_cfg.get("rings_to_search_by_alias", {})
        if isinstance(configured, dict):
            for key in (alias_key, rule_key):
                try:
                    count = int(configured.get(key))
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    return count
        try:
            return max(1, int(auto_cfg.get("rings_to_show", 3)))
        except (TypeError, ValueError):
            return 3
    def _auto_poni_default_settings(self, auto_cfg: dict, aliases) -> dict:
        distance_by_alias = self._auto_poni_default_distance_cm_by_alias(
            aliases,
            auto_cfg=auto_cfg,
        )
        first_visible = {}
        rings_to_search = {}
        for alias in aliases:
            alias_key = str(alias or "").strip().upper()
            distance_cm = distance_by_alias.get(alias_key)
            first_visible[alias_key] = self._auto_poni_default_first_visible_ring(
                alias=alias,
                distance_cm=distance_cm,
                auto_cfg=auto_cfg,
            )
            rings_to_search[alias_key] = self._auto_poni_default_rings_to_search(
                alias=alias,
                distance_cm=distance_cm,
                auto_cfg=auto_cfg,
            )
        return {
            "distance_cm_by_alias": distance_by_alias,
            "first_visible_ring_by_alias": first_visible,
            "rings_to_search_by_alias": rings_to_search,
            "energy_kev": float(auto_cfg.get("energy_kev", 8.04) or 8.04),
        }
    @staticmethod
    def _auto_poni_agbh_q_range_text(first_visible_ring, rings_to_search) -> str:
        try:
            from difra.gui.technical.pyfai_calibration import AGBH_D_SPACING_A
            import math

            first = max(1, int(first_visible_ring or 1))
            count = max(1, int(rings_to_search or 1))
            last = min(len(AGBH_D_SPACING_A), first + count - 1)
            if first > len(AGBH_D_SPACING_A):
                return f"I(q): rings {first}-{first + count - 1}"
            q_min = 20.0 * math.pi / float(AGBH_D_SPACING_A[first - 1])
            q_max = 20.0 * math.pi / float(AGBH_D_SPACING_A[last - 1])
            return f"I(q): {q_min:.2f}-{q_max:.2f} nm^-1 | rings {first}-{last}"
        except Exception:
            try:
                first = max(1, int(first_visible_ring or 1))
                count = max(1, int(rings_to_search or 1))
            except (TypeError, ValueError):
                first, count = 1, 1
            return f"I(q): rings {first}-{first + count - 1}"
    @staticmethod
    def _auto_poni_float_or_none(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
    def _auto_poni_center_px_from_validation_config(
        self,
        alias: str,
        detector_config: dict,
    ):
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return None

        detector_rules = validation_cfg.get("detectors", {})
        if not isinstance(detector_rules, dict):
            return None

        try:
            from difra.gui.main_window_ext.technical.poni_center_validation import (
                resolve_poni_rule_alias,
            )

            rule_alias = resolve_poni_rule_alias(alias, cfg.get("detectors", []))
        except Exception:
            rule_alias = str(alias or "").strip().upper()
        rule_alias = str(rule_alias or "").strip().upper()

        rule = {}
        defaults = validation_cfg.get("defaults", {})
        if isinstance(defaults, dict):
            rule.update(defaults)
        for key, value in detector_rules.items():
            if str(key or "").strip().upper() == rule_alias and isinstance(value, dict):
                rule.update(value)
                break
        if not rule:
            return None

        size_cfg = detector_config.get("size", {}) if isinstance(detector_config, dict) else {}
        if isinstance(size_cfg, dict):
            width = self._auto_poni_float_or_none(size_cfg.get("width")) or 256.0
            height = self._auto_poni_float_or_none(size_cfg.get("height")) or 256.0
        else:
            width = height = 256.0

        row = self._auto_poni_float_or_none(rule.get("row_target_px"))
        if row is None:
            row = height / 2.0

        col = self._auto_poni_float_or_none(rule.get("col_target_px"))
        col_min = self._auto_poni_float_or_none(rule.get("col_min_px"))
        col_max = self._auto_poni_float_or_none(rule.get("col_max_px"))
        col_gt = self._auto_poni_float_or_none(rule.get("col_gt_px"))
        col_lt = self._auto_poni_float_or_none(rule.get("col_lt_px"))

        if col is None:
            if col_gt is not None and col_max is not None and col_max > col_gt:
                col = col_max
            elif col_min is not None and col_lt is not None and col_lt > col_min:
                col = col_min
            elif col_gt is not None and col_lt is not None and col_lt > col_gt:
                col = (col_gt + col_lt) / 2.0
            elif col_gt is not None:
                col = col_gt + 1.0
            elif col_min is not None and col_max is not None and col_max >= col_min:
                col = (col_min + col_max) / 2.0
            elif col_min is not None:
                col = col_min
            elif col_lt is not None:
                col = col_lt - 1.0
            elif col_max is not None:
                col = col_max
            else:
                col = width / 2.0

        if col_gt is not None and not (col > col_gt):
            col = col_gt + 1.0
        if col_min is not None and col < col_min:
            col = col_min
        if col_lt is not None and not (col < col_lt):
            col = col_lt - 1.0
        if col_max is not None and col > col_max:
            col = col_max

        return float(row), float(col)
    def _auto_poni_center_px_for_alias(self, alias: str, detector_config: dict):
        configured_center = self._auto_poni_center_px_from_validation_config(
            alias,
            detector_config,
        )
        if configured_center is not None:
            return configured_center

        existing_poni = str((getattr(self, "ponis", {}) or {}).get(alias) or "")
        if existing_poni:
            return None
        if str(detector_config.get("default_poni") or "").strip():
            return None

        resolver = getattr(self, "_resolve_fake_demo_center_px", None)
        if not callable(resolver):
            return None
        size_cfg = detector_config.get("size", {})
        if isinstance(size_cfg, dict):
            size = (size_cfg.get("width", 256), size_cfg.get("height", 256))
        else:
            size = (256, 256)
        try:
            return resolver(alias, size)
        except Exception:
            logger.debug("Failed to resolve auto PONI center for %s", alias, exc_info=True)
            return None
