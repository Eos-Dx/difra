"""Dev-mode fake PONI generation helpers for technical H5 generation."""

from . import h5_generation_mixin as _module
from .poni_center_validation import resolve_poni_rule_alias
from difra.gui.technical.pyfai_calibration_common import detector_size_px, pixel_size_m

logger = _module.logger


class H5GenerationFakePoniMixin:
    @staticmethod
    def _to_float_or_none(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def _resolve_fake_demo_center_px(self, alias: str, detector_size):
        """Resolve fake demo PONI center from configured center validation rules."""
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        alias_key = resolve_poni_rule_alias(alias, cfg.get("detectors", []))
        if alias_key == "PRIMARY":
            return 128.0, 8.0
        if alias_key == "SECONDARY":
            return 128.0, 280.0

        try:
            width = float(detector_size[0])
            height = float(detector_size[1])
        except Exception:
            width, height = 256.0, 256.0

        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict):
            validation_cfg = {}

        defaults = validation_cfg.get("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}

        rules = validation_cfg.get("detectors", {})
        if not isinstance(rules, dict):
            rules = {}

        rule = dict(defaults) if defaults else {}
        for key, value in rules.items():
            if str(key or "").strip().upper() == alias_key and isinstance(value, dict):
                rule.update(value)
                break

        row_target = self._to_float_or_none(rule.get("row_target_px"))
        if row_target is None:
            row_target = height / 2.0

        col_target = self._to_float_or_none(rule.get("col_target_px"))
        col_min = self._to_float_or_none(rule.get("col_min_px"))
        col_max = self._to_float_or_none(rule.get("col_max_px"))
        col_gt = self._to_float_or_none(rule.get("col_gt_px"))
        col_lt = self._to_float_or_none(rule.get("col_lt_px"))

        if col_target is not None:
            col = float(col_target)
        elif col_gt is not None and col_lt is not None and float(col_lt) > float(col_gt):
            col = (float(col_gt) + float(col_lt)) / 2.0
        elif col_gt is not None:
            col = float(col_gt) + 1.0
        elif col_min is not None and col_max is not None and float(col_max) >= float(col_min):
            col = (float(col_min) + float(col_max)) / 2.0
        elif col_min is not None:
            col = float(col_min)
        elif col_lt is not None:
            col = float(col_lt) - 1.0
        elif col_max is not None:
            col = float(col_max)
        else:
            col = width / 2.0

        if col_gt is not None and not (col > float(col_gt)):
            col = float(col_gt) + 1.0
        if col_min is not None and col < float(col_min):
            col = float(col_min)
        if col_lt is not None and not (col < float(col_lt)):
            col = float(col_lt) - 1.0
        if col_max is not None and col > float(col_max):
            col = float(col_max)

        return float(row_target), float(col)

    def _generate_fake_poni_data(self, aliases, user_distance_cm):
        """Generate fake PONI data for dev mode with distances near user value."""
        import random
        import time

        poni_data = {}

        for alias in aliases:
            detector_config = None
            for detector in self.config.get("detectors", []):
                if detector.get("alias") == alias:
                    detector_config = detector
                    break

            if not detector_config:
                detector_config = {"alias": alias}

            random.seed(hash(alias))
            margin = random.uniform(-0.03, 0.03)
            fake_distance_m = (user_distance_cm / 100.0) * (1 + margin)

            width, height = detector_size_px(detector_config)
            pixel1, pixel2 = pixel_size_m(detector_config)
            row_px, col_px = self._resolve_fake_demo_center_px(alias, (width, height))
            poni1 = float(row_px) * float(pixel1)
            poni2 = float(col_px) * float(pixel2)

            wavelength = 1.5406e-10
            current_time = time.strftime("%a %b %d %H:%M:%S %Y")

            poni_content = f"""# Nota: C-Order, 1 refers to the Y axis, 2 to the X axis
# Calibration done on {current_time} (DEV MODE - FAKE DATA)
poni_version: 2.1
Detector: Detector
Detector_config: {{"pixel1": {pixel1}, "pixel2": {pixel2}, "max_shape": [{height}, {width}], "orientation": 3}}
Distance: {fake_distance_m}
Poni1: {poni1}
Poni2: {poni2}
Rot1: 0
Rot2: 0
Rot3: 0
Wavelength: {wavelength}
# Calibrant: AgBh (DEV MODE)
# Detector: {alias} (DEV MODE - FAKE DATA)
# User specified: {user_distance_cm:.2f} cm, Generated: {fake_distance_m*100:.2f} cm (margin: {margin*100:.1f}%)
"""

            poni_filename = f"{alias.lower()}_fake_h5gen.poni"
            poni_data[alias] = (poni_content, poni_filename)

            logger.info(
                f"Generated fake PONI for {alias}: distance={fake_distance_m*100:.2f} cm "
                f"(user: {user_distance_cm:.2f} cm, margin: {margin*100:.1f}%)"
            )

        return poni_data
