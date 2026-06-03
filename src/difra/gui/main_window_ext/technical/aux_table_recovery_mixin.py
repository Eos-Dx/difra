import logging
from pathlib import Path


def _tm():
    from difra.gui.main_window_ext.technical import aux_table_mixin

    return aux_table_mixin._tm()


logger = logging.getLogger(__name__)


class TechnicalAuxTableRecoveryMixin:
    def load_technical_files(self):
        tm = _tm()
        folder = ""
        current_folder = getattr(self, "_current_technical_output_folder", None)
        if callable(current_folder):
            try:
                folder = str(current_folder() or "")
            except Exception:
                folder = ""
        if not folder:
            try:
                folder = str((self.folderLE.text() or "").strip())
            except Exception:
                folder = ""
        if not folder:
            folder = str(Path.cwd())

        files, _selected_filter = tm.QFileDialog.getOpenFileNames(
            self,
            "Load Technical Files (Recovery)",
            folder,
            "Technical Files (*.npy *.txt);;NumPy Arrays (*.npy);;Text Files (*.txt);;All Files (*)",
        )
        if not files:
            self._log_technical_event("Technical file recovery cancelled by user")
            return

        valid_files = []
        for file_path in files:
            p = Path(str(file_path or "").strip())
            if not p.exists() or not p.is_file():
                continue
            if p.suffix.lower() not in (".npy", ".txt"):
                continue
            valid_files.append(str(p))

        if not valid_files:
            tm.QMessageBox.warning(
                self,
                "No Valid Files",
                "No valid technical files selected.\n\nSupported formats: .npy, .txt",
            )
            return

        replace_existing = tm.QMessageBox.Yes
        if (
            hasattr(self, "auxTable")
            and self.auxTable is not None
            and self.auxTable.rowCount() > 0
        ):
            replace_existing = tm.QMessageBox.question(
                self,
                "Replace Current Technical Rows",
                "Recovery mode will replace current technical rows with selected files.\n\nContinue?",
                tm.QMessageBox.Yes | tm.QMessageBox.No,
                tm.QMessageBox.No,
            )
            if replace_existing != tm.QMessageBox.Yes:
                self._log_technical_event(
                    "Technical file recovery cancelled: replace not confirmed"
                )
                return

        self._restoring_aux_table = True
        try:
            if replace_existing == tm.QMessageBox.Yes:
                self.auxTable.setRowCount(0)

            for file_path in sorted(valid_files):
                alias = self._infer_alias_from_filename(file_path)
                technical_type = self._infer_type_from_filename(file_path)
                self._add_aux_item_to_list(
                    alias or "",
                    file_path,
                    source_kind="file",
                    technical_type=technical_type,
                    is_primary=True,
                )
        finally:
            self._restoring_aux_table = False

        configure_distances = getattr(self, "configure_detector_distances", None)
        if callable(configure_distances):
            try:
                setattr(self, "_suppress_distance_auto_container_creation", True)
                configure_distances()
                setattr(self, "_use_draft_distances_for_next_sync", True)
            finally:
                setattr(self, "_suppress_distance_auto_container_creation", False)

        if hasattr(self, "_sync_active_technical_container_from_table"):
            setattr(self, "_skip_distance_prompt_once", True)
            try:
                synced = bool(
                    self._sync_active_technical_container_from_table(show_errors=True)
                )
            finally:
                setattr(self, "_skip_distance_prompt_once", False)
            if not synced:
                tm.QMessageBox.warning(
                    self,
                    "Recovery Sync Failed",
                    "Files were loaded into the table, but syncing to active container failed.",
                )
                self._log_technical_event("Technical file recovery failed during sync")
                return
            sync_state = getattr(self, "_sync_container_state", None)
            if callable(sync_state):
                active_path = str(
                    getattr(self, "_active_technical_container_path", "") or ""
                ).strip()
                if active_path:
                    sync_state(
                        Path(active_path),
                        reason="recovery_files_synced",
                    )

        self._log_technical_event(
            f"Loaded {len(valid_files)} technical file(s) in recovery mode"
        )
        tm.QMessageBox.information(
            self,
            "Recovery Mode",
            "Technical files were loaded in recovery mode.\n\n"
            "Next step: load PONI files.",
        )

        update_poni = getattr(self, "update_active_technical_container_poni", None)
        if callable(update_poni):
            reply = tm.QMessageBox.question(
                self,
                "Load PONI Files",
                "Recovery files are loaded.\n\nLoad PONI files now?",
                tm.QMessageBox.Yes | tm.QMessageBox.No,
                tm.QMessageBox.Yes,
            )
            if reply == tm.QMessageBox.Yes:
                update_poni()
