"""Technical H5 locking responsibilities: H5LockingActionsMixin."""

from difra.gui.main_window_ext.technical.h5_locking_common import *


class H5LockingActionsMixin:
    def _archive_existing_containers(self, storage_folder: str) -> int:
        """Archive any existing .h5 containers in storage folder before creating new one."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.archive_existing_containers(
            self, storage_folder
        )

    def _update_aux_table_paths_after_archive(self, archive_folder: Path) -> int:
        """Remap aux table file paths to archived locations for visualization."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.update_aux_table_paths_after_archive(
            self, archive_folder
        )

    def create_new_technical_container(self):
        """Legacy API kept for compatibility; uses container-first creation flow."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.create_new_technical_container(self)

    def lock_active_technical_container(self):
        """Lock currently active technical container."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.lock_active_technical_container(self)

    def archive_active_technical_container(self):
        """Archive active technical container (irreversible)."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.archive_active_technical_container(self)

    def update_active_technical_container_poni(self):
        """Update PONI files for active technical container and resync datasets."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.update_active_technical_container_poni(self)
    
    def _lock_container(self, container_path: str, container_id: str):
        """Lock the technical container and archive raw data."""
        self._sync_lock_action_overrides()
        return h5_management_lock_actions.lock_container(
            self, container_path, container_id
        )
    
