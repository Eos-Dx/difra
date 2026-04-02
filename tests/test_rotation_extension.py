import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from difra.gui.main_window_ext.rotation_extension import RotatorToolButton


def test_rotator_tool_button_offers_fine_rotation_angles():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    button = RotatorToolButton("Rotate Left", 1, lambda angle: angle)

    angles = [action.data() for action in button.menu.actions()]

    assert angles == [0.05, 0.1, 0.5, 1, 2, 5, 10]
