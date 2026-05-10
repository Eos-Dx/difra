"""PyQt5 compatibility exports for local Qt migration worktrees."""

from __future__ import annotations

try:
    from PyQt5 import QtCore, QtGui, QtTest, QtWidgets, sip
    from PyQt5.QtCore import (  # noqa: F401
        QByteArray,
        QEvent,
        QObject,
        QPointF,
        QRectF,
        QSettings,
        QSize,
        Qt,
        QThread,
        QTimer,
        pyqtSignal,
        pyqtSlot,
        qInstallMessageHandler,
    )
    from PyQt5.QtCore import QtMsgType  # noqa: F401
    from PyQt5.QtGui import (  # noqa: F401
        QBrush,
        QColor,
        QFont,
        QIcon,
        QImage,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
    )
    from PyQt5.QtTest import QTest  # noqa: F401
    from PyQt5.QtWidgets import (  # noqa: F401
        QAction,
        QActionGroup,
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDockWidget,
        QDoubleSpinBox,
        QFileDialog,
        QGraphicsEllipseItem,
        QGraphicsItem,
        QGraphicsLineItem,
        QGraphicsRectItem,
        QGraphicsScene,
        QGraphicsView,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QSizePolicy,
        QTabWidget,
        QTableWidget,
        QTableWidgetItem,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    raise

for _module in (QtCore, QtGui, QtWidgets, QtTest):
    for _name in dir(_module):
        if not _name.startswith("_"):
            globals().setdefault(_name, getattr(_module, _name))

QT_API = "PyQt5"
DIALOG_ACCEPTED = QDialog.Accepted
DIALOG_REJECTED = QDialog.Rejected


def exec_dialog(dialog):
    return dialog.exec_()


def exec_app(app):
    return app.exec_()
