from __future__ import annotations

import numpy as np


class AutoPoniReviewEventHandlers:
    def __init__(
        self,
        *,
        canvas,
        axis_to_alias,
        top_axes_by_alias,
        full_view_by_alias,
        first_ring_by_alias,
        manual_points_by_alias,
        manual_artists_by_alias,
        integration_axis_to_alias,
        integration_line_artists_by_alias,
        drag_state,
        profile_drag_state,
        status,
        geometry,
    ):
        self.canvas = canvas
        self.axis_to_alias = axis_to_alias
        self.top_axes_by_alias = top_axes_by_alias
        self.full_view_by_alias = full_view_by_alias
        self.first_ring_by_alias = first_ring_by_alias
        self.manual_points_by_alias = manual_points_by_alias
        self.manual_artists_by_alias = manual_artists_by_alias
        self.integration_axis_to_alias = integration_axis_to_alias
        self.integration_line_artists_by_alias = integration_line_artists_by_alias
        self.drag_state = drag_state
        self.profile_drag_state = profile_drag_state
        self.status = status
        self.geometry = geometry

    def connect(self):
        self.canvas.mpl_connect("button_press_event", self.on_click)
        self.canvas.mpl_connect("motion_notify_event", self.on_motion)
        self.canvas.mpl_connect("button_release_event", self.on_release)
        self.canvas.mpl_connect("scroll_event", self.on_scroll)

    def set_status(self, text: str):
        label = self.status.get("label")
        if label is not None:
            label.setText(text)

    def start_profile_drag(self, event):
        alias = self.integration_axis_to_alias.get(event.inaxes)
        if not alias or event.xdata is None or event.button != 1:
            return False
        x0 = self.geometry.first_ring_two_theta_deg(alias)
        if x0 is None:
            return False
        x_left, x_right = event.inaxes.get_xlim()
        tolerance = max(0.03, abs(float(x_right) - float(x_left)) * 0.025)
        if abs(float(event.xdata) - x0) > tolerance:
            return False
        self.profile_drag_state["alias"] = alias
        self.profile_drag_state["x0"] = x0
        self.profile_drag_state["x"] = float(event.xdata)
        self.profile_drag_state["artists"] = list(
            self.integration_line_artists_by_alias.get(alias, [])
        )
        self.status["last_alias"] = alias
        self.set_status(f"{alias}: dragging first-ring profile line")
        return True

    def nearest_clicked_point(
        self,
        alias: str,
        event,
        max_screen_distance: float = 12.0,
    ):
        ax = self.top_axes_by_alias.get(alias)
        points = self.manual_points_by_alias.get(alias) or []
        if ax is None or not points:
            return None
        event_xy = np.asarray([float(event.x), float(event.y)])
        best = None
        best_distance = None
        for index, (col, row) in enumerate(points):
            point_xy = np.asarray(ax.transData.transform((col, row)), dtype=float)
            distance = float(np.linalg.norm(point_xy - event_xy))
            if best_distance is None or distance < best_distance:
                best = index
                best_distance = distance
        if best_distance is None or best_distance > float(max_screen_distance):
            return None
        return best

    def delete_point(self, alias: str, index: int | None = None):
        target_alias = alias
        points = self.manual_points_by_alias.setdefault(target_alias, [])
        artists = self.manual_artists_by_alias.setdefault(target_alias, [])
        if not points:
            self.set_status(f"{target_alias}: no clicked point to delete")
            return
        point_index = len(points) - 1 if index is None else int(index)
        if point_index < 0 or point_index >= len(points):
            return
        points.pop(point_index)
        if point_index < len(artists):
            artist = artists.pop(point_index)
            try:
                artist.remove()
            except Exception:
                pass
        try:
            npt_path, refit = self.geometry.save_clicked_points(target_alias)
        except Exception as exc:
            self.set_status(f"{target_alias}: clicked point refit failed: {exc}")
            self.canvas.draw_idle()
            return
        self.set_status(
            f"{target_alias}: deleted point {point_index + 1}; {len(points)} clicked points on ring "
            f"{self.first_ring_by_alias.get(target_alias, 1)}"
            + ("; refit" if refit else "; integrations recomputed" if points else "")
            + (f"; saved {npt_path}" if npt_path else "")
        )
        self.canvas.draw_idle()

    def delete_last_point(self, alias: str | None = None):
        target_alias = alias or self.status.get("last_alias")
        if not target_alias:
            self.set_status("No clicked point to delete")
            return
        self.delete_point(target_alias)

    def set_selected_ring(self, alias: str, ring_index: int):
        target_alias = str(alias)
        self.first_ring_by_alias[target_alias] = max(1, int(ring_index or 1))
        self.status["last_alias"] = target_alias
        try:
            if self.manual_points_by_alias.get(target_alias):
                npt_path, refit = self.geometry.save_clicked_points(target_alias)
            else:
                review = self.geometry.review_state_by_alias.get(target_alias)
                npt_path = (
                    self.geometry.finalize_review_geometry(target_alias, review)
                    if review is not None
                    else None
                )
                refit = False
        except Exception as exc:
            self.set_status(f"{target_alias}: ring change failed: {exc}")
            self.canvas.draw_idle()
            return
        self.set_status(
            f"{target_alias}: selected ring {self.first_ring_by_alias.get(target_alias, 1)}"
            + ("; refit" if refit else "; points recomputed")
            + (f"; saved {npt_path}" if npt_path else "")
        )
        self.canvas.draw_idle()

    def on_click(self, event):
        if self.start_profile_drag(event):
            self.canvas.draw_idle()
            return
        alias = self.axis_to_alias.get(event.inaxes)
        if not alias or event.xdata is None or event.ydata is None:
            return
        self.status["last_alias"] = alias
        if getattr(event, "dblclick", False):
            self._reset_zoom(alias, event)
            return
        artists = self.manual_artists_by_alias.setdefault(alias, [])
        points = self.manual_points_by_alias.setdefault(alias, [])
        if event.button == 3:
            self._handle_right_click(alias, event)
            return
        if event.button != 1:
            return

        point_index = self.nearest_clicked_point(alias, event)
        if point_index is not None:
            self.drag_state["alias"] = alias
            self.drag_state["index"] = point_index
            self.drag_state["artist"] = (
                artists[point_index] if point_index < len(artists) else None
            )
            self.set_status(f"{alias}: dragging point {point_index + 1}")
            return

        col, row = float(event.xdata), float(event.ydata)
        points.append((col, row))
        artist = event.inaxes.plot(
            [col],
            [row],
            marker="o",
            markersize=6,
            markeredgewidth=1.0,
            markerfacecolor="none",
            color="#ffffff",
            linestyle="None",
        )[0]
        artists.append(artist)
        try:
            npt_path, refit = self.geometry.save_clicked_points(alias)
        except Exception as exc:
            self.set_status(f"{alias}: clicked point refit failed: {exc}")
            self.canvas.draw_idle()
            return
        self.set_status(
            f"{alias}: added point ({col:.1f}, {row:.1f}) on ring "
            f"{self.first_ring_by_alias.get(alias, 1)}; total {len(points)}"
            + ("; refit" if refit else "; integrations recomputed")
            + (f"; saved {npt_path}" if npt_path else "")
        )
        self.canvas.draw_idle()

    def _reset_zoom(self, alias: str, event):
        view = self.full_view_by_alias.get(alias)
        if view:
            event.inaxes.set_xlim(view[0], view[1])
            event.inaxes.set_ylim(view[2], view[3])
            self.set_status(f"{alias}: zoom reset")
            self.canvas.draw_idle()

    def _handle_right_click(self, alias: str, event):
        point_index = self.nearest_clicked_point(alias, event)
        if point_index is not None:
            self.delete_point(alias, point_index)
            return
        try:
            npt_path = self.geometry.apply_center_hint(
                alias,
                float(event.xdata),
                float(event.ydata),
            )
        except Exception as exc:
            self.set_status(f"{alias}: center hint failed: {exc}")
            self.canvas.draw_idle()
            return
        self.set_status(
            f"{alias}: center hint ({float(event.xdata):.1f}, {float(event.ydata):.1f}); "
            "points recomputed"
            + (f"; saved {npt_path}" if npt_path else "")
        )
        self.canvas.draw_idle()

    def on_motion(self, event):
        profile_alias = self.profile_drag_state.get("alias")
        if profile_alias is not None:
            self._move_profile_drag(event, profile_alias)
            return
        alias = self.drag_state.get("alias")
        index = self.drag_state.get("index")
        artist = self.drag_state.get("artist")
        if alias is None or index is None or artist is None:
            return
        if event.inaxes is not self.top_axes_by_alias.get(alias):
            return
        if event.xdata is None or event.ydata is None:
            return
        points = self.manual_points_by_alias.get(alias) or []
        if int(index) >= len(points):
            return
        col, row = float(event.xdata), float(event.ydata)
        points[int(index)] = (col, row)
        artist.set_data([col], [row])
        self.set_status(f"{alias}: moving point {int(index) + 1} to ({col:.1f}, {row:.1f})")
        self.canvas.draw_idle()

    def _move_profile_drag(self, event, profile_alias: str):
        if event.xdata is None:
            return
        x = float(event.xdata)
        self.profile_drag_state["x"] = x
        for artist in self.profile_drag_state.get("artists", []) or []:
            try:
                artist.set_xdata([x, x])
            except Exception:
                pass
        x0 = float(self.profile_drag_state.get("x0") or x)
        self.set_status(f"{profile_alias}: first-ring line shift {x - x0:+.4f} deg")
        self.canvas.draw_idle()

    def on_release(self, event):
        profile_alias = self.profile_drag_state.get("alias")
        if profile_alias is not None:
            self._release_profile_drag(profile_alias)
            return
        alias = self.drag_state.get("alias")
        index = self.drag_state.get("index")
        if alias is None or index is None:
            return
        self.drag_state["alias"] = None
        self.drag_state["index"] = None
        self.drag_state["artist"] = None
        try:
            npt_path, refit = self.geometry.save_clicked_points(str(alias))
        except Exception as exc:
            self.set_status(f"{alias}: clicked point refit failed: {exc}")
            self.canvas.draw_idle()
            return
        points = self.manual_points_by_alias.get(str(alias)) or []
        self.set_status(
            f"{alias}: moved point {int(index) + 1}; {len(points)} clicked points"
            + ("; refit" if refit else "; integrations recomputed")
            + (f"; saved {npt_path}" if npt_path else "")
        )
        self.canvas.draw_idle()

    def _release_profile_drag(self, profile_alias: str):
        target_x = self.profile_drag_state.get("x")
        self.profile_drag_state["alias"] = None
        self.profile_drag_state["x0"] = None
        self.profile_drag_state["x"] = None
        self.profile_drag_state["artists"] = []
        if target_x is None:
            return
        try:
            npt_path = self.geometry.apply_profile_shift(str(profile_alias), float(target_x))
        except Exception as exc:
            self.set_status(f"{profile_alias}: profile shift failed: {exc}")
            self.canvas.draw_idle()
            return
        self.set_status(
            f"{profile_alias}: profile shift applied at {float(target_x):.4f} deg"
            + (f"; saved {npt_path}" if npt_path else "")
        )
        self.canvas.draw_idle()

    def on_scroll(self, event):
        alias = self.axis_to_alias.get(event.inaxes)
        if not alias or event.xdata is None or event.ydata is None:
            return
        self.status["last_alias"] = alias
        ax = event.inaxes
        scale = 0.8 if event.button == "up" else 1.25
        x_left, x_right = ax.get_xlim()
        y_bottom, y_top = ax.get_ylim()
        new_width = abs(x_right - x_left) * scale
        new_height = abs(y_top - y_bottom) * scale
        rel_x = (event.xdata - x_left) / (x_right - x_left)
        rel_y = (event.ydata - y_bottom) / (y_top - y_bottom)
        new_left = event.xdata - new_width * rel_x
        new_right = event.xdata + new_width * (1.0 - rel_x)
        new_bottom = event.ydata - new_height * rel_y
        new_top = event.ydata + new_height * (1.0 - rel_y)
        new_left, new_right, new_bottom, new_top = self._clamp_view(
            alias,
            new_width,
            new_height,
            new_left,
            new_right,
            new_bottom,
            new_top,
        )
        ax.set_xlim(new_left, new_right)
        ax.set_ylim(new_bottom, new_top)
        self.set_status(
            f"{alias}: zoom {abs(new_right - new_left):.0f} x {abs(new_top - new_bottom):.0f} px"
        )
        self.canvas.draw_idle()

    def _clamp_view(
        self,
        alias: str,
        new_width: float,
        new_height: float,
        new_left: float,
        new_right: float,
        new_bottom: float,
        new_top: float,
    ):
        view = self.full_view_by_alias.get(alias)
        if not view:
            return new_left, new_right, new_bottom, new_top
        min_x, max_x, min_y, max_y = view
        full_width = max_x - min_x
        full_height = max_y - min_y
        if new_width >= full_width:
            new_left, new_right = min_x, max_x
        else:
            if new_left < min_x:
                new_right += min_x - new_left
                new_left = min_x
            if new_right > max_x:
                new_left -= new_right - max_x
                new_right = max_x
        if new_height >= full_height:
            new_bottom, new_top = min_y, max_y
        else:
            if new_bottom < min_y:
                new_top += min_y - new_bottom
                new_bottom = min_y
            if new_top > max_y:
                new_bottom -= new_top - max_y
                new_top = max_y
        return new_left, new_right, new_bottom, new_top
