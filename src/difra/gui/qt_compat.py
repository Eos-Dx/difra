from __future__ import annotations

import os
from pathlib import Path

_REQUESTED_QT_API = os.environ.get("DIFRA_QT_API", "auto").strip().lower()
if _REQUESTED_QT_API not in {"auto", "pyqt5", "pyqt6"}:
    raise RuntimeError(
        "DIFRA_QT_API must be one of: auto, pyqt5, pyqt6 "
        f"(got {_REQUESTED_QT_API!r})"
    )


def _configure_pyqt6_plugins() -> None:
    _pyqt6_root = Path(QtCore.__file__).resolve().parent
    _pyqt6_plugins = _pyqt6_root / "Qt6" / "plugins"
    _pyqt6_platforms = _pyqt6_plugins / "platforms"
    if _pyqt6_platforms.exists():
        os.environ.setdefault("QT_PLUGIN_PATH", str(_pyqt6_plugins))
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(_pyqt6_platforms))


if _REQUESTED_QT_API == "pyqt5":
    from PyQt5 import QtCore, QtGui, QtTest, QtWidgets, uic
    from PyQt5 import sip

    QT_API = "PyQt5"
elif _REQUESTED_QT_API == "pyqt6":
    from PyQt6 import QtCore, QtGui, QtTest, QtWidgets, uic
    from PyQt6 import sip

    QT_API = "PyQt6"
    _configure_pyqt6_plugins()
else:
    try:
        from PyQt6 import QtCore, QtGui, QtTest, QtWidgets, uic
        from PyQt6 import sip

        QT_API = "PyQt6"
        _configure_pyqt6_plugins()
    except ImportError:
        from PyQt5 import QtCore, QtGui, QtTest, QtWidgets, uic
        from PyQt5 import sip

        QT_API = "PyQt5"


IS_QT6 = QT_API == "PyQt6"


class _QtProxy:
    _GROUPS = (
        "AlignmentFlag",
        "AspectRatioMode",
        "CheckState",
        "ContextMenuPolicy",
        "CursorShape",
        "DockWidgetArea",
        "FocusPolicy",
        "GlobalColor",
        "ItemDataRole",
        "ItemFlag",
        "Key",
        "MouseButton",
        "Orientation",
        "PenStyle",
        "TextInteractionFlag",
        "TransformationMode",
        "WindowType",
    )

    def __init__(self, qt):
        self._qt = qt

    def __getattr__(self, name: str):
        if hasattr(self._qt, name):
            return getattr(self._qt, name)
        for group_name in self._GROUPS:
            group = getattr(self._qt, group_name, None)
            if group is not None and hasattr(group, name):
                return getattr(group, name)
        raise AttributeError(name)


Qt = _QtProxy(QtCore.Qt)
Signal = QtCore.pyqtSignal
Slot = QtCore.pyqtSlot
Property = QtCore.pyqtProperty
pyqtSignal = Signal
pyqtSlot = Slot
pyqtProperty = Property


for _module in (QtCore, QtGui, QtWidgets, QtTest):
    for _name in dir(_module):
        if _name.startswith("_") or _name in globals():
            continue
        globals()[_name] = getattr(_module, _name)

QAction = getattr(QtGui, "QAction", getattr(QtWidgets, "QAction", None))


def _install_legacy_attr(cls, group_name: str, member_name: str) -> None:
    if hasattr(cls, member_name):
        return
    group = getattr(cls, group_name, None)
    if group is not None and hasattr(group, member_name):
        setattr(cls, member_name, getattr(group, member_name))


def _install_exec_alias(cls) -> None:
    if not hasattr(cls, "exec_") and hasattr(cls, "exec"):
        setattr(cls, "exec_", cls.exec)


for _cls in (
    QtWidgets.QApplication,
    QtWidgets.QDialog,
    QtWidgets.QInputDialog,
    QtWidgets.QMenu,
    QtWidgets.QMessageBox,
):
    _install_exec_alias(_cls)

for _member in ("Accepted", "Rejected"):
    _install_legacy_attr(QtWidgets.QDialog, "DialogCode", _member)

for _member in ("Ok", "Cancel", "Yes", "No", "Close", "Save", "Discard"):
    _install_legacy_attr(QtWidgets.QDialogButtonBox, "StandardButton", _member)
    _install_legacy_attr(QtWidgets.QMessageBox, "StandardButton", _member)

_LEGACY_CLASS_ATTRS = (
    (QtCore.QEvent, "Type", ("KeyPress",)),
    (
        QtWidgets.QDialogButtonBox,
        "ButtonRole",
        ("AcceptRole", "RejectRole", "ActionRole", "DestructiveRole", "HelpRole"),
    ),
    (
        QtWidgets.QMessageBox,
        "ButtonRole",
        ("AcceptRole", "RejectRole", "ActionRole", "YesRole", "NoRole"),
    ),
    (QtWidgets.QMessageBox, "Icon", ("Warning", "Question", "Information", "Critical")),
    (
        QtWidgets.QDockWidget,
        "DockWidgetFeature",
        ("DockWidgetClosable", "DockWidgetMovable", "DockWidgetFloatable"),
    ),
    (
        QtWidgets.QMainWindow,
        "DockOption",
        ("AnimatedDocks", "AllowTabbedDocks", "AllowNestedDocks"),
    ),
    (QtWidgets.QHeaderView, "ResizeMode", ("Stretch", "Interactive", "ResizeToContents", "Fixed")),
    (
        QtWidgets.QAbstractItemView,
        "EditTrigger",
        ("NoEditTriggers", "DoubleClicked", "SelectedClicked"),
    ),
    (
        QtWidgets.QAbstractItemView,
        "SelectionBehavior",
        ("SelectRows", "SelectItems", "SelectColumns"),
    ),
    (
        QtWidgets.QAbstractItemView,
        "SelectionMode",
        ("ExtendedSelection", "SingleSelection", "MultiSelection", "NoSelection"),
    ),
    (QtWidgets.QAbstractItemView, "ScrollMode", ("ScrollPerPixel", "ScrollPerItem")),
    (
        QtWidgets.QTableWidget,
        "EditTrigger",
        ("NoEditTriggers", "DoubleClicked", "SelectedClicked", "EditKeyPressed"),
    ),
    (QtWidgets.QSizePolicy, "Policy", ("Expanding", "Fixed", "Maximum", "Minimum", "Preferred")),
    (QtWidgets.QLineEdit, "EchoMode", ("Normal", "Password", "NoEcho", "PasswordEchoOnEdit")),
    (
        QtWidgets.QGraphicsItem,
        "GraphicsItemFlag",
        (
            "ItemIgnoresTransformations",
            "ItemIsMovable",
            "ItemIsSelectable",
            "ItemSendsGeometryChanges",
        ),
    ),
    (
        QtWidgets.QGraphicsItem,
        "GraphicsItemChange",
        ("ItemPositionChange", "ItemSelectedChange", "ItemPositionHasChanged"),
    ),
    (
        QtWidgets.QGraphicsEllipseItem,
        "GraphicsItemFlag",
        ("ItemIsMovable", "ItemIsSelectable"),
    ),
    (
        QtWidgets.QGraphicsRectItem,
        "GraphicsItemFlag",
        ("ItemIsMovable", "ItemIsSelectable"),
    ),
    (
        QtWidgets.QGraphicsLineItem,
        "GraphicsItemFlag",
        ("ItemIsMovable", "ItemIsSelectable"),
    ),
    (
        QtGui.QImage,
        "Format",
        ("Format_Grayscale8", "Format_RGB888", "Format_RGBA8888"),
    ),
    (QtGui.QPainter, "RenderHint", ("Antialiasing", "SmoothPixmapTransform")),
    (
        QtWidgets.QStyle,
        "ControlElement",
        ("CE_ItemViewItem",),
    ),
    (
        QtWidgets.QStyle,
        "SubElement",
        ("SE_ItemViewItemText",),
    ),
    (
        QtWidgets.QStyle,
        "StateFlag",
        ("State_Selected",),
    ),
)

for _cls, _group_name, _members in _LEGACY_CLASS_ATTRS:
    for _member in _members:
        _install_legacy_attr(_cls, _group_name, _member)


def _qt_member(group_name: str, member_name: str):
    group = getattr(QtCore.Qt, group_name, QtCore.Qt)
    return getattr(group, member_name)


def _header_member(group_name: str, member_name: str):
    group = getattr(QtWidgets.QHeaderView, group_name, QtWidgets.QHeaderView)
    return getattr(group, member_name)


TEXT_SELECTABLE_BY_MOUSE = _qt_member(
    "TextInteractionFlag",
    "TextSelectableByMouse",
)
HORIZONTAL = _qt_member("Orientation", "Horizontal")
USER_ROLE = _qt_member("ItemDataRole", "UserRole")
HEADER_RESIZE_TO_CONTENTS = _header_member("ResizeMode", "ResizeToContents")
DIALOG_ACCEPTED = getattr(
    getattr(QtWidgets.QDialog, "DialogCode", QtWidgets.QDialog),
    "Accepted",
)


def exec_app(app: QtWidgets.QApplication) -> int:
    return int(app.exec() if hasattr(app, "exec") else app.exec_())


def exec_dialog(dialog: QtWidgets.QDialog, *args):
    return dialog.exec(*args) if hasattr(dialog, "exec") else dialog.exec_(*args)
