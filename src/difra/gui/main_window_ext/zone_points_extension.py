"""Main zone points extension functionality."""

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from difra.gui.qt_compat import sip
from difra.gui.qt_compat import QEvent, Qt
from difra.gui.qt_compat import QColor
from difra.gui.qt_compat import (
    QDockWidget,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QSplitter,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from difra.gui.technical.widgets import MeasurementHistoryWidget

from .points.zone_geometry import compute_ideal_radius, farthest_point_sampling
from .points.zone_geometry import sample_points_along_polyline
from .points.zone_points_constants import ZonePointsConstants
from .points.zone_points_renderer import ZonePointsRenderer, ZonePointsTableManager
from .points.zone_points_ui_builder import ZonePointsGeometry, ZonePointsUIBuilder
from . import zone_points_actions

from .zone_points_generation_mixin import ZonePointsGenerationMixin
from .zone_points_measurements_mixin import ZonePointsMeasurementsMixin
from .zone_points_skip_delete_mixin import ZonePointsSkipDeleteMixin
from .zone_points_table_mixin import ZonePointsTableMixin

logger = logging.getLogger(__name__)


class ZonePointsMixin(
    ZonePointsGenerationMixin,
    ZonePointsTableMixin,
    ZonePointsSkipDeleteMixin,
    ZonePointsMeasurementsMixin,
):
    """Mixin for zone-based point generation and management in a Qt GUI."""
