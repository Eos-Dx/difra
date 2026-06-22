from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from difra.gui.technical.auto_poni_review_helpers import (
    alias_file_token,
    command_with_npt,
    npt_path_from_command,
    points_by_ring,
    ring_positions_deg,
)
from difra.gui.technical.pyfai_calibration import (
    AGBH_D_SPACING_A,
    DEFAULT_WAVELENGTH_M,
    build_pyfai_calib2_command,
    build_seed_poni_text,
    parse_poni_parameters,
    pixel_size_m,
    refine_poni_from_clicked_ring_points,
    ring_two_theta_rad,
    write_agbh_clicked_points_npt,
    write_agbh_points_by_ring_npt,
)


class AutoPoniReviewGeometry:
    def __init__(
        self,
        *,
        aliases,
        review_by_alias: dict,
        detector_state_by_alias: dict,
        first_ring_by_alias: dict,
        manual_points_by_alias: dict,
        auto_points_by_alias: dict,
        review_state_by_alias: dict,
        base_review_by_alias: dict,
        rotation_constraints: dict,
        renderer,
    ):
        self.aliases = aliases
        self.review_by_alias = review_by_alias
        self.detector_state_by_alias = detector_state_by_alias
        self.first_ring_by_alias = first_ring_by_alias
        self.manual_points_by_alias = manual_points_by_alias
        self.auto_points_by_alias = auto_points_by_alias
        self.review_state_by_alias = review_state_by_alias
        self.base_review_by_alias = base_review_by_alias
        self.rotation_constraints = rotation_constraints
        self.renderer = renderer

    def command_for_review(self, alias: str, review):
        command = build_pyfai_calib2_command(
            image_path=review.image_path,
            poni_text=review.poni_text,
            detector_config=self.detector_state_by_alias.get(alias, {}),
            calibrant="AgBh",
            fix_rotations=bool(self.rotation_constraints.get("fixed", True)),
        )
        npt_path = npt_path_from_command(getattr(review, "command", []))
        if npt_path is not None:
            command = command_with_npt(command, npt_path)
        return command

    def refresh_review_command(self, alias: str):
        review = self.review_state_by_alias.get(alias)
        if review is None:
            return None
        updated = type(review)(
            image_path=review.image_path,
            poni_path=review.poni_path,
            command=self.command_for_review(alias, review),
            poni_text=review.poni_text,
            source_path=getattr(review, "source_path", None),
        )
        self.review_state_by_alias[alias] = updated
        self.review_by_alias[alias] = updated
        self.review_by_alias[str(alias).upper()] = updated
        return updated

    def refresh_all_review_commands(self):
        for alias in self.aliases:
            self.refresh_review_command(alias)

    def review_with_poni_text(self, alias: str, review, poni_text: str):
        output_dir = Path(getattr(review, "poni_path", "") or ".").parent
        poni_path = output_dir / f"{alias_file_token(alias)}.poni"
        poni_path.write_text(poni_text, encoding="utf-8")
        return type(review)(
            image_path=review.image_path,
            poni_path=poni_path,
            command=review.command,
            poni_text=poni_text,
            source_path=getattr(review, "source_path", None),
        )

    def poni_text_with_manual_hint(self, alias: str, review, points, ring_index: int):
        if not points:
            return str(getattr(review, "poni_text", "") or "")
        payload = self.renderer.center_marker_payload(alias)
        if not payload:
            return str(getattr(review, "poni_text", "") or "")

        params = parse_poni_parameters(str(getattr(review, "poni_text", "") or ""))
        wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
        d_spacing_index = min(len(AGBH_D_SPACING_A) - 1, max(0, int(ring_index) - 1))
        two_theta = ring_two_theta_rad(
            wavelength_m=wavelength_m,
            d_spacing_a=AGBH_D_SPACING_A[d_spacing_index],
        )
        if two_theta is None:
            return str(getattr(review, "poni_text", "") or "")
        center_col = float(payload["col_px"])
        center_row = float(payload["row_px"])
        if len(points) == 2:
            center_col, center_row = self._manual_hint_center_from_chord(
                center_col,
                center_row,
                points,
            )
        radii = [
            math.hypot(float(col) - center_col, float(row) - center_row)
            for col, row in points
        ]
        radii = [radius for radius in radii if math.isfinite(radius) and radius > 0.0]
        if not radii:
            return str(getattr(review, "poni_text", "") or "")
        pixel1, pixel2 = pixel_size_m(self.detector_state_by_alias.get(alias, {}))
        distance_m = (
            (sum(radii) / len(radii))
            * ((pixel1 + pixel2) / 2.0)
            / math.tan(two_theta)
        )
        if not math.isfinite(distance_m) or distance_m <= 0.0:
            return str(getattr(review, "poni_text", "") or "")

        return build_seed_poni_text(
            detector_config=self.detector_state_by_alias.get(alias, {}),
            distance_m=distance_m,
            alias=alias,
            existing_poni_text=str(getattr(review, "poni_text", "") or ""),
            wavelength_m=wavelength_m,
            center_px=(center_row, center_col),
        )

    @staticmethod
    def _manual_hint_center_from_chord(center_col: float, center_row: float, points):
        p1 = np.asarray(points[0], dtype=float)
        p2 = np.asarray(points[1], dtype=float)
        chord = p2 - p1
        chord_len2 = float(np.dot(chord, chord))
        if chord_len2 <= 0.0:
            return center_col, center_row
        midpoint = (p1 + p2) * 0.5
        center = np.asarray([center_col, center_row], dtype=float)
        center = center - (float(np.dot(center - midpoint, chord)) / chord_len2) * chord
        return float(center[0]), float(center[1])

    def finalize_review_geometry(
        self,
        alias: str,
        review,
        *,
        redraw_integrations: bool = True,
    ):
        points = self.manual_points_by_alias.get(alias) or []
        ring_index = int(self.first_ring_by_alias.get(alias, 1) or 1)
        output_dir = Path(getattr(review, "poni_path", "") or ".").parent
        npt_path = output_dir / f"{alias_file_token(alias)}.npt"
        auto_entries = self.renderer.auto_points_for_review(alias, review)
        self.auto_points_by_alias[alias] = auto_entries
        auto_points = points_by_ring(
            auto_entries,
            manual_ring_index=ring_index,
            manual_points=points,
        )
        if auto_points:
            write_agbh_points_by_ring_npt(
                poni_text=str(getattr(review, "poni_text", "") or ""),
                output_path=npt_path,
                points_by_ring=auto_points,
                calibrant="AgBh",
            )
        else:
            write_agbh_clicked_points_npt(
                poni_text=str(getattr(review, "poni_text", "") or ""),
                output_path=npt_path,
                ring_index=ring_index,
                points_col_row=points,
                calibrant="AgBh",
            )
        command = build_pyfai_calib2_command(
            image_path=review.image_path,
            poni_text=review.poni_text,
            detector_config=self.detector_state_by_alias.get(alias, {}),
            calibrant="AgBh",
            fix_rotations=bool(self.rotation_constraints.get("fixed", True)),
        )
        command = command_with_npt(command, npt_path)
        updated = type(review)(
            image_path=review.image_path,
            poni_path=review.poni_path,
            command=command,
            poni_text=review.poni_text,
            source_path=getattr(review, "source_path", None),
        )
        self.review_state_by_alias[alias] = updated
        self.review_by_alias[alias] = updated
        self.review_by_alias[str(alias).upper()] = updated
        self.renderer.draw_ring_overlays(alias)
        if redraw_integrations:
            self.renderer.draw_integrations(alias)
        return npt_path

    def save_clicked_points(self, alias: str):
        points = self.manual_points_by_alias.get(alias) or []
        review = self.review_state_by_alias.get(alias)
        if review is None:
            return None, False
        ring_index = int(self.first_ring_by_alias.get(alias, 1) or 1)
        refit = False
        if len(points) < 3:
            base_review = self.base_review_by_alias.get(alias)
            if base_review is not None:
                review = base_review
                self.review_state_by_alias[alias] = review
                self.review_by_alias[alias] = review
                self.review_by_alias[str(alias).upper()] = review
            if not points:
                npt_path = self.finalize_review_geometry(alias, review)
                return npt_path, False
        else:
            poni_text = refine_poni_from_clicked_ring_points(
                poni_text=str(getattr(review, "poni_text", "") or ""),
                detector_config=self.detector_state_by_alias.get(alias, {}),
                ring_index=ring_index,
                points_col_row=points,
                alias=alias,
            )
            review = self.review_with_poni_text(alias, review, poni_text)
            refit = True
        if points and not refit:
            hinted_poni_text = self.poni_text_with_manual_hint(
                alias,
                review,
                points,
                ring_index,
            )
            if hinted_poni_text != str(getattr(review, "poni_text", "") or ""):
                review = self.review_with_poni_text(alias, review, hinted_poni_text)
        npt_path = self.finalize_review_geometry(alias, review)
        return npt_path, refit

    def apply_center_hint(self, alias: str, col: float, row: float):
        review = self.review_state_by_alias.get(alias)
        if review is None:
            return None
        params = parse_poni_parameters(str(getattr(review, "poni_text", "") or ""))
        distance_m = float(params.get("Distance", 0.0) or 0.0)
        wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
        if distance_m <= 0.0:
            return None
        poni_text = build_seed_poni_text(
            detector_config=self.detector_state_by_alias.get(alias, {}),
            distance_m=distance_m,
            alias=alias,
            existing_poni_text=str(getattr(review, "poni_text", "") or ""),
            wavelength_m=wavelength_m,
            center_px=(float(row), float(col)),
        )
        updated = self.review_with_poni_text(alias, review, poni_text)
        self.base_review_by_alias[alias] = updated
        self.review_state_by_alias[alias] = updated
        if self.manual_points_by_alias.get(alias):
            return self.save_clicked_points(alias)[0]
        return self.finalize_review_geometry(alias, updated)

    def first_ring_two_theta_deg(self, alias: str):
        review = self.review_state_by_alias.get(alias)
        if review is None:
            return None
        first_ring = int(self.first_ring_by_alias.get(alias, 1) or 1)
        positions = ring_positions_deg(
            poni_text=str(getattr(review, "poni_text", "") or ""),
            first_ring=first_ring,
            count=1,
        )
        return float(positions[0][1]) if positions else None

    def apply_profile_shift(self, alias: str, target_two_theta_deg: float):
        review = self.review_state_by_alias.get(alias)
        source_two_theta_deg = self.first_ring_two_theta_deg(alias)
        if review is None or source_two_theta_deg is None:
            return None

        params = parse_poni_parameters(str(getattr(review, "poni_text", "") or ""))
        distance_m = float(params.get("Distance", 0.0) or 0.0)
        wavelength_m = float(params.get("Wavelength", DEFAULT_WAVELENGTH_M))
        source_rad = math.radians(float(source_two_theta_deg))
        target_rad = math.radians(float(target_two_theta_deg))
        if distance_m <= 0.0 or source_rad <= 0.0 or target_rad <= 0.0:
            return None
        new_distance = distance_m * math.tan(target_rad) / math.tan(source_rad)
        if not math.isfinite(new_distance) or new_distance <= 0.0:
            return None
        payload = self.renderer.center_marker_payload(alias)
        center_px = None
        if payload:
            center_px = (float(payload["row_px"]), float(payload["col_px"]))
        poni_text = build_seed_poni_text(
            detector_config=self.detector_state_by_alias.get(alias, {}),
            distance_m=new_distance,
            alias=alias,
            existing_poni_text=str(getattr(review, "poni_text", "") or ""),
            wavelength_m=wavelength_m,
            center_px=center_px,
        )
        updated = self.review_with_poni_text(alias, review, poni_text)
        self.base_review_by_alias[alias] = updated
        self.review_state_by_alias[alias] = updated
        if self.manual_points_by_alias.get(alias):
            return self.save_clicked_points(alias)[0]
        return self.finalize_review_geometry(alias, updated)
