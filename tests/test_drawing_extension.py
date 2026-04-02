from types import SimpleNamespace

from difra.gui.main_window_ext import drawing_extension as module


def test_select_rect_mode_is_blocked_until_sample_photo_rotated():
    modes = []
    checked = []
    owner = SimpleNamespace(
        image_view=SimpleNamespace(set_drawing_mode=lambda mode: modes.append(mode)),
        select_act=SimpleNamespace(setChecked=lambda value: checked.append(bool(value))),
        _ensure_sample_photo_ready_for_workspace_editing=lambda **kwargs: False,
    )

    module.DrawingMixin.select_rect_mode(owner)

    assert checked == [True]
    assert modes == [None]


def test_select_rect_mode_starts_drawing_when_rotation_is_ready():
    modes = []
    owner = SimpleNamespace(
        image_view=SimpleNamespace(set_drawing_mode=lambda mode: modes.append(mode)),
        select_act=SimpleNamespace(setChecked=lambda value: None),
        _ensure_sample_photo_ready_for_workspace_editing=lambda **kwargs: True,
    )

    module.DrawingMixin.select_rect_mode(owner)

    assert modes == ["rect"]
