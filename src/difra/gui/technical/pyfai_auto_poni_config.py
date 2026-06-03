"""Auto-PONI calibration configuration helpers."""

from __future__ import annotations

from typing import Mapping, Sequence

from difra.gui.technical.pyfai_calibration_common import (
    DEFAULT_CALIBRANT,
    DEFAULT_ENERGY_KEV,
)


def auto_poni_default_config() -> dict:
    return {
        "calibrant": DEFAULT_CALIBRANT,
        "energy_kev": DEFAULT_ENERGY_KEV,
        "first_visible_ring_by_alias": {
            "PRIMARY": 2,
            "SECONDARY": 5,
        },
        "first_visible_ring_by_distance_cm": {
            "2": {
                "PRIMARY": 2,
                "SECONDARY": 5,
            },
            "17": {
                "PRIMARY": 1,
                "SECONDARY": 1,
            },
            "18": {
                "PRIMARY": 1,
                "SECONDARY": 1,
            },
        },
        "rings_to_search_by_alias": {
            "PRIMARY": 3,
            "SECONDARY": 3,
        },
        "rings_to_search_by_distance_cm": {
            "2": {
                "PRIMARY": 3,
                "SECONDARY": 4,
            },
            "17": {
                "PRIMARY": 3,
                "SECONDARY": 3,
            },
            "18": {
                "PRIMARY": 3,
                "SECONDARY": 3,
            },
        },
        "rings_to_show": 3,
        "seed_distance_cm_by_distance_cm": {
            "2": {
                "PRIMARY": 2.30,
                "SECONDARY": 2.48,
            },
            "17": {
                "PRIMARY": 17.0,
                "SECONDARY": 17.0,
            },
        },
        "seed_center_px_by_alias": {
            "PRIMARY": [128.0, 10.0],
            "SECONDARY": [130.0, 306.0],
        },
    }


def normalized_auto_poni_config(config: Mapping | None) -> dict:
    cfg = config if isinstance(config, Mapping) else {}
    raw = cfg.get("auto_poni_calibration")
    if not isinstance(raw, Mapping):
        raw = cfg.get("auto_poni")
    raw = raw if isinstance(raw, Mapping) else {}
    defaults = auto_poni_default_config()
    first_visible = dict(defaults["first_visible_ring_by_alias"])
    configured = raw.get("first_visible_ring_by_alias")
    if isinstance(configured, Mapping):
        for alias, value in configured.items():
            try:
                ring = int(value)
            except (TypeError, ValueError):
                continue
            if ring > 0:
                first_visible[str(alias or "").strip().upper()] = ring
    by_distance = {
        str(distance_key or "").strip(): {
            str(alias or "").strip().upper(): int(ring)
            for alias, ring in rings.items()
            if str(alias or "").strip() and int(ring) > 0
        }
        for distance_key, rings in (
            defaults["first_visible_ring_by_distance_cm"].items()
        )
    }
    configured_by_distance = raw.get("first_visible_ring_by_distance_cm")
    if isinstance(configured_by_distance, Mapping):
        for distance_key, rings in configured_by_distance.items():
            if not isinstance(rings, Mapping):
                continue
            normalized_rings = {}
            for alias, value in rings.items():
                try:
                    ring = int(value)
                except (TypeError, ValueError):
                    continue
                alias_key = str(alias or "").strip().upper()
                if alias_key and ring > 0:
                    normalized_rings[alias_key] = ring
            if normalized_rings:
                by_distance[str(distance_key or "").strip()] = normalized_rings
    rings_by_alias = dict(defaults["rings_to_search_by_alias"])
    configured_rings_by_alias = raw.get("rings_to_search_by_alias")
    if isinstance(configured_rings_by_alias, Mapping):
        for alias, value in configured_rings_by_alias.items():
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            alias_key = str(alias or "").strip().upper()
            if alias_key and count > 0:
                rings_by_alias[alias_key] = count
    rings_by_distance = {
        str(distance_key or "").strip(): {
            str(alias or "").strip().upper(): int(count)
            for alias, count in rings.items()
            if str(alias or "").strip() and int(count) > 0
        }
        for distance_key, rings in (defaults["rings_to_search_by_distance_cm"].items())
    }
    configured_rings_by_distance = raw.get("rings_to_search_by_distance_cm")
    if isinstance(configured_rings_by_distance, Mapping):
        for distance_key, rings in configured_rings_by_distance.items():
            if not isinstance(rings, Mapping):
                continue
            normalized_counts = {}
            for alias, value in rings.items():
                try:
                    count = int(value)
                except (TypeError, ValueError):
                    continue
                alias_key = str(alias or "").strip().upper()
                if alias_key and count > 0:
                    normalized_counts[alias_key] = count
            if normalized_counts:
                rings_by_distance[str(distance_key or "").strip()] = normalized_counts
    seed_distance_by_distance = {
        str(distance_key or "").strip(): {
            str(alias or "").strip().upper(): float(distance_cm)
            for alias, distance_cm in distances.items()
            if str(alias or "").strip()
        }
        for distance_key, distances in (
            defaults["seed_distance_cm_by_distance_cm"].items()
        )
    }
    configured_seed_distance_by_distance = raw.get("seed_distance_cm_by_distance_cm")
    if isinstance(configured_seed_distance_by_distance, Mapping):
        for distance_key, distances in configured_seed_distance_by_distance.items():
            if not isinstance(distances, Mapping):
                continue
            normalized_distances = {}
            for alias, value in distances.items():
                try:
                    distance_cm = float(value)
                except (TypeError, ValueError):
                    continue
                alias_key = str(alias or "").strip().upper()
                if alias_key and distance_cm > 0.0:
                    normalized_distances[alias_key] = distance_cm
            if normalized_distances:
                seed_distance_by_distance[str(distance_key or "").strip()] = (
                    normalized_distances
                )
    seed_center_by_alias = {
        str(alias or "").strip().upper(): [float(values[0]), float(values[1])]
        for alias, values in defaults["seed_center_px_by_alias"].items()
    }
    configured_seed_center_by_alias = raw.get("seed_center_px_by_alias")
    if isinstance(configured_seed_center_by_alias, Mapping):
        for alias, value in configured_seed_center_by_alias.items():
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                continue
            if len(value) < 2:
                continue
            try:
                center = [float(value[0]), float(value[1])]
            except (TypeError, ValueError):
                continue
            alias_key = str(alias or "").strip().upper()
            if alias_key:
                seed_center_by_alias[alias_key] = center
    try:
        rings_to_show = int(raw.get("rings_to_show", defaults["rings_to_show"]))
    except (TypeError, ValueError):
        rings_to_show = defaults["rings_to_show"]
    energy_source = raw.get(
        "energy_kev",
        cfg.get("xray_energy_kev", cfg.get("beam_energy_kev", defaults["energy_kev"])),
    )
    try:
        energy_kev = float(energy_source)
    except (TypeError, ValueError):
        energy_kev = defaults["energy_kev"]
    return {
        "calibrant": str(raw.get("calibrant") or defaults["calibrant"]),
        "energy_kev": energy_kev,
        "first_visible_ring_by_alias": first_visible,
        "first_visible_ring_by_distance_cm": by_distance,
        "rings_to_search_by_alias": rings_by_alias,
        "rings_to_search_by_distance_cm": rings_by_distance,
        "rings_to_show": max(1, rings_to_show),
        "seed_distance_cm_by_distance_cm": seed_distance_by_distance,
        "seed_center_px_by_alias": seed_center_by_alias,
    }


def auto_poni_distance_key(distance_cm) -> str:
    try:
        value = float(distance_cm)
    except (TypeError, ValueError):
        return ""
    rounded = round(value)
    if abs(value - rounded) <= 0.55:
        return str(int(rounded))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def auto_poni_seed_distance_cm(
    auto_config: Mapping | None,
    *,
    alias: str,
    nominal_distance_cm,
) -> float | None:
    try:
        nominal = float(nominal_distance_cm)
    except (TypeError, ValueError):
        return None
    if nominal <= 0.0:
        return None
    cfg = auto_config if isinstance(auto_config, Mapping) else {}
    by_distance = cfg.get("seed_distance_cm_by_distance_cm", {})
    distance_key = auto_poni_distance_key(nominal)
    alias_key = str(alias or "").strip().upper()
    if isinstance(by_distance, Mapping):
        distance_rules = by_distance.get(distance_key, {})
        if isinstance(distance_rules, Mapping):
            try:
                value = float(distance_rules.get(alias_key))
            except (TypeError, ValueError):
                value = None
            if value is not None and value > 0.0:
                return value
    return nominal


def auto_poni_seed_center_px(
    auto_config: Mapping | None,
    *,
    alias: str,
) -> tuple[float, float] | None:
    cfg = auto_config if isinstance(auto_config, Mapping) else {}
    centers = cfg.get("seed_center_px_by_alias", {})
    if not isinstance(centers, Mapping):
        return None
    alias_key = str(alias or "").strip().upper()
    value = centers.get(alias_key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    if len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
