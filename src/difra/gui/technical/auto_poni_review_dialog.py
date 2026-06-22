from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from difra.gui.qt_compat import QDialog, QTimer
from difra.gui.technical.auto_poni_review_dialog_ui import (
    build_auto_poni_review_dialog,
)
from difra.gui.technical.auto_poni_review_renderer import AutoPoniReviewRenderer
from difra.gui.technical.auto_poni_review_geometry import AutoPoniReviewGeometry
from difra.gui.technical.auto_poni_review_events import AutoPoniReviewEventHandlers


def show_auto_poni_review_window(
    *,
    aliases,
    review_by_alias: dict,
    images_by_alias: dict,
    detector_config_by_alias: dict,
    first_visible_ring_by_alias: dict,
    rings_to_show: int = 8,
    parent=None,
):
    """Show AgBh heatmaps, cake plots, and 1D integration with ring markers."""
    from difra.gui.technical.pyfai_calibration import AGBH_D_SPACING_A

    aliases = [str(alias) for alias in aliases if str(alias or "").strip()]
    if not aliases:
        return {"decision": "cancel", "dialog": None}

    cols = len(aliases)

    fig = Figure(figsize=(5.2 * cols, 10.8))
    canvas = FigureCanvas(fig)
    axes = fig.subplots(3, cols, squeeze=False)
    if cols == 1:
        axes = np.asarray(axes).reshape(3, 1)
    axis_to_alias = {}
    image_data_by_alias = {}
    detector_state_by_alias = {}
    first_ring_by_alias = {}
    manual_points_by_alias = {}
    manual_artists_by_alias = {}
    auto_points_by_alias = {}
    review_state_by_alias = {}
    base_review_by_alias = {}
    top_axes_by_alias = {}
    cake_axes_by_alias = {}
    curve_axes_by_alias = {}
    overlay_artists_by_alias = {}
    integration_line_artists_by_alias = {}
    integration_axis_to_alias = {}
    full_view_by_alias = {}
    status = {"label": None, "last_alias": None}
    rotation_constraints = {"fixed": True}
    drag_state = {"alias": None, "index": None, "artist": None}
    profile_drag_state = {"alias": None, "x0": None, "x": None, "artists": []}

    renderer = AutoPoniReviewRenderer(
        rings_to_show=rings_to_show,
        axis_to_alias=axis_to_alias,
        image_data_by_alias=image_data_by_alias,
        detector_state_by_alias=detector_state_by_alias,
        first_ring_by_alias=first_ring_by_alias,
        auto_points_by_alias=auto_points_by_alias,
        review_state_by_alias=review_state_by_alias,
        base_review_by_alias=base_review_by_alias,
        top_axes_by_alias=top_axes_by_alias,
        cake_axes_by_alias=cake_axes_by_alias,
        curve_axes_by_alias=curve_axes_by_alias,
        overlay_artists_by_alias=overlay_artists_by_alias,
        integration_line_artists_by_alias=integration_line_artists_by_alias,
        integration_axis_to_alias=integration_axis_to_alias,
        full_view_by_alias=full_view_by_alias,
    )

    renderer.initialize_alias_panels(
        aliases=aliases,
        axes=axes,
        review_by_alias=review_by_alias,
        images_by_alias=images_by_alias,
        detector_config_by_alias=detector_config_by_alias,
        first_visible_ring_by_alias=first_visible_ring_by_alias,
    )

    geometry = AutoPoniReviewGeometry(
        aliases=aliases,
        review_by_alias=review_by_alias,
        detector_state_by_alias=detector_state_by_alias,
        first_ring_by_alias=first_ring_by_alias,
        manual_points_by_alias=manual_points_by_alias,
        auto_points_by_alias=auto_points_by_alias,
        review_state_by_alias=review_state_by_alias,
        base_review_by_alias=base_review_by_alias,
        rotation_constraints=rotation_constraints,
        renderer=renderer,
    )

    events = AutoPoniReviewEventHandlers(
        canvas=canvas,
        axis_to_alias=axis_to_alias,
        top_axes_by_alias=top_axes_by_alias,
        full_view_by_alias=full_view_by_alias,
        first_ring_by_alias=first_ring_by_alias,
        manual_points_by_alias=manual_points_by_alias,
        manual_artists_by_alias=manual_artists_by_alias,
        integration_axis_to_alias=integration_axis_to_alias,
        integration_line_artists_by_alias=integration_line_artists_by_alias,
        drag_state=drag_state,
        profile_drag_state=profile_drag_state,
        status=status,
        geometry=geometry,
    )

    fig.tight_layout()

    def _draw_all_integrations():
        events.set_status("Computing Auto PONI integrations...")
        for alias in aliases:
            renderer.draw_integrations(alias)
            canvas.draw_idle()
        events.set_status("Clicked ring points: none")

    for alias in aliases:
        review = review_state_by_alias.get(alias)
        if review is not None:
            auto_points_by_alias[alias] = renderer.auto_points_for_review(alias, review)
            renderer.draw_ring_overlays(alias)

    events.connect()

    def _set_rotation_constraint(checked):
        rotation_constraints["fixed"] = bool(checked)
        geometry.refresh_all_review_commands()
        events.set_status(
            "Rotations fixed (SAXS constrained)"
            if checked
            else "Rotations unlocked for pyFAI correction"
        )

    dialog, decision = build_auto_poni_review_dialog(
        parent=parent,
        canvas=canvas,
        aliases=aliases,
        cols=cols,
        first_ring_by_alias=first_ring_by_alias,
        max_ring_index=len(AGBH_D_SPACING_A),
        status=status,
        on_selected_ring=events.set_selected_ring,
        on_rotation_constraint=_set_rotation_constraint,
        on_delete_last_point=events.delete_last_point,
        on_draw_all_integrations=_draw_all_integrations,
        on_correct=geometry.refresh_all_review_commands,
    )
    QTimer.singleShot(0, _draw_all_integrations)
    result = dialog.exec_()
    if result != QDialog.Accepted:
        decision["value"] = "cancel"
    return {"decision": decision["value"], "dialog": dialog}
