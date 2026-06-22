from __future__ import annotations

import math
import numpy as np
from matplotlib.patches import Circle

from difra.gui.technical.auto_poni_review_helpers import (
    integrate_with_poni,
    ring_positions_deg,
    rings_to_show_for_alias,
    snap_to_peak,
)
from difra.gui.technical.pyfai_calibration import (
    build_agbh_ring_overlays,
    parse_poni_parameters,
    pixel_size_m,
)


class AutoPoniReviewRenderer:
    def __init__(
        self,
        *,
        rings_to_show,
        axis_to_alias,
        image_data_by_alias,
        detector_state_by_alias,
        first_ring_by_alias,
        auto_points_by_alias,
        review_state_by_alias,
        base_review_by_alias,
        top_axes_by_alias,
        cake_axes_by_alias,
        curve_axes_by_alias,
        overlay_artists_by_alias,
        integration_line_artists_by_alias,
        integration_axis_to_alias,
        full_view_by_alias,
    ):
        self.rings_to_show = rings_to_show
        self.axis_to_alias = axis_to_alias
        self.image_data_by_alias = image_data_by_alias
        self.detector_state_by_alias = detector_state_by_alias
        self.first_ring_by_alias = first_ring_by_alias
        self.auto_points_by_alias = auto_points_by_alias
        self.review_state_by_alias = review_state_by_alias
        self.base_review_by_alias = base_review_by_alias
        self.top_axes_by_alias = top_axes_by_alias
        self.cake_axes_by_alias = cake_axes_by_alias
        self.curve_axes_by_alias = curve_axes_by_alias
        self.overlay_artists_by_alias = overlay_artists_by_alias
        self.integration_line_artists_by_alias = integration_line_artists_by_alias
        self.integration_axis_to_alias = integration_axis_to_alias
        self.full_view_by_alias = full_view_by_alias

    def _rings_to_show_for_alias(self, alias: str) -> int:
        return rings_to_show_for_alias(self.rings_to_show, alias)

    def center_marker_payload(self, alias: str):
        review = self.review_state_by_alias.get(alias)
        if review is None:
            return None
        params = parse_poni_parameters(str(getattr(review, "poni_text", "") or ""))
        detector_config = self.detector_state_by_alias.get(alias, {})
        detector_payload = params.get("Detector_config")
        if isinstance(detector_payload, dict):
            try:
                pixel1 = float(detector_payload.get("pixel1"))
                pixel2 = float(detector_payload.get("pixel2"))
            except (TypeError, ValueError):
                pixel1, pixel2 = pixel_size_m(detector_config)
        else:
            pixel1, pixel2 = pixel_size_m(detector_config)
        if pixel1 <= 0.0 or pixel2 <= 0.0:
            return None
        try:
            row_px = float(params.get("Poni1", 0.0)) / pixel1
            col_px = float(params.get("Poni2", 0.0)) / pixel2
        except (TypeError, ValueError):
            return None
        distance = float(params.get("Distance", 0.0) or 0.0)
        rot1 = float(params.get("Rot1", 0.0) or 0.0)
        rot2 = float(params.get("Rot2", 0.0) or 0.0)
        rot3 = float(params.get("Rot3", 0.0) or 0.0)
        return {
            "row_px": row_px,
            "col_px": col_px,
            "text": (
                f"center: col {col_px:.1f}, row {row_px:.1f}\n"
                f"dist: {distance * 100.0:.3f} cm\n"
                f"rot: {rot1:.3g}, {rot2:.3g}, {rot3:.3g}"
            ),
        }

    def draw_center_marker(self, alias: str):
        ax = self.top_axes_by_alias.get(alias)
        if ax is None:
            return
        payload = self.center_marker_payload(alias)
        if not payload:
            return
        marker = ax.plot(
            [float(payload["col_px"])],
            [float(payload["row_px"])],
            marker="o",
            markersize=6.5,
            markerfacecolor="#ff2a2a",
            markeredgecolor="#ffffff",
            markeredgewidth=0.9,
            linestyle="None",
            zorder=8,
        )[0]
        label = ax.text(
            0.98,
            0.98,
            str(payload["text"]),
            transform=ax.transAxes,
            va="top",
            ha="right",
            fontsize=8.2,
            color="#ff2a2a",
            bbox=dict(
                facecolor=(0, 0, 0, 0.58),
                edgecolor="#ff2a2a",
                linewidth=0.7,
            ),
            zorder=9,
        )
        self.overlay_artists_by_alias.setdefault(alias, []).extend([marker, label])

    def expanded_image_view(self, alias: str, width: float, height: float):
        margin = max(8.0, min(float(width), float(height)) * 0.05)
        min_col = 0.0
        max_col = float(width)
        min_row = 0.0
        max_row = float(height)
        payload = self.center_marker_payload(alias)
        if payload:
            center_col = float(payload["col_px"])
            center_row = float(payload["row_px"])
            min_col = min(min_col, center_col - margin)
            max_col = max(max_col, center_col + margin)
            min_row = min(min_row, center_row - margin)
            max_row = max(max_row, center_row + margin)
        return (min_col, max_col, min_row, max_row)

    def ensure_center_visible(self, alias: str):
        ax = self.top_axes_by_alias.get(alias)
        data = self.image_data_by_alias.get(alias)
        payload = self.center_marker_payload(alias)
        if ax is None or data is None or not payload:
            return
        height, width = np.asarray(data).shape
        full_view = self.expanded_image_view(alias, float(width), float(height))
        self.full_view_by_alias[alias] = full_view
        center_col = float(payload["col_px"])
        center_row = float(payload["row_px"])
        margin = max(8.0, min(float(width), float(height)) * 0.05)
        x_left, x_right = ax.get_xlim()
        y_bottom, y_top = ax.get_ylim()
        next_left = min(float(x_left), center_col - margin)
        next_right = max(float(x_right), center_col + margin)
        next_bottom = min(float(y_bottom), center_row - margin)
        next_top = max(float(y_top), center_row + margin)
        if (
            center_col < min(x_left, x_right)
            or center_col > max(x_left, x_right)
            or center_row < min(y_bottom, y_top)
            or center_row > max(y_bottom, y_top)
        ):
            ax.set_xlim(next_left, next_right)
            ax.set_ylim(next_bottom, next_top)

    def draw_auto_points(self, alias: str):
        ax = self.top_axes_by_alias.get(alias)
        if ax is None:
            return
        first_ring = int(self.first_ring_by_alias.get(alias, 1) or 1)
        for entry in self.auto_points_by_alias.get(alias, []) or []:
            ring_index = int(entry.get("ring_index", first_ring))
            points = list(entry.get("points", []) or [])
            if not points:
                continue
            cols_px = [float(col) for col, _row in points]
            rows_px = [float(row) for _col, row in points]
            artist = ax.plot(
                cols_px,
                rows_px,
                marker=".",
                markersize=4.2,
                markerfacecolor="#35d0ff" if ring_index == first_ring else "#f9f871",
                markeredgecolor="#101010",
                markeredgewidth=0.25,
                linestyle="None",
                alpha=0.92,
                zorder=7,
            )[0]
            self.overlay_artists_by_alias.setdefault(alias, []).append(artist)

    def auto_points_for_review(self, alias: str, review):
        data = self.image_data_by_alias.get(alias)
        if data is None:
            return []
        height, width = np.asarray(data).shape
        first_ring = int(self.first_ring_by_alias.get(alias, 1) or 1)
        overlays = build_agbh_ring_overlays(
            poni_text=str(getattr(review, "poni_text", "") or ""),
            detector_config=self.detector_state_by_alias.get(alias, {}),
            first_visible_ring=first_ring,
            rings_to_show=self._rings_to_show_for_alias(alias),
        )

        result = []
        for overlay in overlays:
            ring_index = int(overlay["ring_index"])
            radius = float(overlay["radius_px"])
            center_col = float(overlay["center_col_px"])
            center_row = float(overlay["center_row_px"])
            point_count = 72 if ring_index == first_ring else 48
            points = []
            seen = set()
            for idx in range(point_count):
                angle = 2.0 * math.pi * float(idx) / float(point_count)
                col = center_col + radius * math.cos(angle)
                row = center_row + radius * math.sin(angle)
                if not (0.0 <= col < float(width) and 0.0 <= row < float(height)):
                    continue
                snapped_col, snapped_row = snap_to_peak(data, col, row, radius=5)
                if not (
                    0.0 <= snapped_col < float(width)
                    and 0.0 <= snapped_row < float(height)
                ):
                    continue
                key = (int(round(snapped_col)), int(round(snapped_row)))
                if key in seen:
                    continue
                seen.add(key)
                points.append((float(snapped_col), float(snapped_row)))
            if points:
                result.append({"ring_index": ring_index, "points": points})
        return result

    def draw_ring_overlays(self, alias: str):
        ax = self.top_axes_by_alias.get(alias)
        review = self.review_state_by_alias.get(alias)
        if ax is None or review is None:
            return
        for artist in self.overlay_artists_by_alias.get(alias, []):
            try:
                artist.remove()
            except Exception:
                pass
        self.overlay_artists_by_alias[alias] = []
        first_ring = int(self.first_ring_by_alias.get(alias, 1) or 1)
        overlays = build_agbh_ring_overlays(
            poni_text=str(getattr(review, "poni_text", "") or ""),
            detector_config=self.detector_state_by_alias.get(alias, {}),
            first_visible_ring=first_ring,
            rings_to_show=self._rings_to_show_for_alias(alias),
        )
        for overlay in overlays:
            self._draw_single_ring_overlay(ax, alias, overlay, first_ring)
        self.draw_auto_points(alias)
        self.ensure_center_visible(alias)
        self.draw_center_marker(alias)

    def _draw_single_ring_overlay(self, ax, alias: str, overlay: dict, first_ring: int):
        ring_index = int(overlay["ring_index"])
        circle = Circle(
            (
                float(overlay["center_col_px"]),
                float(overlay["center_row_px"]),
            ),
            float(overlay["radius_px"]),
            fill=False,
            linewidth=1.15 if ring_index == first_ring else 0.85,
            edgecolor="#35d0ff" if ring_index == first_ring else "#f9f871",
            alpha=0.95 if ring_index == first_ring else 0.78,
        )
        ax.add_patch(circle)
        self.overlay_artists_by_alias[alias].append(circle)
        if ring_index != first_ring:
            return
        label = ax.text(
            0.02,
            0.98,
            f"first visible ring: {ring_index}",
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=8.5,
            color="#35d0ff",
            bbox=dict(
                facecolor=(0, 0, 0, 0.55),
                edgecolor="#35d0ff",
                linewidth=0.7,
            ),
        )
        self.overlay_artists_by_alias[alias].append(label)

    def draw_integrations(self, alias: str):
        cake_ax = self.cake_axes_by_alias.get(alias)
        curve_ax = self.curve_axes_by_alias.get(alias)
        review = self.review_state_by_alias.get(alias)
        data = self.image_data_by_alias.get(alias)
        if cake_ax is None or curve_ax is None or review is None or data is None:
            return
        self.integration_line_artists_by_alias[alias] = []
        self.integration_axis_to_alias[cake_ax] = alias
        self.integration_axis_to_alias[curve_ax] = alias
        first_ring = int(self.first_ring_by_alias.get(alias, 1) or 1)
        ring_positions = ring_positions_deg(
            poni_text=str(getattr(review, "poni_text", "") or ""),
            first_ring=first_ring,
            count=self._rings_to_show_for_alias(alias),
        )
        cake_ax.clear()
        curve_ax.clear()
        cake, curve = integrate_with_poni(review, data)
        self._draw_cake_plot(cake_ax, alias, cake, ring_positions, first_ring)
        self._draw_curve_plot(curve_ax, alias, curve, ring_positions, first_ring)

    def _draw_cake_plot(self, cake_ax, alias: str, cake, ring_positions, first_ring: int):
        if cake is None:
            cake_ax.set_title(f"{alias} cake unavailable")
            cake_ax.axis("off")
            return
        cake_data = np.asarray(cake.intensity, dtype=float)
        cake_display = np.log1p(np.clip(cake_data, a_min=0.0, a_max=None))
        radial = np.asarray(cake.radial, dtype=float)
        azimuthal = np.asarray(cake.azimuthal, dtype=float)
        cake_ax.imshow(
            cake_display,
            origin="lower",
            aspect="auto",
            cmap="magma",
            extent=(
                float(np.nanmin(radial)),
                float(np.nanmax(radial)),
                float(np.nanmin(azimuthal)),
                float(np.nanmax(azimuthal)),
            ),
        )
        cake_ax.set_title(f"{alias} cake")
        cake_ax.set_xlabel("2theta (deg)")
        cake_ax.set_ylabel("azimuth (deg)")
        for ring_index, two_theta_deg in ring_positions:
            line = cake_ax.axvline(
                two_theta_deg,
                color="#35d0ff" if ring_index == first_ring else "#f9f871",
                linewidth=1.45 if ring_index == first_ring else 0.75,
                alpha=0.9,
            )
            if ring_index == first_ring:
                self.integration_line_artists_by_alias.setdefault(alias, []).append(line)

    def _draw_curve_plot(self, curve_ax, alias: str, curve, ring_positions, first_ring: int):
        if curve is None:
            curve_ax.set_title(f"{alias} radial integration unavailable")
            curve_ax.axis("off")
            return
        radial = np.asarray(curve.radial, dtype=float)
        intensity = np.asarray(curve.intensity, dtype=float)
        curve_ax.plot(radial, intensity, color="#35d0ff", linewidth=1.0)
        curve_ax.set_yscale("log")
        curve_ax.set_title(f"{alias} radial integration")
        curve_ax.set_xlabel("2theta (deg)")
        curve_ax.set_ylabel("I")
        for ring_index, two_theta_deg in ring_positions:
            line = curve_ax.axvline(
                two_theta_deg,
                color="#35d0ff" if ring_index == first_ring else "#f9f871",
                linewidth=1.45 if ring_index == first_ring else 0.75,
                alpha=0.9,
            )
            if ring_index == first_ring:
                self.integration_line_artists_by_alias.setdefault(alias, []).append(line)
            curve_ax.text(
                two_theta_deg,
                0.96,
                str(ring_index),
                transform=curve_ax.get_xaxis_transform(),
                va="top",
                ha="center",
                fontsize=8,
                color="#35d0ff" if ring_index == first_ring else "#f9f871",
            )

    def initialize_alias_panels(
        self,
        *,
        aliases,
        axes,
        review_by_alias: dict,
        images_by_alias: dict,
        detector_config_by_alias: dict,
        first_visible_ring_by_alias: dict,
    ):
        for col_index, alias in enumerate(aliases):
            self._initialize_alias_panel(
                alias=alias,
                ax=axes[0, col_index],
                cake_ax=axes[1, col_index],
                curve_ax=axes[2, col_index],
                review_by_alias=review_by_alias,
                images_by_alias=images_by_alias,
                detector_config_by_alias=detector_config_by_alias,
                first_visible_ring_by_alias=first_visible_ring_by_alias,
            )

    def _initialize_alias_panel(
        self,
        *,
        alias: str,
        ax,
        cake_ax,
        curve_ax,
        review_by_alias: dict,
        images_by_alias: dict,
        detector_config_by_alias: dict,
        first_visible_ring_by_alias: dict,
    ):
        alias_key = str(alias).upper()
        review = review_by_alias.get(alias) or review_by_alias.get(alias_key)
        if review is None:
            return
        detector_config = (
            detector_config_by_alias.get(alias)
            or detector_config_by_alias.get(alias_key)
            or {}
        )
        image = images_by_alias.get(alias) if isinstance(images_by_alias, dict) else None
        if image is None and isinstance(images_by_alias, dict):
            image = images_by_alias.get(alias_key)
        try:
            data = np.asarray(image, dtype=float)
            if data.ndim != 2:
                raise ValueError("non-2d")
        except Exception:
            data = np.zeros((256, 256), dtype=float)

        display = np.log1p(np.clip(data, a_min=0.0, a_max=None))
        height, width = display.shape
        ax.imshow(
            display,
            origin="lower",
            cmap="magma",
            aspect="equal",
            extent=(0.0, float(width), 0.0, float(height)),
        )
        ax.set_title(f"{alias} AgBh")
        ax.set_xlabel("col (px)")
        ax.set_ylabel("row (px)")
        ax.set_facecolor("black")

        first_ring = int(first_visible_ring_by_alias.get(alias_key, 1) or 1)
        self.axis_to_alias[ax] = alias
        self.top_axes_by_alias[alias] = ax
        self.cake_axes_by_alias[alias] = cake_ax
        self.curve_axes_by_alias[alias] = curve_ax
        self.image_data_by_alias[alias] = data
        self.detector_state_by_alias[alias] = detector_config
        self.first_ring_by_alias[alias] = first_ring
        self.auto_points_by_alias[alias] = []
        self.overlay_artists_by_alias[alias] = []
        self.review_state_by_alias[alias] = review
        self.base_review_by_alias[alias] = review

        overlays = build_agbh_ring_overlays(
            poni_text=str(getattr(review, "poni_text", "") or ""),
            detector_config=detector_config,
            first_visible_ring=first_ring,
            rings_to_show=self._rings_to_show_for_alias(alias),
        )
        for overlay in overlays:
            self._draw_single_ring_overlay(ax, alias, overlay, first_ring)
        self.draw_center_marker(alias)

        full_view = self.expanded_image_view(alias, float(width), float(height))
        ax.set_xlim(full_view[0], full_view[1])
        ax.set_ylim(full_view[2], full_view[3])
        self.full_view_by_alias[alias] = full_view

        cake_ax.set_title(f"{alias} cake not computed")
        cake_ax.axis("off")
        curve_ax.set_title(f"{alias} radial integration not computed")
        curve_ax.axis("off")
