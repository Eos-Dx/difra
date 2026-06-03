"""Technical H5 locking responsibilities: H5LockingPoniReviewMixin."""

from difra.gui.main_window_ext.technical.h5_locking_common import *


class H5LockingPoniReviewMixin:
    def _poni_validation_config_label(self) -> str:
        config_path = getattr(self, "_active_config_path", None)
        if config_path:
            try:
                return str(Path(config_path).name)
            except Exception:
                return str(config_path)
        return "active setup config"

    def _ensure_embedded_poni_before_review(
        self,
        container_path: Path,
        *,
        container_id: str,
    ) -> bool:
        """Best-effort sync of in-memory PONI selections into the active container."""
        if self._container_has_poni_datasets(container_path):
            return True

        in_memory_ponis = getattr(self, "ponis", {}) or {}
        if not any(str(text or "").strip() for text in in_memory_ponis.values()):
            return False

        sync_fn = getattr(self, "_sync_active_technical_container_from_table", None)
        if not callable(sync_fn):
            return False

        synced = bool(sync_fn(show_errors=False))
        if synced and self._container_has_poni_datasets(container_path):
            self._log_technical_event(
                f"Embedded PONI synchronized into container before review validation for {container_id}"
            )
            return True
        return False

    def _prompt_poni_override_password(self, *, container_id: str) -> bool:
        try:
            from difra.gui.qt_compat import QLineEdit

            echo_mode = QLineEdit.Password
        except Exception:
            echo_mode = 0

        password, ok = QInputDialog.getText(
            self,
            "Override PONI Validation",
            "Enter password to accept and lock despite an out-of-zone PONI center:",
            echo_mode,
        )
        if not ok:
            self._log_technical_event(
                f"PONI override cancelled for {container_id}"
            )
            return False
        if str(password) != self.PONI_OVERRIDE_PASSWORD:
            QMessageBox.warning(
                self,
                "Wrong Password",
                "Password is incorrect. Lock will remain blocked.",
            )
            self._log_technical_event(
                f"PONI override rejected due to wrong password for {container_id}"
            )
            return False
        return True

    def _current_operator_id_for_review(self) -> str:
        operator_id = ""
        operator_manager = getattr(self, "operator_manager", None)
        if operator_manager is not None:
            getter = getattr(operator_manager, "get_current_operator_id", None)
            if callable(getter):
                try:
                    operator_id = str(getter() or "").strip()
                except Exception:
                    operator_id = ""

        if not operator_id and hasattr(self, "config") and isinstance(self.config, dict):
            operator_id = str(self.config.get("operator_id") or "").strip()

        return operator_id or "unknown"

    def _read_poni_review_state(self, container_path: Path):
        import h5py

        try:
            with h5py.File(container_path, "r") as h5f:
                status = self._decode_attr_text(
                    h5f.attrs.get(self.PONI_REVIEW_STATUS_ATTR, "pending")
                ).strip().lower()
                user = self._decode_attr_text(
                    h5f.attrs.get(self.PONI_REVIEW_USER_ATTR, "")
                ).strip()
                timestamp = self._decode_attr_text(
                    h5f.attrs.get(self.PONI_REVIEW_TS_ATTR, "")
                ).strip()
                notes = self._decode_attr_text(
                    h5f.attrs.get(self.PONI_REVIEW_NOTES_ATTR, "")
                ).strip()
                reject_reason = self._decode_attr_text(
                    h5f.attrs.get(self.PONI_REVIEW_REASON_ATTR, "")
                ).strip()
                raw_in_zone = h5f.attrs.get(self.PONI_REVIEW_IN_ZONE_ATTR, False)
                in_zone = bool(raw_in_zone)
        except Exception:
            return {
                "status": "pending",
                "user": "",
                "timestamp": "",
                "in_zone": False,
                "notes": "",
                "reject_reason": "",
            }

        return {
            "status": status or "pending",
            "user": user,
            "timestamp": timestamp,
            "in_zone": in_zone,
            "notes": notes,
            "reject_reason": reject_reason,
        }

    def _write_poni_review_state(
        self,
        container_path: Path,
        *,
        status: str,
        in_zone: bool,
        notes: str = "",
        reject_reason: str = "",
    ) -> bool:
        review_status = str(status or "pending").strip().lower() or "pending"
        review_user = self._current_operator_id_for_review()
        review_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        review_notes = str(notes or "").strip()
        reason_code = str(reject_reason or "").strip().lower()
        if review_status == "rejected":
            if not reason_code:
                logger.warning(
                    "Refusing to persist rejected PONI review without reject_reason for %s",
                    container_path,
                )
                return False
            if reason_code not in self.VALID_PONI_REJECT_REASONS:
                reason_code = "other"
        else:
            reason_code = ""

        return self._write_container_attrs(
            Path(container_path),
            {
                self.PONI_REVIEW_STATUS_ATTR: review_status,
                self.PONI_REVIEW_USER_ATTR: review_user,
                self.PONI_REVIEW_TS_ATTR: review_timestamp,
                self.PONI_REVIEW_IN_ZONE_ATTR: bool(in_zone),
                self.PONI_REVIEW_NOTES_ATTR: review_notes,
                self.PONI_REVIEW_REASON_ATTR: reason_code,
            },
        )

    def _run_poni_center_review_workflow(
        self,
        container_path: Path,
        *,
        container_id: str,
        prompt_reload_on_reject: bool = True,
    ) -> bool:
        cfg = self.config if hasattr(self, "config") and isinstance(self.config, dict) else {}
        validation_cfg = cfg.get("poni_center_validation", {})
        if not isinstance(validation_cfg, dict) or not validation_cfg.get("enabled", False):
            return True

        show_preview = getattr(self, "_show_poni_center_preview_for_container", None)
        if not callable(show_preview):
            QMessageBox.warning(
                self,
                "PONI Review Unavailable",
                "PONI center review UI is unavailable in this build.",
            )
            return False

        decision = show_preview(str(container_path), decision_mode=True)
        if decision is None:
            self._set_container_state(
                Path(container_path),
                state=self.STATE_PENDING_PONI_REVIEW,
                reason="review_unavailable",
            )
            QMessageBox.warning(
                self,
                "PONI Review Required",
                "PONI center preview could not be shown.\n\n"
                "Cannot proceed without user review.",
            )
            self._log_technical_event(
                f"PONI center review blocked: preview unavailable for {container_id}"
            )
            return False

        if bool(decision):
            self._ensure_embedded_poni_before_review(
                Path(container_path),
                container_id=container_id,
            )
            center_errors, _center_warnings = self._validate_poni_centers_for_container(
                Path(container_path)
            )
            in_zone = len(center_errors) == 0
            if in_zone:
                persisted = self._write_poni_review_state(
                    Path(container_path),
                    status="accepted",
                    in_zone=True,
                    notes="accepted_in_valid_zone",
                )
                if not persisted:
                    QMessageBox.warning(
                        self,
                        "PONI Review Error",
                        "Failed to persist PONI review acceptance. Lock cannot continue.",
                    )
                    return False
                self._set_container_state(
                    Path(container_path),
                    state=self.STATE_READY_TO_LOCK,
                    reason="poni_review_accepted_in_zone",
                )
                self._log_technical_event(
                    f"PONI center review accepted for {container_id}"
                )
                return True

            # Hard fail: user cannot proceed with an out-of-zone center acceptance.
            persisted = self._write_poni_review_state(
                Path(container_path),
                status="rejected",
                in_zone=False,
                notes="accept_attempt_rejected_out_of_zone",
                reject_reason="center_out_of_zone",
            )
            if not persisted:
                QMessageBox.warning(
                    self,
                    "PONI Review Error",
                    "Failed to persist reject reason for invalid PONI center. Lock cannot continue.",
                )
                return False
            self._set_container_state(
                Path(container_path),
                state=self.STATE_REJECTED_BLOCKED,
                reason="center_out_of_zone",
            )
            details = "\n".join(f"- {err}" for err in center_errors[:4])
            if len(center_errors) > 4:
                details += f"\n- ... and {len(center_errors) - 4} more"
            QMessageBox.critical(
                self,
                "PONI Validation Failed",
                "PONI center is outside the allowed zone.\n\n"
                f"{details}\n\n"
                "Lock cannot continue.\n"
                f"Adjust the PONI values or update poni_center_validation in {self._poni_validation_config_label()}, "
                "then load valid PONI files.",
            )
            override_reply = QMessageBox.question(
                self,
                "Override Lock",
                "Accept and lock this technical container anyway?\n\n"
                "This will be recorded as a password override outside the allowed zone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if override_reply == QMessageBox.Yes:
                if self._prompt_poni_override_password(container_id=container_id):
                    persisted = self._write_poni_review_state(
                        Path(container_path),
                        status="accepted",
                        in_zone=False,
                        notes="accepted_password_override_out_of_zone",
                    )
                    if not persisted:
                        QMessageBox.warning(
                            self,
                            "PONI Review Error",
                            "Failed to persist password override state. Lock cannot continue.",
                        )
                        return False
                    self._set_container_state(
                        Path(container_path),
                        state=self.STATE_READY_TO_LOCK,
                        reason="poni_review_override_out_of_zone",
                    )
                    self._log_technical_event(
                        f"PONI review override accepted by password for {container_id}"
                    )
                    return True
            if prompt_reload_on_reject:
                retry = QMessageBox.question(
                    self,
                    "Reload PONI",
                    "Load new PONI files now?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if retry == QMessageBox.Yes:
                    return bool(self._launch_poni_update_for_container(Path(container_path)))

            self._set_container_state(
                Path(container_path),
                state=self.STATE_REJECTED_BLOCKED,
                reason="reload_declined_after_reject",
            )
            QMessageBox.warning(
                self,
                "Lock Blocked",
                "Technical container remains unlocked.\n\n"
                "Without lock, downstream measurements will not be available.",
            )
            return False

        persisted = self._write_poni_review_state(
            Path(container_path),
            status="rejected",
            in_zone=False,
            notes="rejected_by_user",
            reject_reason="user_rejected_preview",
        )
        if not persisted:
            QMessageBox.warning(
                self,
                "PONI Review Error",
                "Failed to persist reject reason. Lock cannot continue.",
            )
            return False
        self._set_container_state(
            Path(container_path),
            state=self.STATE_REJECTED_BLOCKED,
            reason="user_rejected_preview",
        )
        self._log_technical_event(
            f"PONI center review rejected for {container_id}"
        )

        if prompt_reload_on_reject:
            retry = QMessageBox.question(
                self,
                "PONI Rejected",
                "PONI centers were rejected.\n\nLoad new PONI files now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if retry == QMessageBox.Yes:
                return bool(self._launch_poni_update_for_container(Path(container_path)))

        self._set_container_state(
            Path(container_path),
            state=self.STATE_REJECTED_BLOCKED,
            reason="reload_declined_after_reject",
        )
        QMessageBox.warning(
            self,
            "Lock Blocked",
            "PONI review was rejected and no replacement was loaded.\n\n"
            "Technical container remains unlocked.\n"
            "Without lock, downstream measurements will not be available.",
        )
        return False

