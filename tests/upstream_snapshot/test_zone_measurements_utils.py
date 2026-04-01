from difra.gui.main_window_ext.zone_measurements.logic.utils import (
    ZoneMeasurementsUtilsMixin,
)


class _Spin:
    def __init__(self, value: float) -> None:
        self._value = float(value)

    def value(self) -> float:
        return self._value


class _DummyWindow(ZoneMeasurementsUtilsMixin):
    pass


def test_mm_to_pixels_returns_sentinel_if_reference_widgets_missing():
    win = _DummyWindow()
    win.pixel_to_mm_ratio = 1.0
    win.include_center = (0.0, 0.0)

    x, y = win.mm_to_pixels(1.0, 2.0)
    assert x == -1.0
    assert y == -1.0


def test_mm_to_pixels_converts_using_reference_position():
    win = _DummyWindow()
    win.real_x_pos_mm = _Spin(10.0)
    win.real_y_pos_mm = _Spin(20.0)
    win.pixel_to_mm_ratio = 2.0
    win.include_center = (5.0, 6.0)

    x, y = win.mm_to_pixels(8.0, 19.0)
    assert x == 9.0
    assert y == 8.0


def test_mm_to_pixels_uses_holder_center_when_rotation_confirmed():
    win = _DummyWindow()
    win.real_x_pos_mm = _Spin(10.0)
    win.real_y_pos_mm = _Spin(20.0)
    win.pixel_to_mm_ratio = 2.0
    win.include_center = (5.0, 6.0)
    win.sample_holder_center_px = (100.0, 200.0)
    win.sample_photo_rotation_confirmed = True
    win._use_sample_photo_rotated_mapping = lambda: True
    win._get_holder_center_px = lambda: (100.0, 200.0)
    win._get_sample_photo_beam_center_mm = lambda: (6.15, -9.15)

    x, y = win.mm_to_pixels(5.15, -10.15)
    assert x == 98.0
    assert y == 198.0
