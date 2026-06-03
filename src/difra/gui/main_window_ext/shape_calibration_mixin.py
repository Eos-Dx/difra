import logging
from math import ceil, floor, sqrt

from difra.gui.qt_compat import QPointF, QRectF, Qt
from difra.gui.qt_compat import QBrush, QColor, QImage, QPen, QPixmap
from difra.gui.qt_compat import (
    QColorDialog,
    QDialog,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGridLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from difra.gui.extra.resizable_zone import (
    ResizableEllipseItem,
    ResizableSquareItem,
    ResizableZoneItem,
)

from .shape_calibration_catch_auto_mixin import ShapeCatchAutoMixin
from .shape_calibration_geometry_mixin import ShapeCalibrationGeometryMixin
from .shape_calibration_rotation_mixin import ShapeCalibrationRotationMixin

logger = logging.getLogger(__name__)


class ShapeCalibrationMixin(
    ShapeCatchAutoMixin,
    ShapeCalibrationGeometryMixin,
    ShapeCalibrationRotationMixin,
):
    DEFAULT_CALIBRATION_SQUARE_SIDE_MM = 18.35
    DEFAULT_HOLDER_CIRCLE_DIAMETER_MM = 15.18
    DEFAULT_SAMPLE_HOLDER_LENGTH_MM = 65.45
    DEFAULT_LOAD_POSITION_MM = (-13.9, -6.0)
    DEFAULT_BEAM_CENTER_MM = (6.15, -9.15)
    ROLE_CALIBRATION_SQUARE = "calibration square"
    ROLE_HOLDER_CIRCLE = "holder circle"
    DEFAULT_CATCH_AUTO_HOLDER_RGB = (190, 165, 70)
    DEFAULT_CATCH_AUTO_BACKGROUND_RGB = (50, 110, 50)
