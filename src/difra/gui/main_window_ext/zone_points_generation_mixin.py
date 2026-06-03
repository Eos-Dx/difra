"""Main zone points extension functionality."""

import logging
from typing import Any, List, Optional, Tuple

from difra.gui.qt_compat import Qt
from difra.gui.qt_compat import (
    QDockWidget,
    QHBoxLayout,
    QMessageBox,
    QSplitter,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)


from .points.zone_geometry import compute_ideal_radius, farthest_point_sampling
from .points.zone_geometry import sample_points_along_polyline
from .points.zone_points_constants import ZonePointsConstants
from .points.zone_points_renderer import ZonePointsRenderer
from .points.zone_points_ui_builder import ZonePointsGeometry, ZonePointsUIBuilder

logger = logging.getLogger(__name__)


class ZonePointsGenerationMixin:
    """Zone points behavior split from ZonePointsMixin."""

    def create_zone_points_widget(self):
        """Create the zone points widget with all UI components."""
        self._initialize_state()

        self.zonePointsDock = QDockWidget("Zone Points", self)
        self.zonePointsDock.setObjectName("ZonePointsDock")
        self.zonePointsDock.setAllowedAreas(Qt.AllDockWidgetAreas)
        self.zonePointsDock.setFeatures(
            QDockWidget.DockWidgetClosable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
        )
        container = QWidget()

        # Set smaller font for all controls to fit smaller screens
        try:
            from difra.gui.qt_compat import QFont

            control_font = QFont()
            control_font.setPointSize(9)  # Smaller font for controls (menu-size)
            container.setFont(control_font)
        except Exception:
            pass

        layout = QVBoxLayout(container)
        # Tighten margins/spacing to reduce vertical footprint
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # Create UI components using helper classes (packed into a compact bar)
        controls_layout = self._create_all_controls()
        try:
            controls_layout.setContentsMargins(0, 0, 0, 0)
            controls_layout.setSpacing(8)
        except Exception:
            pass
        from difra.gui.qt_compat import QSizePolicy

        controls_bar = QWidget()
        controls_bar.setLayout(controls_layout)
        controls_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        try:
            controls_bar.setMaximumHeight(36)
        except Exception:
            pass
        layout.addWidget(controls_bar)

        # Splitter with left table and right measurements panel
        splitter = QSplitter(Qt.Horizontal)

        # Left: points table
        self.pointsTable = ZonePointsUIBuilder.create_points_table(self)
        splitter.addWidget(self.pointsTable)

        # Right: measurements tree (collapsible sections per point)
        self.measurementsTree = QTreeWidget()
        self.measurementsTree.setColumnCount(1)
        self.measurementsTree.setHeaderLabels(["Point"])
        self.measurementsTree.setExpandsOnDoubleClick(True)
        splitter.addWidget(self.measurementsTree)

        layout.addWidget(splitter)

        self._setup_event_handlers()

        container.setLayout(layout)
        self.zonePointsDock.setWidget(container)

        # Set minimum height to be compact - just enough for toolbar and a few table rows
        # This gives more vertical space to other zones (image view, etc.)
        try:
            self.zonePointsDock.setMinimumHeight(150)
        except Exception:
            pass

        self.addDockWidget(Qt.BottomDockWidgetArea, self.zonePointsDock)
        try:
            splitter.setStretchFactor(0, 4)
            splitter.setStretchFactor(1, 2)
        except Exception:
            pass

    def _initialize_state(self):
        """Initialize required state attributes."""
        if not hasattr(self, "next_point_id"):
            self.next_point_id = 1
        if not hasattr(self, "measurement_widgets") or not isinstance(
            getattr(self, "measurement_widgets", None), dict
        ):
            self.measurement_widgets = {}
        # Hidden parking parent to keep widgets alive when detaching from table
        if not hasattr(self, "_widgets_parking") or self._widgets_parking is None:
            self._widgets_parking = QWidget()
            self._widgets_parking.hide()
        # Mapping for tree items per point
        if not hasattr(self, "_measurement_items"):
            self._measurement_items = {}
        if not hasattr(self, "include_center"):
            self.include_center = (0, 0)
        if not hasattr(self, "pixel_to_mm_ratio"):
            self.pixel_to_mm_ratio = 1.0
        if not hasattr(self.image_view, "points_dict"):
            self.image_view.points_dict = {
                "generated": {"points": [], "zones": []},
                "user": {"points": [], "zones": []},
            }

    def _create_all_controls(self) -> QHBoxLayout:
        """Create all control layouts in a single horizontal layout."""
        layout = QHBoxLayout()
        try:
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(12)
        except Exception:
            pass

        # Point count and shrink controls
        controls = ZonePointsUIBuilder.create_controls_layout(self)
        layout.addLayout(controls)

        # Coordinate controls
        coord_controls = ZonePointsUIBuilder.create_coordinate_controls(self)
        layout.addLayout(coord_controls)

        # Action buttons
        button_controls = ZonePointsUIBuilder.create_action_buttons(self)
        layout.addLayout(button_controls)
        layout.addStretch(1)

        return layout

    def _setup_event_handlers(self):
        """Set up all event handlers for the UI components."""
        self.generatePointsBtn.clicked.connect(self.generate_zone_points)
        self.pointsTable.selectionModel().selectionChanged.connect(
            self.on_points_table_selection
        )
        self.pointsTable.itemChanged.connect(self._handle_points_table_item_changed)
        self.pointsTable.installEventFilter(self)
        self.pointsTable.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pointsTable.customContextMenuRequested.connect(
            self._show_points_table_context_menu
        )
        if hasattr(self, "refresh_stage_limit_overlays"):
            if hasattr(self, "real_x_pos_mm") and self.real_x_pos_mm is not None:
                self.real_x_pos_mm.valueChanged.connect(
                    lambda _value: self.refresh_stage_limit_overlays()
                )
            if hasattr(self, "real_y_pos_mm") and self.real_y_pos_mm is not None:
                self.real_y_pos_mm.valueChanged.connect(
                    lambda _value: self.refresh_stage_limit_overlays()
                )
        update_rotation_ui = getattr(self, "_update_sample_photo_rotation_ui", None)
        if callable(update_rotation_ui):
            update_rotation_ui()

    def update_conversion_label(self):
        self.conversionLabel.setText(f"Conversion: {self.pixel_to_mm_ratio:.2f} px/mm")

    def generate_zone_points(self):
        """Main method to generate zone points."""
        self._reset_point_counter()

        # Get parameters from UI
        n_points = self.pointCountSpinBox.value()
        shrink_percent = self.shrinkSpinBox.value()
        shrink_factor = (100 - shrink_percent) / 100.0
        # Keep generated point centers visually away from the include border.
        edge_clearance_px = float(ZonePointsConstants.POINT_RADIUS)

        # Get shapes for inclusion and exclusion
        include_shape, exclude_shapes = self._get_inclusion_exclusion_shapes()
        if include_shape is None:
            logger.info("No include shape defined. Cannot generate points.")
            return

        profile_points = self._get_active_profile_vertices()
        if profile_points:
            final_points = sample_points_along_polyline(profile_points, n_points)
            final_points = [
                (float(x_value), float(y_value))
                for x_value, y_value in final_points
                if self._point_within_allowed_region(float(x_value), float(y_value))
            ]
            if len(final_points) != int(n_points):
                QMessageBox.warning(
                    self,
                    "Profile Outside Allowed Region",
                    "Some profile points fall outside the allowed include zone.\n"
                    "Adjust the path or holder circle and try again.",
                )
                return
            ideal_radius = float(ZonePointsConstants.POINT_RADIUS)
            self._clear_generated_points()
            self._render_generated_points(final_points, ideal_radius)
            self.update_points_table()
            return

        # Generate candidate points
        candidates, area = self._generate_candidate_points(
            include_shape, exclude_shapes, shrink_factor, edge_clearance_px
        )
        if not candidates:
            logger.info("No candidate points found in allowed region.")
            return

        # Sample final points and compute ideal radius
        final_points = farthest_point_sampling(candidates, n_points)
        ideal_radius = compute_ideal_radius(
            area * len(candidates) / ZonePointsConstants.MAX_CANDIDATES,
            n_points,
        )

        # Clear existing generated points and render new ones
        self._clear_generated_points()
        self._render_generated_points(final_points, ideal_radius)

        self.update_points_table()

    def _toggle_profile_draw_mode(self, checked: bool):
        if not hasattr(self, "image_view"):
            return
        setter = getattr(self.image_view, "set_drawing_mode", None)
        if not callable(setter):
            return
        setter("profile" if checked else None)

    def _clear_profile_paths(self):
        image_view = getattr(self, "image_view", None)
        if image_view is None:
            return
        for profile_info in list(getattr(image_view, "profile_paths", []) or []):
            item = profile_info.get("item")
            if item is not None:
                try:
                    image_view.scene.removeItem(item)
                except Exception:
                    pass
        image_view.profile_paths = []
        draw_btn = getattr(self, "drawProfileBtn", None)
        if draw_btn is not None:
            try:
                draw_btn.setChecked(False)
            except Exception:
                pass
        setter = getattr(image_view, "set_drawing_mode", None)
        if callable(setter):
            setter(None)
        try:
            image_view.scene.update()
        except Exception:
            pass

    def _get_active_profile_vertices(self) -> List[Tuple[float, float]]:
        image_view = getattr(self, "image_view", None)
        if image_view is None:
            return []
        profiles = list(getattr(image_view, "profile_paths", []) or [])
        if not profiles:
            return []
        profile_info = profiles[-1]
        points = profile_info.get("points") or []
        vertices: List[Tuple[float, float]] = []
        for point in points:
            if point is None or len(point) < 2:
                continue
            vertices.append((float(point[0]), float(point[1])))
        return vertices

    def _reset_point_counter(self):
        """Reset the point ID counter."""
        if not hasattr(self, "next_point_id"):
            self.next_point_id = 1
        else:
            self.next_point_id = 1

    def _get_inclusion_exclusion_shapes(
        self,
    ) -> Tuple[Optional[Any], List[Any]]:
        """Get inclusion and exclusion shapes from the image view."""
        include_shape = None
        exclude_shapes = []
        holder_circle_shape = None

        for shape in self.image_view.shapes:
            role = shape.get("role", "include")
            if (
                role in ("holder circle", "sample holder")
                and holder_circle_shape is None
            ):
                holder_circle_shape = shape["item"]
            elif role == "include" and include_shape is None:
                include_shape = shape["item"]
            elif role == "exclude":
                exclude_shapes.append(shape["item"])

        # Explicit include zone must win. Holder circle is only a fallback
        # when the operator has not drawn a dedicated include region.
        if include_shape is None and holder_circle_shape is not None:
            include_shape = holder_circle_shape
        return include_shape, exclude_shapes

    def _generate_candidate_points(
        self,
        include_shape,
        exclude_shapes: List,
        shrink_factor: float,
        edge_clearance_px: float = 0.0,
    ) -> Tuple[List[Tuple[float, float]], float]:
        """Generate and filter candidate points based on shapes."""
        # Get initial candidates and area using geometry helper
        candidates, area, bounds = ZonePointsGeometry.get_shape_bounds_and_candidates(
            include_shape,
            shrink_factor,
            edge_clearance_px=edge_clearance_px,
        )

        # Filter candidates by inclusion/exclusion shapes
        filtered_candidates = ZonePointsGeometry.filter_candidates_by_shapes(
            candidates, include_shape, exclude_shapes
        )

        return filtered_candidates, area

    def _clear_generated_points(self):
        """Clear all existing generated points and zones from the scene."""
        for item in self.image_view.points_dict["generated"]["points"]:
            self.safe_remove_item(item)
        for item in self.image_view.points_dict["generated"]["zones"]:
            self.safe_remove_item(item)

        self.image_view.points_dict["generated"]["points"].clear()
        self.image_view.points_dict["generated"]["zones"].clear()

    def _render_generated_points(
        self, points: List[Tuple[float, float]], ideal_radius: float
    ):
        """Render the generated points and zones on the scene."""
        for x, y in points:
            # Create and add zone (background circle)
            zone_item = ZonePointsRenderer.create_zone_item(x, y, ideal_radius)
            self.image_view.scene.addItem(zone_item)
            self.image_view.points_dict["generated"]["zones"].append(zone_item)

            # Create and add point (foreground dot)
            point_id = self.next_point_id
            point_uid = self._new_point_uid(point_id)
            point_item = ZonePointsRenderer.create_point_item(
                x, y, point_id, "generated", point_uid=point_uid
            )
            self.next_point_id += 1
            self.image_view.scene.addItem(point_item)
            self.image_view.points_dict["generated"]["points"].append(point_item)

    # --- Table and selection methods remain as before, with attribute checks as needed ---
    def update_coordinates(self):
        self.update_points_table()

    def safe_remove_item(self, item):
        try:
            if item in self.image_view.scene.items():
                self.image_view.scene.removeItem(item)
        except Exception as e:
            logger.warning("Error removing item from scene: %s", e, exc_info=True)
