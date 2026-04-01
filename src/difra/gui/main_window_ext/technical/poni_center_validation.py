"""PONI beam-center validation helpers driven by JSON configuration."""

from __future__ import annotations

import json
import re
from typing import Dict, Mapping, Optional, Sequence, Tuple, TypeVar

_T = TypeVar("_T")


def _to_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_alias(alias: str) -> str:
    return str(alias or "").strip().upper()


def resolve_poni_rule_alias(
    alias: str,
    detector_configs: Optional[Sequence[Mapping]] = None,
) -> str:
    alias_key = _normalize_alias(alias)
    for detector_cfg in detector_configs or ():
        if not isinstance(detector_cfg, Mapping):
            continue
        configured_alias = _normalize_alias(detector_cfg.get("alias"))
        if configured_alias != alias_key:
            continue
        mapped_alias = _normalize_alias(
            detector_cfg.get("poni_center_rule_alias") or detector_cfg.get("poni_rule_alias")
        )
        if mapped_alias:
            return mapped_alias
        break
    return alias_key


def normalize_alias_mapping_to_rule_aliases(
    values_by_alias: Optional[Mapping[str, _T]],
    detector_configs: Optional[Sequence[Mapping]] = None,
) -> Dict[str, _T]:
    normalized: Dict[str, _T] = {}
    for alias, value in (values_by_alias or {}).items():
        alias_key = resolve_poni_rule_alias(alias, detector_configs)
        if not alias_key:
            continue
        normalized[alias_key] = value
    return normalized


def parse_poni_center_px(
    poni_text: str,
    fallback_detector_size: Tuple[int, int] = (256, 256),
) -> Optional[Dict[str, float]]:
    """Parse PONI center in pixel coordinates.

    Returns a dict with:
    - row_px
    - col_px
    - width_px
    - height_px
    """
    text = str(poni_text or "")
    if not text.strip():
        return None

    match_poni1 = re.search(r"^Poni1:\s*([0-9.eE+\-]+)", text, flags=re.MULTILINE)
    match_poni2 = re.search(r"^Poni2:\s*([0-9.eE+\-]+)", text, flags=re.MULTILINE)
    poni1 = _to_float(match_poni1.group(1)) if match_poni1 else None
    poni2 = _to_float(match_poni2.group(1)) if match_poni2 else None

    pixel1 = None
    pixel2 = None
    width_px = None
    height_px = None

    for line in text.splitlines():
        if not str(line).startswith("Detector_config:"):
            continue
        payload = str(line).split(":", 1)[1].strip()
        try:
            cfg = json.loads(payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            cfg = None
        if not isinstance(cfg, dict):
            continue
        pixel1 = _to_float(cfg.get("pixel1"))
        pixel2 = _to_float(cfg.get("pixel2"))
        max_shape = cfg.get("max_shape")
        if isinstance(max_shape, (list, tuple)) and len(max_shape) >= 2:
            height_px = _to_float(max_shape[0])
            width_px = _to_float(max_shape[1])
        break

    if pixel1 is None:
        m = re.search(r"^PixelSize1:\s*([0-9.eE+\-]+)", text, flags=re.MULTILINE)
        pixel1 = _to_float(m.group(1)) if m else None
    if pixel2 is None:
        m = re.search(r"^PixelSize2:\s*([0-9.eE+\-]+)", text, flags=re.MULTILINE)
        pixel2 = _to_float(m.group(1)) if m else None

    try:
        fallback_width = float(fallback_detector_size[0])
        fallback_height = float(fallback_detector_size[1])
    except (TypeError, ValueError, IndexError):
        fallback_width = 256.0
        fallback_height = 256.0

    width_px = width_px if width_px is not None else fallback_width
    height_px = height_px if height_px is not None else fallback_height

    if None in (poni1, poni2, pixel1, pixel2):
        return None
    if float(pixel1) == 0.0 or float(pixel2) == 0.0:
        return None

    row_px = float(poni1) / float(pixel1)
    col_px = float(poni2) / float(pixel2)
    return {
        "row_px": float(row_px),
        "col_px": float(col_px),
        "width_px": float(width_px),
        "height_px": float(height_px),
    }


def _resolve_row_target_and_tolerance(
    *,
    rule: Mapping,
    defaults: Mapping,
    height_px: float,
) -> Tuple[Optional[float], Optional[float]]:
    row_target = _to_float(rule.get("row_target_px"))
    if row_target is None:
        row_target = _to_float(defaults.get("row_target_px"))
    if row_target is None:
        row_target = float(height_px) / 2.0

    row_tolerance_px = _to_float(rule.get("row_tolerance_px"))
    if row_tolerance_px is None:
        row_tolerance_px = _to_float(defaults.get("row_tolerance_px"))

    if row_tolerance_px is None:
        row_tolerance_percent = _to_float(rule.get("row_tolerance_percent"))
        if row_tolerance_percent is None:
            row_tolerance_percent = _to_float(defaults.get("row_tolerance_percent"))
        if row_tolerance_percent is not None:
            row_tolerance_px = float(height_px) * float(row_tolerance_percent) / 100.0

    return row_target, row_tolerance_px


def _format_rule_violation(
    alias: str,
    row_px: float,
    col_px: float,
    expected: str,
) -> str:
    return (
        f"PONI center for {alias} is outside the allowed zone. "
        f"Actual center: row={row_px:.2f}px, col={col_px:.2f}px. "
        f"Allowed rule: {expected}."
    )


def validate_poni_centers(
    *,
    poni_text_by_alias: Mapping[str, str],
    detector_sizes_by_alias: Optional[Mapping[str, Tuple[int, int]]],
    validation_config: Mapping,
) -> Tuple[list, list]:
    """Validate PONI center placement for configured detector aliases."""
    cfg = validation_config if isinstance(validation_config, Mapping) else {}
    if not bool(cfg.get("enabled", False)):
        return [], []

    rules = cfg.get("detectors", {})
    if not isinstance(rules, Mapping) or not rules:
        return [], []

    defaults = cfg.get("defaults", {})
    if not isinstance(defaults, Mapping):
        defaults = {}

    normalized_poni = {
        _normalize_alias(alias): text
        for alias, text in (poni_text_by_alias or {}).items()
        if str(alias or "").strip()
    }
    normalized_sizes = {
        _normalize_alias(alias): size
        for alias, size in (detector_sizes_by_alias or {}).items()
        if str(alias or "").strip()
    }

    errors = []
    warnings = []

    for alias_raw, rule_raw in rules.items():
        alias = _normalize_alias(alias_raw)
        rule = rule_raw if isinstance(rule_raw, Mapping) else {}

        poni_text = normalized_poni.get(alias, "")
        if not str(poni_text or "").strip():
            errors.append(f"PONI center validation: missing PONI content for alias {alias}")
            continue

        fallback_size = normalized_sizes.get(alias, (256, 256))
        geometry = parse_poni_center_px(poni_text, fallback_detector_size=fallback_size)
        if geometry is None:
            errors.append(
                f"PONI center validation: could not parse center/pixel geometry for alias {alias}"
            )
            continue

        row_px = float(geometry["row_px"])
        col_px = float(geometry["col_px"])
        width_px = float(geometry["width_px"])
        height_px = float(geometry["height_px"])

        row_target, row_tolerance_px = _resolve_row_target_and_tolerance(
            rule=rule,
            defaults=defaults,
            height_px=height_px,
        )
        if row_target is not None and row_tolerance_px is not None:
            row_min = float(row_target) - float(row_tolerance_px)
            row_max = float(row_target) + float(row_tolerance_px)
            if not (row_min <= row_px <= row_max):
                errors.append(
                    _format_rule_violation(
                        alias,
                        row_px,
                        col_px,
                        f"row in [{row_min:.2f}, {row_max:.2f}]",
                    )
                )

        col_target = _to_float(rule.get("col_target_px"))
        if col_target is None:
            col_target = _to_float(defaults.get("col_target_px"))
        col_tolerance_px = _to_float(rule.get("col_tolerance_px"))
        if col_tolerance_px is None:
            col_tolerance_px = _to_float(defaults.get("col_tolerance_px"))
        if col_target is not None and col_tolerance_px is not None:
            col_min_target = float(col_target) - float(col_tolerance_px)
            col_max_target = float(col_target) + float(col_tolerance_px)
            if not (col_min_target <= col_px <= col_max_target):
                errors.append(
                    _format_rule_violation(
                        alias,
                        row_px,
                        col_px,
                        f"col in [{col_min_target:.2f}, {col_max_target:.2f}]",
                    )
                )

        col_min_px = _to_float(rule.get("col_min_px"))
        if col_min_px is not None and col_px < float(col_min_px):
            errors.append(
                _format_rule_violation(
                    alias,
                    row_px,
                    col_px,
                    f"col >= {float(col_min_px):.2f}",
                )
            )

        col_max_px = _to_float(rule.get("col_max_px"))
        if col_max_px is not None and col_px > float(col_max_px):
            errors.append(
                _format_rule_violation(
                    alias,
                    row_px,
                    col_px,
                    f"col <= {float(col_max_px):.2f}",
                )
            )

        col_gt_px = _to_float(rule.get("col_gt_px"))
        if col_gt_px is not None and not (col_px > float(col_gt_px)):
            errors.append(
                _format_rule_violation(
                    alias,
                    row_px,
                    col_px,
                    f"col > {float(col_gt_px):.2f}",
                )
            )

        col_lt_px = _to_float(rule.get("col_lt_px"))
        if col_lt_px is not None and not (col_px < float(col_lt_px)):
            errors.append(
                _format_rule_violation(
                    alias,
                    row_px,
                    col_px,
                    f"col < {float(col_lt_px):.2f}",
                )
            )

        if width_px <= 0 or height_px <= 0:
            warnings.append(
                f"PONI center validation: non-positive detector shape for alias {alias}"
            )

    return errors, warnings
