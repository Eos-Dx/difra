"""PONI center preview and range-editing dialog."""

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from difra.gui.main_window_ext.technical.poni_center_preview import (
    rule_with_zone,
    resolve_overlay_zone,
    resolve_preview_limits,
)
from difra.gui.qt_compat import (
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)
_PONI_RANGE_EDIT_PASSWORD = "Ulster2026!"


def _resolve_poni_validation_config_target(parent) -> Optional[Path]:
    for attr_name in ("_active_config_path", "_global_path", "_legacy_main_path"):
        candidate = getattr(parent, attr_name, None)
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _load_json_payload(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_poni_validation_rule_edits(
    *,
    parent,
    validation_cfg: dict,
    edited_rules_by_alias: dict,
) -> Path:
    target_path = _resolve_poni_validation_config_target(parent)
    if target_path is None:
        raise RuntimeError("Active setup config file is not available.")

    payload = _load_json_payload(target_path)
    block = payload.get("poni_center_validation")
    if not isinstance(block, dict):
        block = dict(validation_cfg or {})
    detectors = block.get("detectors")
    if not isinstance(detectors, dict):
        detectors = {}
    for alias_key, rule in (edited_rules_by_alias or {}).items():
        detectors[str(alias_key).upper()] = dict(rule or {})
    block["detectors"] = detectors
    if "enabled" not in block:
        block["enabled"] = True
    payload["poni_center_validation"] = block
    target_path.write_text(json.dumps(payload, indent=4), encoding="utf-8")

    if parent is not None and hasattr(parent, "load_config"):
        try:
            parent.config = parent.load_config()
        except Exception:
            logger.warning(
                "Failed to reload config after PONI range edit", exc_info=True
            )
    elif parent is not None and hasattr(parent, "config"):
        parent.config = dict(getattr(parent, "config", {}) or {})
        parent.config["poni_center_validation"] = block

    return target_path


def show_poni_centers_preview_window(
    *,
    aliases,
    poni_by_alias: dict,
    detector_sizes_by_alias: dict,
    validation_cfg: dict,
    agbh_images_by_alias: Optional[dict] = None,
    decision_mode: bool = False,
    parent=None,
):
    """Show detector previews with PONI centers and allowed center zones."""
    from matplotlib.patches import Rectangle
    from matplotlib.widgets import RectangleSelector

    from difra.gui.main_window_ext.technical.poni_center_validation import (
        evaluate_poni_centers,
        parse_poni_center_px,
    )

    aliases = [str(a) for a in aliases if str(a or "").strip()]
    if not aliases:
        return None

    data_by_alias = (
        agbh_images_by_alias if isinstance(agbh_images_by_alias, dict) else {}
    )
    detector_rules = {}
    if isinstance(validation_cfg, dict):
        rules = validation_cfg.get("detectors", {})
        if isinstance(rules, dict):
            detector_rules = {str(k).upper(): v for k, v in rules.items()}
        defaults = validation_cfg.get("defaults", {})
        if not isinstance(defaults, dict):
            defaults = {}
    else:
        defaults = {}

    cols = len(aliases)
    fig = Figure(figsize=(4.5 * cols, 4.2))
    canvas = FigureCanvas(fig)
    axes = fig.subplots(1, cols)
    if cols == 1:
        axes = [axes]

    zone_patches = {}
    rules_by_alias = {}
    zones_by_alias = {}
    axes_by_alias = {}
    status_by_alias = {
        str(item.get("alias") or "").upper(): item
        for item in evaluate_poni_centers(
            poni_text_by_alias=poni_by_alias,
            detector_sizes_by_alias=detector_sizes_by_alias,
            validation_config=validation_cfg,
        )
    }

    for ax, alias in zip(axes, aliases):
        alias_key = str(alias).upper()
        axes_by_alias[alias_key] = ax
        size = (
            detector_sizes_by_alias.get(alias)
            or detector_sizes_by_alias.get(alias_key)
            or (256, 256)
        )
        try:
            width_px = int(size[0])
            height_px = int(size[1])
        except Exception:
            width_px, height_px = 256, 256

        raw_data = data_by_alias.get(alias)
        if raw_data is None:
            raw_data = data_by_alias.get(alias_key)
        if raw_data is None:
            img = np.zeros((height_px, width_px), dtype=float)
            source_label = "fake detector square"
        else:
            img = np.asarray(raw_data, dtype=float)
            if img.ndim != 2:
                img = np.zeros((height_px, width_px), dtype=float)
                source_label = "fake detector square"
            else:
                source_label = "AGBH"

        h, w = img.shape
        ax.imshow(
            img,
            origin="lower",
            cmap="gray",
            aspect="equal",
            extent=(0.0, float(w), 0.0, float(h)),
        )
        ax.set_title(f"{alias} ({source_label})")
        ax.set_xlabel("col (px)")
        ax.set_ylabel("row (px)")

        rule = {}
        if alias_key in detector_rules and isinstance(detector_rules[alias_key], dict):
            rule = dict(defaults)
            rule.update(detector_rules[alias_key])
        elif isinstance(defaults, dict):
            rule = dict(defaults)
        rules_by_alias[alias_key] = dict(rule)

        zone = resolve_overlay_zone(rule, w, h)
        zones_by_alias[alias_key] = zone
        if zone is not None:
            rect = Rectangle(
                (zone[0], zone[1]),
                zone[2],
                zone[3],
                facecolor=(0.58, 0.28, 0.78, 0.25),
                edgecolor=(0.58, 0.28, 0.78, 0.8),
                linewidth=1.5,
            )
            ax.add_patch(rect)
            zone_patches[alias_key] = rect

        poni_text = str(poni_by_alias.get(alias) or poni_by_alias.get(alias_key) or "")
        center = parse_poni_center_px(poni_text, fallback_detector_size=(w, h))
        if center is not None:
            ax.plot(
                [float(center["col_px"])],
                [float(center["row_px"])],
                marker="o",
                markersize=6,
                markerfacecolor="red",
                markeredgecolor="white",
                markeredgewidth=0.8,
            )

        status_info = status_by_alias.get(alias_key, {})
        if isinstance(status_info, dict):
            status_label = (
                "IN ZONE" if bool(status_info.get("in_zone")) else "OUT OF ZONE"
            )
            color = "#1b7f3b" if bool(status_info.get("in_zone")) else "#b42318"
            geometry = status_info.get("geometry") or {}
            row_text = geometry.get("row_px")
            col_text = geometry.get("col_px")
            status_lines = [status_label]
            if row_text is not None and col_text is not None:
                status_lines.append(
                    f"row={float(row_text):.2f}, col={float(col_text):.2f}"
                )
            summary = status_info.get("rule_summary") or []
            if summary:
                status_lines.append("; ".join(summary[:2]))
            ax.text(
                0.02,
                0.98,
                "\n".join(status_lines),
                transform=ax.transAxes,
                va="top",
                ha="left",
                fontsize=8.5,
                color=color,
                bbox=dict(facecolor=(1, 1, 1, 0.72), edgecolor=color, linewidth=0.8),
            )

        detector_frame = Rectangle(
            (0.0, 0.0),
            float(w),
            float(h),
            facecolor="none",
            edgecolor=(1.0, 1.0, 1.0, 0.55),
            linewidth=1.0,
            linestyle="--",
        )
        ax.add_patch(detector_frame)

        x_min, x_max, y_min, y_max = resolve_preview_limits(
            width_px=w,
            height_px=h,
            zone=zone,
            center=center,
        )
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min, y_max)

    fig.tight_layout()

    dialog = QDialog(parent)
    dialog.setWindowTitle("PONI Centers: PRIMARY/SECONDARY")
    if decision_mode:
        dialog.setModal(True)

    layout = QVBoxLayout(dialog)
    layout.addWidget(canvas)

    help_label = QLabel(dialog)
    help_label.setWordWrap(True)
    help_label.setStyleSheet("color: #555; font-size: 11px;")
    help_label.setText(
        "Purple rectangles show allowed PONI beam-center ranges. "
        "Use 'Unlock Editing…' to drag/resize them with the mouse; OK/Accept will save updates to the active setup config."
    )
    layout.addWidget(help_label)

    selectors = {}
    editing_enabled = {"value": False}

    def _apply_selector_style(selector):
        artist = getattr(selector, "_selection_artist", None)
        if artist is not None:
            artist.set_facecolor((0.58, 0.28, 0.78, 0.25))
            artist.set_edgecolor((0.58, 0.28, 0.78, 0.9))
            artist.set_linewidth(1.6)
        handles = getattr(selector, "_corner_handles", None)
        if handles is not None:
            try:
                handles.artist.set_markerfacecolor((0.58, 0.28, 0.78, 0.95))
                handles.artist.set_markeredgecolor("white")
            except Exception:
                pass

    def _selector_for_alias(alias_key: str):
        selector = selectors.get(alias_key)
        if selector is not None:
            return selector
        ax = axes_by_alias.get(alias_key)
        zone = zones_by_alias.get(alias_key)
        if ax is None or zone is None:
            return None

        x0, y0, zone_w, zone_h = zone
        selector_kwargs = dict(
            useblit=False,
            button=[1],
            interactive=True,
            minspanx=1.0,
            minspany=1.0,
            spancoords="data",
        )
        try:
            selector = RectangleSelector(
                ax,
                lambda *_args, **_kwargs: None,
                drag_from_anywhere=True,
                props=dict(
                    facecolor=(0.58, 0.28, 0.78, 0.25),
                    edgecolor=(0.58, 0.28, 0.78, 0.9),
                    linewidth=1.6,
                ),
                **selector_kwargs,
            )
        except TypeError:
            try:
                selector = RectangleSelector(
                    ax,
                    lambda *_args, **_kwargs: None,
                    rectprops=dict(
                        facecolor=(0.58, 0.28, 0.78, 0.25),
                        edgecolor=(0.58, 0.28, 0.78, 0.9),
                        linewidth=1.6,
                    ),
                    **selector_kwargs,
                )
            except TypeError:
                selector = RectangleSelector(
                    ax,
                    lambda *_args, **_kwargs: None,
                    **selector_kwargs,
                )
                try:
                    selector.drag_from_anywhere = True
                except Exception:
                    pass

        selector.extents = (x0, x0 + zone_w, y0, y0 + zone_h)
        _apply_selector_style(selector)
        selectors[alias_key] = selector
        return selector

    def _unlock_editing():
        if editing_enabled["value"]:
            return
        password, ok = QInputDialog.getText(
            dialog,
            "Unlock PONI Range Editing",
            "Enter password to edit allowed PONI ranges:",
            QLineEdit.Password,
        )
        if not ok:
            return
        if str(password) != _PONI_RANGE_EDIT_PASSWORD:
            QMessageBox.warning(dialog, "Wrong Password", "Password is incorrect.")
            return
        for alias_key, patch in list(zone_patches.items()):
            if patch is not None:
                patch.set_visible(False)
            _selector_for_alias(alias_key)
        editing_enabled["value"] = True
        help_label.setText(
            "Editing unlocked. Drag inside a rectangle to move it, or drag its edges/corners to resize it. "
            "Click OK/Accept to save the updated ranges to the active setup config."
        )
        canvas.draw_idle()

    def _save_current_edits() -> bool:
        if not editing_enabled["value"]:
            return True
        edited_rules_by_alias = {}
        for alias_key, selector in selectors.items():
            try:
                x1, x2, y1, y2 = selector.extents
            except Exception:
                continue
            zone = (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
            edited_rules_by_alias[alias_key] = rule_with_zone(
                rules_by_alias.get(alias_key, {}),
                zone,
            )
        if not edited_rules_by_alias:
            return True
        try:
            target_path = _save_poni_validation_rule_edits(
                parent=parent,
                validation_cfg=validation_cfg,
                edited_rules_by_alias=edited_rules_by_alias,
            )
        except Exception as exc:
            QMessageBox.warning(
                dialog,
                "Save Failed",
                f"Could not update PONI range config:\n{exc}",
            )
            return False
        QMessageBox.information(
            dialog,
            "PONI Ranges Saved",
            f"Updated PONI range rules in:\n{target_path}",
        )
        return True

    if decision_mode:
        decision_buttons = QDialogButtonBox(dialog)
        unlock_btn = decision_buttons.addButton(
            "Unlock Editing…", QDialogButtonBox.ActionRole
        )
        accept_btn = decision_buttons.addButton("Accept", QDialogButtonBox.AcceptRole)
        reject_btn = decision_buttons.addButton("Reject", QDialogButtonBox.RejectRole)
        unlock_btn.clicked.connect(_unlock_editing)

        def _accept_and_maybe_save():
            if _save_current_edits():
                dialog.accept()

        accept_btn.clicked.connect(_accept_and_maybe_save)
        reject_btn.clicked.connect(dialog.reject)
        layout.addWidget(decision_buttons)
    else:
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog
        )
        unlock_btn = buttons.addButton("Unlock Editing…", QDialogButtonBox.ActionRole)
        unlock_btn.clicked.connect(_unlock_editing)

        def _ok_and_maybe_save():
            if _save_current_edits():
                dialog.accept()

        buttons.accepted.connect(_ok_and_maybe_save)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

    dialog.resize(max(640, 460 * cols), 420)
    dialog._poni_zone_selectors = selectors
    if decision_mode:
        result = dialog.exec_()
        return {"dialog": dialog, "accepted": bool(result == QDialog.Accepted)}

    dialog.show()
    return dialog
