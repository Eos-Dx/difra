"""Technical H5 loading/table population responsibilities."""

from pathlib import Path
import hashlib
import json

import numpy as np

from . import h5_management_mixin as _module
from .poni_center_validation import resolve_poni_rule_alias, validate_poni_metadata
from .poni_distance_validation import parse_poni_distance_cm, validate_poni_distances
from . import technical_startup_reconcile
from difra.gui.technical.analysis_compat import detect_faulty_pixel_masks

os = _module.os
shutil = _module.shutil
time = _module.time
logger = _module.logger
QInputDialog = _module.QInputDialog
QMessageBox = _module.QMessageBox
QFileDialog = _module.QFileDialog
get_container_manager = _module.get_container_manager
get_schema = _module.get_schema
get_technical_validator = _module.get_technical_validator

from difra.gui.main_window_ext.technical import h5_management_loading_actions



class H5LoadingStartupMixin:
    @staticmethod
    def _paths_same(left: Path, right: Path) -> bool:
        try:
            return Path(left).resolve() == Path(right).resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            return str(Path(left)) == str(Path(right))
    @staticmethod
    def _distance_matches(
        actual_cm,
        expected_cm: float,
        tolerance_cm: float = 0.5,
    ) -> bool:
        try:
            if actual_cm is None:
                return False
            return abs(float(actual_cm) - float(expected_cm)) <= float(tolerance_cm)
        except (TypeError, ValueError):
            return False
    def _read_technical_container_distance_cm(self, container_path: Path):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        try:
            with h5py.File(container_path, "r") as h5f:
                distance_attr = h5f.attrs.get(schema.ATTR_DISTANCE_CM)
                if distance_attr is not None:
                    return float(distance_attr)

                tech_group = h5f.get(schema.GROUP_TECHNICAL)
                if tech_group is None:
                    tech_group = h5f.get(f"{schema.GROUP_CALIBRATION_SNAPSHOT}/events")
                if tech_group is not None:
                    for event_name in sorted(tech_group.keys()):
                        event_group = tech_group[event_name]
                        for detector_name in sorted(event_group.keys()):
                            detector_group = event_group[detector_name]
                            distance_attr = detector_group.attrs.get(schema.ATTR_DISTANCE_CM)
                            if distance_attr is not None:
                                return float(distance_attr)

                poni_group = h5f.get(schema.GROUP_TECHNICAL_PONI)
                if poni_group is not None:
                    for ds_name in sorted(poni_group.keys()):
                        distance_attr = poni_group[ds_name].attrs.get(schema.ATTR_DISTANCE_CM)
                        if distance_attr is not None:
                            return float(distance_attr)
        except (KeyError, OSError, TypeError, ValueError):
            return None

        return None
    def _list_storage_technical_containers(self, storage_folder: Path):
        storage_folder = Path(storage_folder)
        if not storage_folder.exists():
            return []

        candidates = []
        seen = set()
        for pattern in ("technical_*.nxs.h5", "technical_*.h5"):
            for tech_path in storage_folder.glob(pattern):
                if not tech_path.is_file():
                    continue
                try:
                    resolved = str(tech_path.resolve())
                except (OSError, RuntimeError, TypeError, ValueError):
                    resolved = str(tech_path)
                if resolved in seen:
                    continue
                seen.add(resolved)
                candidates.append(tech_path)

        candidates.sort(
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        return candidates
    def _list_archived_technical_containers(self):
        return technical_startup_reconcile.list_archived_technical_containers(self)
    def _find_duplicate_archived_technical_container(self, container_path: Path):
        return technical_startup_reconcile.find_duplicate_archived_technical_container(
            self,
            container_path,
        )
    def _delete_storage_technical_container(self, container_path: Path) -> bool:
        return technical_startup_reconcile.delete_storage_technical_container(
            self,
            container_path,
        )
    def _format_startup_technical_container_option(self, container_path: Path) -> str:
        return technical_startup_reconcile.format_startup_technical_container_option(
            self,
            container_path,
        )
    def _prompt_startup_technical_container_selection(self, candidates):
        return technical_startup_reconcile.prompt_startup_technical_container_selection(
            self,
            candidates,
        )
    def reconcile_startup_technical_containers(self):
        return technical_startup_reconcile.reconcile_startup_technical_containers(self)
    def _lock_and_archive_technical_container(self, existing_path: Path) -> Path:
        container_manager = get_container_manager(
            self.config if hasattr(self, "config") else None
        )
        existing_path = Path(existing_path)

        if not container_manager.is_container_locked(existing_path):
            operator_id = None
            if hasattr(self, "config") and isinstance(self.config, dict):
                operator_id = self.config.get("operator_id")
            container_manager.lock_container(existing_path, user_id=operator_id)
            set_state = getattr(self, "_set_container_state", None)
            if callable(set_state):
                set_state(
                    Path(existing_path),
                    state=getattr(self, "STATE_LOCKED", "locked"),
                    reason="locked_before_archive",
                )

        archived = self._archive_existing_technical_container_for_replacement(
            existing_path=existing_path,
        )

        current_active = self._active_technical_container_path_obj()
        if current_active is not None and self._paths_same(current_active, existing_path):
            self._active_technical_container_path = ""
            self._active_technical_container_locked = False
            if hasattr(self, "_refresh_technical_output_folder_lock"):
                try:
                    self._refresh_technical_output_folder_lock()
                except (AttributeError, RuntimeError, TypeError) as exc:
                    logger.warning(
                        "Failed to refresh technical output lock after archive: %s",
                        exc,
                        exc_info=True,
                    )

        return archived
    def _archive_existing_technical_container_for_replacement(
        self,
        existing_path: Path,
    ) -> Path:
        from .helpers import _get_technical_archive_folder

        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        container_manager = get_container_manager(
            self.config if hasattr(self, "config") else None
        )
        archive_base = Path(
            _get_technical_archive_folder(self.config if hasattr(self, "config") else None)
        )
        archive_base.mkdir(parents=True, exist_ok=True)

        operator_token = "unknown"
        get_lock_info = getattr(container_manager, "get_lock_info", None)
        if callable(get_lock_info):
            try:
                lock_info = get_lock_info(Path(existing_path)) or {}
                operator_token = self._safe_archive_token(
                    lock_info.get("locked_by") or cfg.get("operator_id", ""),
                    fallback="unknown",
                )
            except (AttributeError, OSError, TypeError, ValueError):
                operator_token = "unknown"

        from .h5_management_lock_actions import (
            _find_existing_technical_companion_archive_folder,
            _unique_archive_destination,
        )

        archive_dir = _find_existing_technical_companion_archive_folder(
            existing_path,
            archive_base,
        )
        if archive_dir is None:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            container_token = self._safe_archive_token(Path(existing_path).stem, "technical")
            archive_dir = archive_base / (
                f"{container_token}_{operator_token}_{timestamp}"
            )
            suffix = 1
            while archive_dir.exists():
                suffix += 1
                archive_dir = archive_base / (
                    f"{container_token}_{operator_token}_{timestamp}_{suffix}"
                )
            archive_dir.mkdir(parents=True, exist_ok=False)
        else:
            archive_dir.mkdir(parents=True, exist_ok=True)

        destination = _unique_archive_destination(archive_dir, Path(existing_path).name)
        shutil.move(str(existing_path), str(destination))
        set_state = getattr(self, "_set_container_state", None)
        if callable(set_state):
            set_state(
                Path(destination),
                state=getattr(self, "STATE_ARCHIVED", "archived"),
                reason="archived_for_replacement",
            )
        archived_count = 0
        archive_technical_data_files = getattr(
            container_manager,
            "archive_technical_data_files",
            None,
        )
        if callable(archive_technical_data_files):
            file_patterns = None
            if isinstance(cfg, dict):
                file_patterns = cfg.get(
                    "technical_archive_patterns",
                    ["*.txt", "*.dsc", "*.npy", "*.poni", "*_state.json"],
                )
            try:
                archived_count = int(
                    archive_technical_data_files(
                        container_path=Path(existing_path),
                        archive_folder=archive_dir,
                        file_patterns=file_patterns,
                    )
                    or 0
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to archive technical companion files for %s: %s",
                    existing_path,
                    exc,
                    exc_info=True,
                )
        if archived_count > 0:
            try:
                from .h5_management_lock_actions import _rewrite_technical_source_paths

                _rewrite_technical_source_paths(destination, archive_dir)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to rewrite archived technical source paths for %s: %s",
                    destination,
                    exc,
                    exc_info=True,
                )
            self._log_technical_event(
                f"Archived {archived_count} technical companion file(s) to {archive_dir.name}"
            )
            update_paths = getattr(self, "_update_aux_table_paths_after_archive", None)
            if callable(update_paths):
                try:
                    update_paths(archive_dir)
                except (AttributeError, RuntimeError, TypeError):
                    logger.debug(
                        "Suppressed exception in h5_management_loading_mixin.py",
                        exc_info=True,
                    )
        try:
            self._remap_aux_table_container_references(
                old_container_path=Path(existing_path),
                new_container_path=Path(destination),
            )
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.warning(
                "Failed to remap aux table references after technical archive: %s",
                exc,
                exc_info=True,
            )
        try:
            from difra.gui.session_lifecycle_service import SessionLifecycleService

            SessionLifecycleService.copy_archive_item_to_mirror(
                archive_dir,
                config=self.config if hasattr(self, "config") else None,
                archive_kind="technical",
            )
        except Exception as exc:
            logger.warning(
                "Failed to mirror archived technical container folder %s: %s",
                archive_dir,
                exc,
                exc_info=True,
            )
        return destination
    def _remap_aux_table_container_references(
        self,
        *,
        old_container_path: Path,
        new_container_path: Path,
    ) -> int:
        """Rewrite aux table h5ref/source_info container paths after archive move."""
        if not hasattr(self, "auxTable") or self.auxTable is None:
            return 0

        old_path = Path(old_container_path)
        new_path = Path(new_container_path)
        updated = 0

        source_ref_role = self._aux_metadata_role() - 1
        source_info_role = self._aux_source_info_role()

        for row in range(self.auxTable.rowCount()):
            file_item = self.auxTable.item(row, 1)
            if file_item is None:
                continue

            row_updated = False

            source_ref = str(file_item.data(source_ref_role) or "").strip()
            container_path, dataset_path = self._parse_h5ref(source_ref)
            if container_path and dataset_path:
                try:
                    if self._paths_same(Path(container_path), old_path):
                        file_item.setData(
                            source_ref_role,
                            f"h5ref://{new_path}#{dataset_path}",
                        )
                        row_updated = True
                except (OSError, RuntimeError, TypeError, ValueError):
                    logger.debug(
                        "Suppressed exception in h5_management_loading_mixin.py",
                        exc_info=True,
                    )

            source_info = file_item.data(source_info_role)
            if isinstance(source_info, dict):
                source_container = str(source_info.get("container_path") or "").strip()
                if source_container:
                    try:
                        if self._paths_same(Path(source_container), old_path):
                            patched = dict(source_info)
                            patched["container_path"] = str(new_path)
                            file_item.setData(source_info_role, patched)
                            row_updated = True
                    except (OSError, RuntimeError, TypeError, ValueError):
                        logger.debug(
                            "Suppressed exception in h5_management_loading_mixin.py",
                            exc_info=True,
                        )

            if row_updated:
                updated += 1

        if updated > 0:
            self._log_technical_event(
                f"Remapped {updated} aux row(s) to archived container path: {new_path.name}"
            )
        return updated
    def _attempt_forced_session_send(self, session_path: Path) -> None:
        # Stub transport is handled by SessionLifecycleActions; keep this hook for
        # future real API integration.
        return None
    def _finalize_active_session_for_new_technical_container(self) -> bool:
        return h5_management_loading_actions.finalize_active_session_for_new_technical_container(
            self
        )
    def _prompt_existing_technical_container_resolution(
        self,
        existing_path: Path,
    ):
        return h5_management_loading_actions.prompt_existing_technical_container_resolution(
            self,
            existing_path,
        )
    def _create_new_active_technical_container(self, *, clear_table: bool = False):
        return h5_management_loading_actions.create_new_active_technical_container(
            self,
            clear_table=clear_table,
        )
    def _ensure_active_technical_container_available(
        self,
        *,
        for_edit: bool = False,
        prompt_on_locked: bool = False,
    ) -> bool:
        return h5_management_loading_actions.ensure_active_technical_container_available(
            self,
            for_edit=for_edit,
            prompt_on_locked=prompt_on_locked,
        )
