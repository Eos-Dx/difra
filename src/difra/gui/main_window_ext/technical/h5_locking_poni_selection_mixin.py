"""PONI selection/update helpers used before technical container locking."""

from difra.gui.main_window_ext.technical.h5_locking_common import *


class H5LockingPoniSelectionMixin:
    def _collect_lock_detector_aliases(self, container_path: Path):
        import h5py

        schema = get_schema(self.config if hasattr(self, "config") else None)
        aliases = []

        if hasattr(self, "_get_active_detector_aliases"):
            try:
                aliases.extend([a for a in self._get_active_detector_aliases() if a])
            except Exception:
                import logging

                logging.getLogger(__name__).debug(
                    "Suppressed exception in h5_management_locking_mixin.py",
                    exc_info=True,
                )

        if aliases:
            return sorted({str(a) for a in aliases if str(a).strip()})

        try:
            with h5py.File(container_path, "r") as h5f:
                tech_group = h5f.get(schema.GROUP_TECHNICAL)
                if tech_group is None:
                    return []
                for event_name in tech_group.keys():
                    if not str(event_name).startswith("tech_evt_"):
                        continue
                    event_group = tech_group[event_name]
                    for detector_name in event_group.keys():
                        detector_group = event_group[detector_name]
                        alias = detector_group.attrs.get(schema.ATTR_DETECTOR_ALIAS, "")
                        if isinstance(alias, bytes):
                            alias = alias.decode("utf-8", errors="replace")
                        alias = str(alias or "").strip()
                        if alias:
                            aliases.append(alias)
        except Exception:
            return []

        return sorted({str(a) for a in aliases if str(a).strip()})

    def _prompt_poni_selection_for_lock(self, aliases) -> bool:
        from difra.gui.main_window_ext.technical_measurements import (
            PoniFileSelectionDialog,
        )

        if not isinstance(getattr(self, "ponis", None), dict):
            self.ponis = {}
        if not isinstance(getattr(self, "poni_files", None), dict):
            self.poni_files = {}

        dialog = PoniFileSelectionDialog(
            aliases=sorted(set(aliases)),
            current_poni_files=getattr(self, "poni_files", {}) or {},
            parent=self,
        )
        accepted_value = getattr(type(dialog), "Accepted", 1)
        if dialog.exec_() != accepted_value:
            self._log_technical_event("Lock cancelled: PONI selection dialog cancelled")
            return False

        selected_poni_files = dialog.get_poni_files() or {}
        missing = []
        read_errors = []

        for alias in sorted(set(aliases)):
            candidate_path = str(selected_poni_files.get(alias) or "").strip()
            if not candidate_path:
                current_info = self.poni_files.get(alias)
                if isinstance(current_info, dict):
                    candidate_path = str(current_info.get("path") or "").strip()

            if not candidate_path or not os.path.exists(candidate_path):
                missing.append(alias)
                continue

            try:
                with open(candidate_path, "r", encoding="utf-8") as file_handle:
                    self.ponis[alias] = file_handle.read()
                self.poni_files[alias] = {
                    "path": candidate_path,
                    "name": Path(candidate_path).name,
                }
            except Exception as exc:
                read_errors.append(f"{alias}: {exc}")

        if missing:
            QMessageBox.warning(
                self,
                "Missing PONI Files",
                "PONI files are required before locking.\n\nMissing for: "
                + ", ".join(missing),
            )
            self._log_technical_event(
                f"Lock blocked: missing PONI for aliases {missing}"
            )
            return False

        if read_errors:
            QMessageBox.warning(
                self,
                "PONI Read Error",
                "Failed to read one or more PONI files:\n\n"
                + "\n".join(read_errors),
            )
            self._log_technical_event(
                f"Lock blocked: failed to read PONI files ({len(read_errors)})"
            )
            return False

        return True

    def _has_ready_poni_file_selection(self, aliases) -> bool:
        if not isinstance(getattr(self, "poni_files", None), dict):
            return False

        for alias in sorted(
            {str(alias).strip() for alias in (aliases or []) if str(alias).strip()}
        ):
            info = self.poni_files.get(alias)
            if not isinstance(info, dict):
                return False
            candidate_path = str(info.get("path") or "").strip()
            if not candidate_path or not os.path.exists(candidate_path):
                return False
        return True

    def _launch_poni_update_for_container(self, container_path: Path) -> bool:
        """Run Update PONI flow for a specific container path."""
        update_fn = getattr(self, "update_active_technical_container_poni", None)
        if not callable(update_fn):
            return False

        previous_path = str(getattr(self, "_active_technical_container_path", "") or "")
        previous_locked = bool(
            getattr(self, "_active_technical_container_locked", False)
        )
        switched = False
        try:
            same_path = False
            if previous_path:
                try:
                    same_path = Path(previous_path).resolve() == Path(
                        container_path
                    ).resolve()
                except Exception:
                    same_path = str(previous_path) == str(container_path)
            if not same_path:
                self._active_technical_container_path = str(container_path)
                self._active_technical_container_locked = False
                switched = True
            return bool(update_fn())
        finally:
            if switched:
                self._active_technical_container_path = previous_path
                self._active_technical_container_locked = previous_locked

    def _ensure_poni_before_lock(self, container_path: Path, container_id: str) -> bool:
        if self._container_has_poni_datasets(container_path):
            return True

        aliases = self._collect_lock_detector_aliases(container_path)
        if not aliases:
            QMessageBox.warning(
                self,
                "Missing PONI",
                "Container has no PONI datasets and detector aliases could not be determined.\n\n"
                "Load/select a valid technical container with detector aliases and try again.",
            )
            self._log_technical_event(
                f"Lock blocked: missing PONI and detector aliases for {container_id}"
            )
            return False

        if bool(self.config.get("DEV", False)):
            if self._auto_provision_demo_poni_files(aliases):
                if hasattr(self, "_sync_active_technical_container_from_table"):
                    synced = self._sync_active_technical_container_from_table(
                        show_errors=False
                    )
                    if not synced:
                        QMessageBox.warning(
                            self,
                            "PONI Sync Failed",
                            "Demo PONI files were generated but container sync failed.\n\n"
                            "Fix technical rows and try locking again.",
                        )
                        self._log_technical_event(
                            f"Lock blocked: failed to sync demo PONI into {container_id}"
                        )
                        return False
                if self._container_has_poni_datasets(container_path):
                    QMessageBox.information(
                        self,
                        "Demo PONI Added",
                        "DEMO mode detected. Fake PONI files were added automatically.\n\n"
                        "Validation will now continue before lock.",
                    )
                    return True

        if self._has_ready_poni_file_selection(aliases):
            if hasattr(self, "_sync_active_technical_container_from_table"):
                synced = self._sync_active_technical_container_from_table(
                    show_errors=False
                )
                if not synced:
                    QMessageBox.warning(
                        self,
                        "PONI Sync Failed",
                        "Prepared PONI files were found, but syncing them into the container failed.\n\n"
                        "Fix technical rows and try locking again.",
                    )
                    self._log_technical_event(
                        f"Lock blocked: failed to sync preselected PONI into {container_id}"
                    )
                    return False

            if self._container_has_poni_datasets(container_path):
                self._log_technical_event(
                    f"PONI datasets auto-applied before lock for {container_id}"
                )
                return True

        reply = QMessageBox.question(
            self,
            "PONI Required",
            "Active technical container has no embedded PONI datasets.\n\n"
            "Select PONI files now? They will be added, then validation will run before lock.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._log_technical_event("Lock cancelled: user declined PONI selection")
            return False

        if not self._prompt_poni_selection_for_lock(aliases):
            return False

        if hasattr(self, "_sync_active_technical_container_from_table"):
            synced = self._sync_active_technical_container_from_table(show_errors=False)
            if not synced:
                QMessageBox.warning(
                    self,
                    "PONI Sync Failed",
                    "PONI files were selected but container sync failed.\n\n"
                    "Fix technical rows and try locking again.",
                )
                self._log_technical_event(
                    f"Lock blocked: failed to sync selected PONI into {container_id}"
                )
                return False

        if not self._container_has_poni_datasets(container_path):
            QMessageBox.warning(
                self,
                "PONI Missing",
                "PONI datasets are still missing after selection.\n\n"
                "Please verify detector aliases and selected PONI files.",
            )
            self._log_technical_event(
                f"Lock blocked: PONI still missing after selection for {container_id}"
            )
            return False

        self._log_technical_event(
            f"PONI datasets added before lock for {container_id}"
        )
        return True
