from __future__ import annotations

from difra.gui.qt_compat import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)


def build_auto_poni_review_dialog(
    *,
    parent,
    canvas,
    aliases,
    cols: int,
    first_ring_by_alias: dict,
    max_ring_index: int,
    status: dict,
    on_selected_ring,
    on_rotation_constraint,
    on_delete_last_point,
    on_draw_all_integrations,
    on_correct,
):
    dialog = QDialog(parent)
    dialog.setWindowTitle("Auto PONI Review")
    dialog.setModal(True)
    layout = QVBoxLayout(dialog)
    layout.addWidget(canvas)

    note = QLabel(dialog)
    note.setWordWrap(True)
    note.setText(
        "Validate saves generated PONI files and updates the active technical container. "
        "Correct opens pyFAI-calib2 for manual refinement. "
        "Left-click an AgBh image to add a point on the selected first ring; drag points to move them. "
        "Right-click a point to delete it; right-click empty image space to set a center hint. "
        "Drag the first-ring vertical line in cake/radial plots, then release to shift the first-ring radius. "
        "Use mouse wheel to zoom around cursor; double-click to reset zoom."
    )
    layout.addWidget(note)

    clicked_status = QLabel(dialog)
    clicked_status.setWordWrap(True)
    clicked_status.setText("Clicked ring points: none")
    status["label"] = clicked_status
    layout.addWidget(clicked_status)

    fixed_rotations_check = QCheckBox("Fix rotations (SAXS constrained)", dialog)
    fixed_rotations_check.setChecked(True)
    fixed_rotations_check.setToolTip(
        "Keep Rot1/Rot2/Rot3 fixed and pass --no-tilt to pyFAI-calib2. "
        "Uncheck to allow pyFAI-calib2 to refine detector tilt."
    )
    fixed_rotations_check.toggled.connect(on_rotation_constraint)
    layout.addWidget(fixed_rotations_check)

    ring_row = QHBoxLayout()
    ring_row.addWidget(QLabel("Selected ring", dialog))
    for alias in aliases:
        alias_key = str(alias)
        ring_row.addWidget(QLabel(alias_key, dialog))
        ring_spin = QSpinBox(dialog)
        ring_spin.setRange(1, int(max_ring_index))
        ring_spin.setValue(int(first_ring_by_alias.get(alias_key, 1) or 1))
        ring_spin.setToolTip("Ring index used for newly clicked AgBh points.")
        ring_spin.valueChanged.connect(
            lambda value, a=alias_key: on_selected_ring(a, int(value))
        )
        ring_row.addWidget(ring_spin)
    layout.addLayout(ring_row)

    buttons = QDialogButtonBox(dialog)
    delete_btn = buttons.addButton("Delete last point", QDialogButtonBox.ActionRole)
    integrate_btn = buttons.addButton("Compute integrations", QDialogButtonBox.ActionRole)
    validate_btn = buttons.addButton("Validate", QDialogButtonBox.AcceptRole)
    correct_btn = buttons.addButton("Correct", QDialogButtonBox.ActionRole)
    cancel_btn = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
    decision = {"value": "cancel"}

    def _validate():
        decision["value"] = "validate"
        dialog.accept()

    def _correct():
        decision["value"] = "correct"
        on_correct()
        dialog.accept()

    validate_btn.clicked.connect(_validate)
    delete_btn.clicked.connect(on_delete_last_point)
    integrate_btn.clicked.connect(on_draw_all_integrations)
    correct_btn.clicked.connect(_correct)
    cancel_btn.clicked.connect(dialog.reject)
    layout.addWidget(buttons)

    dialog.resize(max(900, 560 * int(cols)), 980)
    return dialog, decision
