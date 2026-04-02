"""Resizable calibration shapes with drag handles for user-controlled sizing."""

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QPen
from PyQt5.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsRectItem


class ResizeHandle(QGraphicsEllipseItem):
    """Small draggable handle for resizing zones."""

    HANDLE_SIZE = 14

    def __init__(self, parent_zone, position: str):
        """
        Create a resize handle.

        Args:
            parent_zone: The ResizableZoneItem this handle belongs to
            position: 'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW'
        """
        super().__init__(-self.HANDLE_SIZE / 2, -self.HANDLE_SIZE / 2,
                          self.HANDLE_SIZE, self.HANDLE_SIZE)
        self.parent_zone = parent_zone
        self.position = position

        # Visual styling
        self.setBrush(QBrush(QColor(255, 255, 255, 200)))
        self.setPen(QPen(QColor(0, 0, 0), 1))

        # Make it draggable
        self.setFlags(
            QGraphicsItem.ItemIgnoresTransformations
        )
        self.setCursor(self._get_cursor())
        self.setZValue(1000)  # Always on top
        self._restore_parent_movable = None

    def _get_cursor(self):
        """Get appropriate cursor for handle position."""
        cursors = {
            'N': Qt.SizeVerCursor,
            'S': Qt.SizeVerCursor,
            'E': Qt.SizeHorCursor,
            'W': Qt.SizeHorCursor,
            'NE': Qt.SizeBDiagCursor,
            'SW': Qt.SizeBDiagCursor,
            'NW': Qt.SizeFDiagCursor,
            'SE': Qt.SizeFDiagCursor,
        }
        return cursors.get(self.position, Qt.SizeAllCursor)

    def itemChange(self, change, value):
        """Handle item position changes to resize parent zone."""
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            if bool(getattr(self.parent_zone, "_updating_handles", False)):
                return super().itemChange(change, value)
            # Get the new position in scene coordinates
            new_pos = value
            self.parent_zone.resize_from_handle(self.position, new_pos)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        parent = getattr(self, "parent_zone", None)
        if parent is not None:
            try:
                self._restore_parent_movable = bool(
                    parent.flags() & QGraphicsItem.ItemIsMovable
                )
                parent.setFlag(QGraphicsItem.ItemIsMovable, False)
            except Exception:
                self._restore_parent_movable = None
        event.accept()

    def mouseMoveEvent(self, event):
        parent = getattr(self, "parent_zone", None)
        if parent is not None:
            try:
                new_pos = parent.mapFromScene(event.scenePos())
            except Exception:
                new_pos = event.pos()
            parent.resize_from_handle(self.position, new_pos)
        event.accept()

    def mouseReleaseEvent(self, event):
        parent = getattr(self, "parent_zone", None)
        if parent is not None and self._restore_parent_movable is not None:
            try:
                parent.setFlag(QGraphicsItem.ItemIsMovable, self._restore_parent_movable)
            except Exception:
                pass
        self._restore_parent_movable = None
        event.accept()


class ResizableEllipseItem(QGraphicsEllipseItem):
    """Editable ellipse with draggable handles used before holder-circle calibration."""

    def __init__(self, x: float, y: float, width: float, height: float):
        rect = QRectF(float(x), float(y), max(10.0, float(width)), max(10.0, float(height)))
        super().__init__(rect)
        self._handles = {}
        self._handles_visible = False
        self._updating_handles = False
        self.geometry_changed_callback = None
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self._create_handles()
        self._update_handle_positions()
        self._set_handles_visible(False)

    def _create_handles(self):
        for pos in ("N", "S", "E", "W", "NE", "NW", "SE", "SW"):
            handle = ResizeHandle(self, pos)
            handle.setParentItem(self)
            self._handles[pos] = handle

    def _update_handle_positions(self):
        self._updating_handles = True
        rect = self.rect()
        center = rect.center()
        left = rect.left()
        right = rect.right()
        top = rect.top()
        bottom = rect.bottom()
        self._handles["N"].setPos(center.x(), top)
        self._handles["S"].setPos(center.x(), bottom)
        self._handles["E"].setPos(right, center.y())
        self._handles["W"].setPos(left, center.y())
        self._handles["NE"].setPos(right, top)
        self._handles["NW"].setPos(left, top)
        self._handles["SE"].setPos(right, bottom)
        self._handles["SW"].setPos(left, bottom)
        self._updating_handles = False

    def _set_handles_visible(self, visible: bool):
        self._handles_visible = visible
        for handle in self._handles.values():
            handle.setVisible(bool(visible))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedChange:
            self._set_handles_visible(bool(value))
        elif change == QGraphicsItem.ItemPositionHasChanged:
            self._update_handle_positions()
            callback = getattr(self, "geometry_changed_callback", None)
            if callable(callback):
                callback()
        return super().itemChange(change, value)

    def resize_from_handle(self, handle_position: str, handle_scene_pos: QPointF):
        rect = QRectF(self.rect())
        min_size = 10.0

        if "W" in handle_position:
            rect.setLeft(min(handle_scene_pos.x(), rect.right() - min_size))
        if "E" in handle_position:
            rect.setRight(max(handle_scene_pos.x(), rect.left() + min_size))
        if "N" in handle_position:
            rect.setTop(min(handle_scene_pos.y(), rect.bottom() - min_size))
        if "S" in handle_position:
            rect.setBottom(max(handle_scene_pos.y(), rect.top() + min_size))

        self.setRect(rect.normalized())
        self._update_handle_positions()
        callback = getattr(self, "geometry_changed_callback", None)
        if callable(callback):
            callback()

    def hoverEnterEvent(self, event):
        self.setCursor(Qt.SizeAllCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._normalize_translation()

    def _normalize_translation(self):
        delta = self.pos()
        if abs(delta.x()) < 1e-9 and abs(delta.y()) < 1e-9:
            return
        rect = QRectF(self.rect())
        rect.translate(float(delta.x()), float(delta.y()))
        self.setRect(rect)
        self.setPos(0.0, 0.0)
        self._update_handle_positions()
        callback = getattr(self, "geometry_changed_callback", None)
        if callable(callback):
            callback()


class ResizableRectangleItem(QGraphicsRectItem):
    """Editable rectangle with draggable handles."""

    def __init__(self, x: float, y: float, width: float, height: float):
        rect = QRectF(float(x), float(y), max(10.0, float(width)), max(10.0, float(height)))
        super().__init__(rect)
        self._handles = {}
        self._handles_visible = False
        self._updating_handles = False
        self.geometry_changed_callback = None
        self.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self._create_handles()
        self._update_handle_positions()
        self._set_handles_visible(False)

    def _create_handles(self):
        for pos in ("N", "S", "E", "W", "NE", "NW", "SE", "SW"):
            handle = ResizeHandle(self, pos)
            handle.setParentItem(self)
            self._handles[pos] = handle

    def _update_handle_positions(self):
        self._updating_handles = True
        rect = self.rect()
        center = rect.center()
        left = rect.left()
        right = rect.right()
        top = rect.top()
        bottom = rect.bottom()
        self._handles["N"].setPos(center.x(), top)
        self._handles["S"].setPos(center.x(), bottom)
        self._handles["E"].setPos(right, center.y())
        self._handles["W"].setPos(left, center.y())
        self._handles["NE"].setPos(right, top)
        self._handles["NW"].setPos(left, top)
        self._handles["SE"].setPos(right, bottom)
        self._handles["SW"].setPos(left, bottom)
        self._updating_handles = False

    def _set_handles_visible(self, visible: bool):
        self._handles_visible = visible
        for handle in self._handles.values():
            handle.setVisible(bool(visible))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedChange:
            self._set_handles_visible(bool(value))
        elif change == QGraphicsItem.ItemPositionHasChanged:
            self._update_handle_positions()
            callback = getattr(self, "geometry_changed_callback", None)
            if callable(callback):
                callback()
        return super().itemChange(change, value)

    def resize_from_handle(self, handle_position: str, handle_scene_pos: QPointF):
        rect = QRectF(self.rect())
        min_size = 10.0

        if "W" in handle_position:
            rect.setLeft(min(handle_scene_pos.x(), rect.right() - min_size))
        if "E" in handle_position:
            rect.setRight(max(handle_scene_pos.x(), rect.left() + min_size))
        if "N" in handle_position:
            rect.setTop(min(handle_scene_pos.y(), rect.bottom() - min_size))
        if "S" in handle_position:
            rect.setBottom(max(handle_scene_pos.y(), rect.top() + min_size))

        self.setRect(rect.normalized())
        self._update_handle_positions()
        callback = getattr(self, "geometry_changed_callback", None)
        if callable(callback):
            callback()

    def hoverEnterEvent(self, event):
        self.setCursor(Qt.SizeAllCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._normalize_translation()

    def _normalize_translation(self):
        delta = self.pos()
        if abs(delta.x()) < 1e-9 and abs(delta.y()) < 1e-9:
            return
        rect = QRectF(self.rect())
        rect.translate(float(delta.x()), float(delta.y()))
        self.setRect(rect)
        self.setPos(0.0, 0.0)
        self._update_handle_positions()
        callback = getattr(self, "geometry_changed_callback", None)
        if callable(callback):
            callback()


class ResizableZoneItem(QGraphicsEllipseItem):
    """
    A zone (circle) that can be resized by dragging handles.

    The zone maintains its center position while the user drags the edge handles.
    """

    def __init__(self, center_x: float, center_y: float, radius: float):
        """
        Create a resizable zone item.

        Args:
            center_x: X coordinate of zone center
            center_y: Y coordinate of zone center
            radius: Initial radius of the zone
        """
        super().__init__(center_x - radius, center_y - radius, 2 * radius, 2 * radius)

        self._center_x = center_x
        self._center_y = center_y
        self._radius = radius
        self._handles = {}
        self._handles_visible = False
        self._updating_handles = False
        self.geometry_changed_callback = None

        # Store radius in Qt's data system for persistence (key = 99)
        self.setData(99, radius)

        # Make zone selectable and movable
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)

        # Create resize handles (but don't show them yet)
        self._create_handles()
        self._update_handle_positions()
        self._set_handles_visible(False)

    def _create_handles(self):
        """Create resize handles at cardinal and diagonal positions."""
        positions = ['N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW']
        for pos in positions:
            handle = ResizeHandle(self, pos)
            handle.setParentItem(self)
            self._handles[pos] = handle

    def _update_handle_positions(self):
        """Update handle positions based on current zone radius."""
        self._updating_handles = True
        r = self._radius
        center = QPointF(self._center_x, self._center_y)

        # Cardinal directions (N, S, E, W)
        self._handles['N'].setPos(center.x(), center.y() - r)
        self._handles['S'].setPos(center.x(), center.y() + r)
        self._handles['E'].setPos(center.x() + r, center.y())
        self._handles['W'].setPos(center.x() - r, center.y())

        # Diagonal directions (NE, NW, SE, SW)
        diag_offset = r / 1.414  # r / sqrt(2)
        self._handles['NE'].setPos(center.x() + diag_offset, center.y() - diag_offset)
        self._handles['NW'].setPos(center.x() - diag_offset, center.y() - diag_offset)
        self._handles['SE'].setPos(center.x() + diag_offset, center.y() + diag_offset)
        self._handles['SW'].setPos(center.x() - diag_offset, center.y() + diag_offset)
        self._updating_handles = False

    def _set_handles_visible(self, visible: bool):
        """Show or hide resize handles."""
        self._handles_visible = visible
        for handle in self._handles.values():
            handle.setVisible(visible)

    def itemChange(self, change, value):
        """Handle item state changes (selection, position)."""
        if change == QGraphicsItem.ItemSelectedChange:
            # Show handles when selected, hide when deselected
            self._set_handles_visible(bool(value))
        elif change == QGraphicsItem.ItemPositionHasChanged:
            # Update center when zone is moved
            pos = self.pos()
            self._center_x = pos.x() + self._radius
            self._center_y = pos.y() + self._radius
            self._update_handle_positions()
            callback = getattr(self, "geometry_changed_callback", None)
            if callable(callback):
                callback()
        return super().itemChange(change, value)

    def resize_from_handle(self, handle_position: str, handle_scene_pos: QPointF):
        """
        Resize the zone based on handle drag.

        Args:
            handle_position: Which handle is being dragged ('N', 'E', etc.)
            handle_scene_pos: New position of the handle in scene coordinates
        """
        # Calculate distance from center to handle
        center = QPointF(self._center_x, self._center_y)
        dx = handle_scene_pos.x() - center.x()
        dy = handle_scene_pos.y() - center.y()

        # New radius is the distance from center to handle
        new_radius = max(10, (dx ** 2 + dy ** 2) ** 0.5)  # Minimum radius of 10

        # Update radius and geometry
        self._radius = new_radius
        self.setRect(
            self._center_x - new_radius,
            self._center_y - new_radius,
            2 * new_radius,
            2 * new_radius
        )

        # Store updated radius persistently in Qt data (key = 99)
        self.setData(99, new_radius)

        # Update handle positions
        self._update_handle_positions()
        callback = getattr(self, "geometry_changed_callback", None)
        if callable(callback):
            callback()

    def get_radius(self) -> float:
        """Get the current radius of the zone."""
        return self._radius

    def get_center(self) -> tuple:
        """Get the center position (x, y) of the zone."""
        return (self._center_x, self._center_y)

    def set_radius(self, radius: float):
        """
        Set the zone radius programmatically.

        Args:
            radius: New radius value
        """
        self._radius = max(10, radius)
        self.setRect(
            self._center_x - self._radius,
            self._center_y - self._radius,
            2 * self._radius,
            2 * self._radius
        )
        # Store updated radius persistently in Qt data (key = 99)
        self.setData(99, self._radius)
        self._update_handle_positions()

    def hoverEnterEvent(self, event):
        """Change cursor when hovering over zone."""
        self.setCursor(Qt.SizeAllCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Reset cursor when leaving zone."""
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._normalize_translation()

    def _normalize_translation(self):
        delta = self.pos()
        if abs(delta.x()) < 1e-9 and abs(delta.y()) < 1e-9:
            return
        self._center_x += float(delta.x())
        self._center_y += float(delta.y())
        self.setRect(
            self._center_x - self._radius,
            self._center_y - self._radius,
            2 * self._radius,
            2 * self._radius
        )
        self.setPos(0.0, 0.0)
        self._update_handle_positions()
        callback = getattr(self, "geometry_changed_callback", None)
        if callable(callback):
            callback()


class ResizableSquareItem(QGraphicsRectItem):
    """Square calibration item with draggable handles."""

    def __init__(self, center_x: float, center_y: float, side: float):
        side = max(10.0, float(side))
        half = side / 2.0
        super().__init__(center_x - half, center_y - half, side, side)
        self._center_x = float(center_x)
        self._center_y = float(center_y)
        self._side = float(side)
        self._handles = {}
        self._handles_visible = False
        self._updating_handles = False
        self.geometry_changed_callback = None
        self.setData(99, self._side)
        self.setFlags(
            QGraphicsItem.ItemIsSelectable |
            QGraphicsItem.ItemIsMovable |
            QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self._create_handles()
        self._update_handle_positions()
        self._set_handles_visible(False)

    def _create_handles(self):
        for pos in ("N", "S", "E", "W", "NE", "NW", "SE", "SW"):
            handle = ResizeHandle(self, pos)
            handle.setParentItem(self)
            self._handles[pos] = handle

    def _update_handle_positions(self):
        self._updating_handles = True
        half = self._side / 2.0
        center = QPointF(self._center_x, self._center_y)
        self._handles["N"].setPos(center.x(), center.y() - half)
        self._handles["S"].setPos(center.x(), center.y() + half)
        self._handles["E"].setPos(center.x() + half, center.y())
        self._handles["W"].setPos(center.x() - half, center.y())
        self._handles["NE"].setPos(center.x() + half, center.y() - half)
        self._handles["NW"].setPos(center.x() - half, center.y() - half)
        self._handles["SE"].setPos(center.x() + half, center.y() + half)
        self._handles["SW"].setPos(center.x() - half, center.y() + half)
        self._updating_handles = False

    def _set_handles_visible(self, visible: bool):
        self._handles_visible = visible
        for handle in self._handles.values():
            handle.setVisible(visible)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedChange:
            self._set_handles_visible(bool(value))
        elif change == QGraphicsItem.ItemPositionHasChanged:
            pos = self.pos()
            self._center_x = pos.x() + self._side / 2.0
            self._center_y = pos.y() + self._side / 2.0
            self._update_handle_positions()
            callback = getattr(self, "geometry_changed_callback", None)
            if callable(callback):
                callback()
        return super().itemChange(change, value)

    def resize_from_handle(self, handle_position: str, handle_scene_pos: QPointF):
        center = QPointF(self._center_x, self._center_y)
        dx = abs(handle_scene_pos.x() - center.x())
        dy = abs(handle_scene_pos.y() - center.y())
        half = max(5.0, dx, dy)
        self._side = 2.0 * half
        self.setRect(
            self._center_x - half,
            self._center_y - half,
            self._side,
            self._side,
        )
        self.setData(99, self._side)
        self._update_handle_positions()
        callback = getattr(self, "geometry_changed_callback", None)
        if callable(callback):
            callback()

    def get_center(self) -> tuple:
        return (self._center_x, self._center_y)

    def get_side(self) -> float:
        return float(self._side)

    def set_side(self, side: float):
        self._side = max(10.0, float(side))
        half = self._side / 2.0
        self.setRect(
            self._center_x - half,
            self._center_y - half,
            self._side,
            self._side,
        )
        self.setData(99, self._side)
        self._update_handle_positions()
        callback = getattr(self, "geometry_changed_callback", None)
        if callable(callback):
            callback()

    def hoverEnterEvent(self, event):
        self.setCursor(Qt.SizeAllCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._normalize_translation()

    def _normalize_translation(self):
        delta = self.pos()
        if abs(delta.x()) < 1e-9 and abs(delta.y()) < 1e-9:
            return
        self._center_x += float(delta.x())
        self._center_y += float(delta.y())
        half = self._side / 2.0
        self.setRect(
            self._center_x - half,
            self._center_y - half,
            self._side,
            self._side,
        )
        self.setPos(0.0, 0.0)
        self._update_handle_positions()
        callback = getattr(self, "geometry_changed_callback", None)
        if callable(callback):
            callback()
