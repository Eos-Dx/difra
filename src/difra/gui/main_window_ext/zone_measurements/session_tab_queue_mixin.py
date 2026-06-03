"""Session management tab for Zone Measurements."""

from pathlib import Path
from typing import List, Optional

from difra.gui.qt_compat import (
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)

from difra.gui.session_lifecycle_service import SessionLifecycleService
from difra.gui.session_tab_presenter import SessionTabPresenter
from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionTabQueueMixin:
    """Session tab behavior split from SessionTabMixin."""

    def _get_measurements_folder_for_queue(self) -> Path:
        if hasattr(self, "config") and self.config:
            folder = self.config.get("measurements_folder") or self.config.get(
                "session_folder"
            )
            if folder:
                return Path(folder)

        if hasattr(self, "folderLineEdit"):
            folder = (self.folderLineEdit.text() or "").strip()
            if folder:
                return Path(folder)

        if (
            hasattr(self, "session_manager")
            and self.session_manager
            and getattr(self.session_manager, "session_path", None)
        ):
            return Path(self.session_manager.session_path).parent

        return Path.home() / "difra_measurements"

    def _get_session_archive_folder(self) -> Path:
        measurements_folder = self._get_measurements_folder_for_queue()
        return SessionLifecycleService.resolve_archive_folder(
            config=self.config if hasattr(self, "config") else None,
            measurements_folder=measurements_folder,
        )

    def _refresh_session_container_lists(self):
        if not hasattr(self, "_pending_session_summary_text"):
            return

        schema = self._container_schema()
        container_manager = self._container_manager()
        pending_rows = SessionTabPresenter.build_pending_rows(
            self._get_measurements_folder_for_queue(),
            schema=schema,
            container_manager=container_manager,
        )
        archived_rows = SessionTabPresenter.build_archived_rows(
            self._get_session_archive_folder(),
            schema=schema,
            container_manager=container_manager,
        )
        self._pending_rows = list(pending_rows)
        self._update_pending_session_summary(self._pending_rows)
        self._archived_rows_all = list(archived_rows)
        self._archived_rows_filtered = list(archived_rows)
        self._populate_archive_window_table()
        self._update_archive_action_buttons()

        if hasattr(self, "archive_path_label"):
            archive_folder = self._get_session_archive_folder()
            self.archive_path_label.setText(f"Archive folder: {archive_folder}")

    def _set_pending_session_actions_enabled(self, enabled: bool) -> None:
        load_button = getattr(self, "load_session_btn", None)
        if load_button is not None:
            load_button.setEnabled(True)

        for attr_name in ("close_session_btn", "send_session_btn"):
            button = getattr(self, attr_name, None)
            if button is not None:
                button.setEnabled(bool(enabled))
        self._update_preview_session_data_enabled()

    def _active_session_container_path(self) -> Optional[Path]:
        session_manager = getattr(self, "session_manager", None)
        active_path = getattr(session_manager, "session_path", None)
        if not active_path:
            return None
        path = Path(active_path)
        return path if path.exists() else None

    def _preview_session_container_path(self) -> Optional[Path]:
        return (
            self._selected_pending_container() or self._active_session_container_path()
        )

    def _update_preview_session_data_enabled(self) -> None:
        button = getattr(self, "preview_session_data_btn", None)
        if button is not None:
            button.setEnabled(self._preview_session_container_path() is not None)

    def _update_pending_session_summary(self, pending_rows: List[dict]) -> None:
        rows = list(pending_rows or [])
        self._current_pending_container_path = None

        if not rows:
            self._pending_session_summary_text = (
                "No session container in measurements folder."
            )
            self._set_pending_session_actions_enabled(False)
            return

        if len(rows) > 1:
            file_names = [str(row.get("file_name") or "") for row in rows[:3]]
            summary = [
                f"Multiple session containers found in measurements folder ({len(rows)}).",
                "This screen expects exactly one active session container.",
            ]
            if file_names:
                summary.append("")
                summary.extend(file_names)
            self._pending_session_summary_text = "\n".join(summary)
            self._set_pending_session_actions_enabled(False)
            return

        row = rows[0]
        raw_path = str(row.get("path") or "").strip()
        self._current_pending_container_path = Path(raw_path) if raw_path else None
        summary = [
            f"File: {row.get('file_name', '')}",
            f"Specimen: {row.get('sample_id', '')}",
            f"Study: {row.get('study_name', '')}",
            f"Operator: {row.get('operator_id', '')}",
            f"Created: {row.get('created', '')}",
            f"Status: {row.get('status', '')}",
        ]
        self._pending_session_summary_text = "\n".join(summary)
        self._set_pending_session_actions_enabled(
            self._current_pending_container_path is not None
        )

    def _apply_archive_filters(self):
        self._archived_rows_filtered = list(
            getattr(self, "_archived_rows_all", []) or []
        )
        self._populate_archive_window_table()
        self._update_archive_action_buttons()

    def _selected_pending_container(self) -> Optional[Path]:
        return getattr(self, "_current_pending_container_path", None)

    @staticmethod
    def _session_preview_text(value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    @classmethod
    def _session_preview_detector_key(
        cls,
        *,
        alias,
        detector_id,
        role_name,
    ) -> Optional[str]:
        tokens = {
            cls._session_preview_text(alias).strip().upper(),
            cls._session_preview_text(detector_id).strip().upper(),
            cls._session_preview_text(role_name).strip().upper(),
        }
        expanded = set(tokens)
        for token in list(tokens):
            if token.startswith("DET_"):
                expanded.add(token[4:])
        if expanded & {"PRIMARY", "SAXS"}:
            return "PRIMARY"
        if expanded & {"SECONDARY", "WAXS"}:
            return "SECONDARY"
        return None

    def _session_container_has_attenuation(self, h5f, schema) -> bool:
        ana_group_path = getattr(
            schema,
            "GROUP_ANALYTICAL_MEASUREMENTS",
            "/analytical_measurements",
        )
        ana_group = h5f.get(ana_group_path)
        if ana_group is None:
            return False

        type_attr = getattr(schema, "ATTR_ANALYSIS_TYPE", "analysis_type")
        role_attr = getattr(schema, "ATTR_ANALYSIS_ROLE", "analysis_role")
        for item_name in sorted(ana_group.keys()):
            item = ana_group[item_name]
            analysis_type = (
                self._session_preview_text(item.attrs.get(type_attr, item_name))
                .strip()
                .lower()
            )
            analysis_role = (
                self._session_preview_text(item.attrs.get(role_attr, ""))
                .strip()
                .lower()
            )
            if analysis_type.startswith("attenuation") or analysis_role in {
                "i0",
                "i",
                "without",
                "with",
                "without_sample",
                "with_sample",
            }:
                return True
        return False

    def _collect_session_data_preview(self, container_path: Path) -> dict:
        import h5py

        schema = self._container_schema()
        measurements_path = getattr(schema, "GROUP_MEASUREMENTS", "/entry/measurements")
        dataset_name = getattr(schema, "DATASET_PROCESSED_SIGNAL", "processed_signal")
        alias_attr = getattr(schema, "ATTR_DETECTOR_ALIAS", "detector_alias")
        detector_id_attr = getattr(schema, "ATTR_DETECTOR_ID", "detector_id")

        profiles = {"PRIMARY": [], "SECONDARY": []}
        attenuation_exists = False
        extractor = getattr(self, "_extract_profile_from_measurement", None)

        with h5py.File(container_path, "r") as h5f:
            attenuation_exists = self._session_container_has_attenuation(h5f, schema)
            measurements_group = h5f.get(measurements_path)
            if measurements_group is None:
                return {
                    "profiles": profiles,
                    "attenuation_exists": attenuation_exists,
                }

            for point_name in sorted(measurements_group.keys()):
                point_group = measurements_group[point_name]
                for measurement_name in sorted(point_group.keys()):
                    measurement_group = point_group[measurement_name]
                    for role_name in sorted(measurement_group.keys()):
                        detector_group = measurement_group[role_name]
                        if dataset_name not in detector_group:
                            continue
                        dataset_path = (
                            f"{measurement_group.name}/{role_name}/{dataset_name}"
                        )
                        alias = detector_group.attrs.get(alias_attr, role_name)
                        detector_id = detector_group.attrs.get(detector_id_attr, "")
                        key = self._session_preview_detector_key(
                            alias=alias,
                            detector_id=detector_id,
                            role_name=role_name,
                        )
                        if key not in profiles:
                            continue
                        ref = f"h5ref://{container_path}#{dataset_path}"
                        if callable(extractor):
                            npt = 100 if key == "SECONDARY" else 200
                            try:
                                profile = extractor(ref, alias=key, npt=npt)
                            except TypeError:
                                try:
                                    profile = extractor(ref, key)
                                except TypeError:
                                    profile = extractor(ref)
                        else:
                            profile = None
                        if not profile:
                            continue
                        profiles[key].append(
                            {
                                "label": f"{point_name}/{measurement_name}",
                                "profile": profile,
                            }
                        )

        return {"profiles": profiles, "attenuation_exists": attenuation_exists}

    @staticmethod
    def _plot_session_detector_profiles(axis, detector_key: str, rows: List[dict]):
        import numpy as np

        title = (
            "Primary detector" if detector_key == "PRIMARY" else "Secondary detector"
        )
        axis.set_title(title)
        axis.set_ylabel("Intensity")
        if not rows:
            axis.text(
                0.5,
                0.5,
                "No measurement profiles",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_axis_off()
            return

        uses_q = False
        plotted = False
        for row in rows:
            raw_profile = row.get("profile") or {}
            profile = (
                raw_profile
                if isinstance(raw_profile, dict)
                else {"intensity": raw_profile}
            )
            intensity = np.asarray(profile.get("intensity"), dtype=float).reshape(-1)
            if intensity.size < 2:
                continue
            q_values = profile.get("q_values")
            if q_values is not None:
                x_values = np.asarray(q_values, dtype=float).reshape(-1)
                uses_q = True
            else:
                x_values = np.arange(intensity.size, dtype=float)
            count = min(int(x_values.size), int(intensity.size))
            if count < 2:
                continue
            x_values = x_values[:count]
            intensity = intensity[:count]
            finite = np.isfinite(x_values) & np.isfinite(intensity) & (intensity > 0)
            if np.count_nonzero(finite) < 2:
                continue
            axis.plot(
                x_values[finite],
                intensity[finite],
                linewidth=0.9,
                alpha=0.65,
            )
            plotted = True

        if not plotted:
            axis.text(
                0.5,
                0.5,
                "No valid positive profiles",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.set_axis_off()
            return

        axis.set_yscale("log")
        axis.set_xlabel("q (nm^-1)" if uses_q else "Index")
        axis.grid(True, alpha=0.25)

    @staticmethod
    def _plot_session_attenuation_placeholder(axis, detector_key: str, exists: bool):
        axis.set_title(f"{detector_key.title()} attenuation")
        axis.set_xlabel("Measurement index")
        axis.set_ylabel("Absorption")
        axis.grid(True, alpha=0.25)
        if exists:
            axis.text(
                0.5,
                0.5,
                "Calculation placeholder",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
        else:
            axis.text(
                0.5,
                0.5,
                "No attenuation measurements",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )

    def _show_session_data_preview_dialog(self, container_path: Path, payload: dict):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure

        dialog = QDialog(self)
        dialog.setWindowTitle("See results: session data")
        dialog.setModal(False)
        dialog.resize(1280, 760)

        layout = QVBoxLayout(dialog)
        summary = QLabel(str(container_path))
        summary.setStyleSheet("color: #555; padding: 4px;")
        layout.addWidget(summary)

        fig = Figure(figsize=(12.5, 7.0), constrained_layout=True)
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas, 1)

        axes = fig.subplots(
            2,
            2,
            gridspec_kw={"height_ratios": [4, 1.25]},
        )
        profiles = payload.get("profiles") or {}
        self._plot_session_detector_profiles(
            axes[0][0],
            "PRIMARY",
            list(profiles.get("PRIMARY") or []),
        )
        self._plot_session_detector_profiles(
            axes[0][1],
            "SECONDARY",
            list(profiles.get("SECONDARY") or []),
        )
        attenuation_exists = bool(payload.get("attenuation_exists"))
        self._plot_session_attenuation_placeholder(
            axes[1][0],
            "PRIMARY",
            attenuation_exists,
        )
        self._plot_session_attenuation_placeholder(
            axes[1][1],
            "SECONDARY",
            attenuation_exists,
        )
        canvas.draw_idle()

        close_button = QPushButton("Close", dialog)
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)

        self._session_data_preview_dialog = dialog
        dialog.finished.connect(
            lambda *_args: setattr(self, "_session_data_preview_dialog", None)
        )
        dialog.show()
        return dialog

    def _on_preview_session_data(self):
        container_path = self._preview_session_container_path()
        if container_path is None or not Path(container_path).exists():
            QMessageBox.warning(
                self,
                "No Container Selected",
                "Select a session container with measurements.",
            )
            return

        try:
            payload = self._collect_session_data_preview(Path(container_path))
        except Exception as exc:
            logger.warning(
                "Failed to build session data preview",
                session_path=str(container_path),
                exc_info=True,
            )
            QMessageBox.warning(
                self,
                "Preview Failed",
                f"Could not build session data preview:\n{exc}",
            )
            return

        self._show_session_data_preview_dialog(Path(container_path), payload)

    def _all_pending_containers(self) -> List[Path]:
        return [
            Path(str(row.get("path")))
            for row in list(getattr(self, "_pending_rows", []) or [])
            if str(row.get("path") or "").strip()
        ]

    def _path_from_table_row(self, table: QTableWidget, row: int, path_col: int):
        if row < 0:
            return None
        path_item = table.item(row, path_col)
        if path_item is None:
            return None
        raw = (path_item.text() or "").strip()
        if not raw:
            return None
        return Path(raw)

    def _selected_archived_containers(
        self, *, fallback_path: Optional[Path] = None
    ) -> List[Path]:
        table = getattr(self, "archive_window_table", None)
        return self._selected_paths_from_archive_table(
            table, fallback_path=fallback_path
        )

    def _selected_paths_from_archive_table(
        self,
        table: Optional[QTableWidget],
        *,
        fallback_path: Optional[Path] = None,
    ) -> List[Path]:
        if table is None:
            return [Path(fallback_path)] if fallback_path is not None else []
        selected_rows = sorted({index.row() for index in table.selectedIndexes()})
        selected_paths: List[Path] = []
        for row in selected_rows:
            path = self._path_from_table_row(table, row, 9)
            if path is not None:
                selected_paths.append(Path(path))

        if fallback_path is None:
            return selected_paths

        fallback_resolved = str(Path(fallback_path))
        if not selected_paths:
            return [Path(fallback_path)]
        if fallback_resolved not in {str(path) for path in selected_paths}:
            return [Path(fallback_path)]
        return selected_paths

    def _update_archive_action_buttons(self):
        window_button = getattr(self, "send_archived_window_btn", None)
        window_table = getattr(self, "archive_window_table", None)
        if window_button is not None:
            window_button.setEnabled(
                bool(self._selected_paths_from_archive_table(window_table))
            )
