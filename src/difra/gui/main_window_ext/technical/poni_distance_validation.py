"""PONI detector-distance validation helpers."""

from __future__ import annotations

from typing import Mapping, Optional, Tuple


def _to_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_poni_distance_cm(poni_text: str) -> Optional[float]:
    for line in str(poni_text or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("Distance:"):
            continue
        value = _to_float(stripped.split(":", 1)[1].strip())
        if value is None:
            return None
        return float(value) * 100.0
    return None


def _configured_tolerance_percent(validation_config: Mapping | None) -> float:
    cfg = validation_config if isinstance(validation_config, Mapping) else {}
    value = _to_float(
        cfg.get("tolerance_percent")
        if "tolerance_percent" in cfg
        else cfg.get("default_tolerance_percent")
    )
    if value is None:
        value = 5.0
    return max(0.0, float(value))


def _configured_nominal_range(
    *,
    expected_cm: float,
    alias: str,
    validation_config: Mapping | None,
) -> Optional[Tuple[float, float]]:
    cfg = validation_config if isinstance(validation_config, Mapping) else {}
    ranges = cfg.get("nominal_ranges_cm") or cfg.get("distance_ranges_cm") or []
    if not isinstance(ranges, (list, tuple)):
        return None

    alias_key = str(alias or "").strip().upper()
    for rule in ranges:
        if not isinstance(rule, Mapping):
            continue

        aliases = rule.get("aliases")
        if aliases:
            allowed = {str(item or "").strip().upper() for item in aliases}
            if alias_key and alias_key not in allowed:
                continue

        nominal = _to_float(rule.get("nominal_cm"))
        min_cm = _to_float(rule.get("min_cm"))
        max_cm = _to_float(rule.get("max_cm"))
        if nominal is None or min_cm is None or max_cm is None:
            continue

        match_tolerance_cm = _to_float(rule.get("match_tolerance_cm"))
        if match_tolerance_cm is None:
            match_tolerance_cm = max(0.25, abs(float(nominal)) * 0.10)

        if abs(float(expected_cm) - float(nominal)) <= float(match_tolerance_cm):
            lower, upper = sorted((float(min_cm), float(max_cm)))
            return lower, upper

    return None


def allowed_distance_bounds_cm(
    *,
    expected_cm: float,
    alias: str = "",
    validation_config: Mapping | None = None,
) -> Tuple[float, float, str]:
    configured_range = _configured_nominal_range(
        expected_cm=float(expected_cm),
        alias=alias,
        validation_config=validation_config,
    )
    if configured_range is not None:
        return configured_range[0], configured_range[1], "nominal_range"

    tolerance_percent = _configured_tolerance_percent(validation_config)
    delta = abs(float(expected_cm)) * tolerance_percent / 100.0
    return float(expected_cm) - delta, float(expected_cm) + delta, "percent_tolerance"


def validate_poni_distances(
    *,
    poni_text_by_alias: Mapping[str, str],
    distances_by_alias: Mapping[str, float],
    poni_name_by_alias: Mapping[str, str] | None = None,
    validation_config: Mapping | None = None,
) -> list[str]:
    errors: list[str] = []
    if not poni_text_by_alias or not distances_by_alias:
        return errors

    normalized_distances = {}
    for alias, distance in dict(distances_by_alias).items():
        alias_key = str(alias or "").strip().upper()
        expected = _to_float(distance)
        if alias_key and expected is not None:
            normalized_distances[alias_key] = float(expected)

    names = {
        str(alias or "").strip().upper(): str(name or "").strip()
        for alias, name in dict(poni_name_by_alias or {}).items()
    }

    for alias, poni_text in dict(poni_text_by_alias).items():
        alias_key = str(alias or "").strip().upper()
        if alias_key not in normalized_distances:
            continue

        poni_name = names.get(alias_key) or f"{alias_key or alias}.poni"
        poni_distance_cm = parse_poni_distance_cm(str(poni_text or ""))
        if poni_distance_cm is None:
            errors.append(f"{alias}: cannot read Distance from {poni_name}")
            continue

        expected_cm = normalized_distances[alias_key]
        if expected_cm <= 0:
            errors.append(f"{alias}: invalid detector distance {expected_cm:g} cm")
            continue

        lower, upper, mode = allowed_distance_bounds_cm(
            expected_cm=expected_cm,
            alias=alias_key,
            validation_config=validation_config,
        )
        if lower <= float(poni_distance_cm) <= upper:
            continue

        deviation_percent = abs(float(poni_distance_cm) - expected_cm) / expected_cm * 100.0
        if mode == "nominal_range":
            errors.append(
                f"{alias}: PONI distance {poni_distance_cm:.3f} cm is outside "
                f"allowed range {lower:.3f}-{upper:.3f} cm for nominal "
                f"container distance {expected_cm:.3f} cm "
                f"({deviation_percent:.1f}% from nominal)"
            )
        else:
            tolerance_percent = _configured_tolerance_percent(validation_config)
            errors.append(
                f"{alias}: PONI distance {poni_distance_cm:.3f} cm does not match "
                f"container distance {expected_cm:.3f} cm "
                f"({deviation_percent:.1f}% > {float(tolerance_percent):.1f}%)"
            )

    return errors
