"""Editable session metadata helpers for SessionManager."""

from typing import Dict

import h5py

from difra.utils.logger import get_module_logger

logger = get_module_logger(__name__)


class SessionManagerMetadataMixin:
    """Editable metadata updates and current-session summaries."""

    def update_sample_id(self, new_sample_id: str) -> bool:
        """Update the sample ID in the session container.

        Can only update if container is unlocked.

        Args:
            new_sample_id: New sample identifier

        Returns:
            True if updated successfully, False if locked or failed
        """
        self._check_active()

        if self.is_locked():
            logger.warning(
                "Cannot update specimenId: container is locked",
                specimen_id=new_sample_id,
            )
            return False

        try:
            import h5py

            with h5py.File(self.session_path, "a") as f:
                old_sample_id = f.attrs.get(self.schema.ATTR_SAMPLE_ID, "unknown")
                f.attrs[self.schema.ATTR_SAMPLE_ID] = new_sample_id
                f.attrs["specimenId"] = new_sample_id
                if self.schema.GROUP_SAMPLE in f:
                    f[self.schema.GROUP_SAMPLE].attrs[self.schema.ATTR_SAMPLE_ID] = (
                        new_sample_id
                    )
                    f[self.schema.GROUP_SAMPLE].attrs["specimenId"] = new_sample_id

            refresh_summary = getattr(self.writer, "refresh_human_summary", None)
            if callable(refresh_summary):
                refresh_summary(self.session_path)

            self.sample_id = new_sample_id
            self.specimen_id = new_sample_id

            logger.info(
                "Updated sample_id in session container",
                old_sample_id=old_sample_id,
                new_sample_id=new_sample_id,
                session_path=str(self.session_path),
            )

            return True

        except Exception as e:
            logger.error(
                f"Failed to update sample_id: {e}",
                exc_info=True,
            )
            return False

    def update_container_information(
        self,
        *,
        specimen_id: str,
        study_name: str,
        project_id: str,
        operator_id: str,
        matador_project_id=None,
        matador_project_name: str = "",
        matador_study_id=None,
        matador_machine_id=None,
        password_authorized: bool = False,
    ) -> bool:
        """Update editable session metadata without touching measurements."""
        self._check_active()

        if self.is_locked():
            logger.warning(
                "Cannot update session container information: container is locked",
                session_path=str(self.session_path),
            )
            return False

        specimen_text = self._as_text(specimen_id, "").strip()
        study_text = self._as_text(study_name, "").strip()
        project_text = self._as_text(project_id, "").strip()
        operator_text = self._as_text(operator_id, "").strip()
        project_name_text = (
            self._as_text(matador_project_name, "").strip() or project_text
        )

        if not specimen_text or not study_text or not project_text or not operator_text:
            return False

        def _optional_int(value):
            text = self._as_text(value, "").strip()
            if not text:
                return None
            return int(text)

        try:
            project_id_int = _optional_int(matador_project_id)
            study_id_int = _optional_int(matador_study_id)
            machine_id_int = _optional_int(matador_machine_id)
        except Exception:
            return False

        try:
            with h5py.File(self.session_path, "a") as h5f:
                old_specimen_text = self._as_text(
                    h5f.attrs.get(
                        "specimenId",
                        h5f.attrs.get(self.schema.ATTR_SAMPLE_ID, ""),
                    ),
                    "",
                ).strip()
                old_study_text = self._as_text(
                    h5f.attrs.get(self.schema.ATTR_STUDY_NAME, ""),
                    "",
                ).strip()
                old_project_text = ""
                if hasattr(self.schema, "ATTR_PROJECT_ID"):
                    old_project_text = self._as_text(
                        h5f.attrs.get(self.schema.ATTR_PROJECT_ID, ""),
                        "",
                    ).strip()
                old_operator_text = self._as_text(
                    h5f.attrs.get(self.schema.ATTR_OPERATOR_ID, ""),
                    "",
                ).strip()

                h5f.attrs[self.schema.ATTR_SAMPLE_ID] = specimen_text
                h5f.attrs["specimenId"] = specimen_text
                h5f.attrs[self.schema.ATTR_STUDY_NAME] = study_text
                h5f.attrs[self.schema.ATTR_OPERATOR_ID] = operator_text
                if hasattr(self.schema, "ATTR_PROJECT_ID"):
                    h5f.attrs[self.schema.ATTR_PROJECT_ID] = project_text

                if project_id_int is None:
                    if "matadorProjectId" in h5f.attrs:
                        del h5f.attrs["matadorProjectId"]
                else:
                    h5f.attrs["matadorProjectId"] = int(project_id_int)
                h5f.attrs["matadorProjectName"] = project_name_text

                if study_id_int is None:
                    if "matadorStudyId" in h5f.attrs:
                        del h5f.attrs["matadorStudyId"]
                else:
                    h5f.attrs["matadorStudyId"] = int(study_id_int)

                if machine_id_int is None:
                    if "matadorMachineId" in h5f.attrs:
                        del h5f.attrs["matadorMachineId"]
                else:
                    h5f.attrs["matadorMachineId"] = int(machine_id_int)

                sample_group = h5f.get(self.schema.GROUP_SAMPLE)
                if sample_group is not None:
                    sample_group.attrs[self.schema.ATTR_SAMPLE_ID] = specimen_text
                    sample_group.attrs["specimenId"] = specimen_text
                    sample_group.attrs[self.schema.ATTR_STUDY_NAME] = study_text
                    if hasattr(self.schema, "ATTR_PROJECT_ID"):
                        sample_group.attrs[self.schema.ATTR_PROJECT_ID] = project_text

                user_group = h5f.get(self.schema.GROUP_USER)
                if user_group is not None:
                    user_group.attrs[self.schema.ATTR_OPERATOR_ID] = operator_text

            refresh_summary = getattr(self.writer, "refresh_human_summary", None)
            if callable(refresh_summary):
                refresh_summary(self.session_path)

            self.sample_id = specimen_text
            self.specimen_id = specimen_text
            self.study_name = study_text
            self.operator_id = operator_text
            specimen_changed = old_specimen_text != specimen_text
            event_message = "Session container information updated"
            if specimen_changed:
                password_text = " under password" if password_authorized else ""
                event_message = (
                    f"Specimen ID changed{password_text}: "
                    f"{old_specimen_text or '<empty>'} -> {specimen_text}"
                )
            self.log_event(
                message=event_message,
                event_type="container_information_updated",
                details={
                    "password_authorized": bool(password_authorized),
                    "specimen_id_changed": bool(specimen_changed),
                    "previous_specimenId": old_specimen_text,
                    "new_specimenId": specimen_text,
                    "previous_study_name": old_study_text,
                    "new_study_name": study_text,
                    "previous_project_id": old_project_text,
                    "new_project_id": project_text,
                    "previous_operator_id": old_operator_text,
                    "new_operator_id": operator_text,
                },
            )
            logger.info(
                "Updated session container information",
                session_path=str(self.session_path),
                specimen_id=specimen_text,
                study_name=study_text,
                project_id=project_text,
                operator_id=operator_text,
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to update session container information: %s",
                exc,
                exc_info=True,
            )
            return False

    def get_session_info(self) -> Dict:
        """Get current session information.

        Returns:
            Dict with session metadata
        """
        if not self.is_session_active():
            return {"active": False}

        try:
            with h5py.File(self.session_path, "r") as h5f:
                persisted_state = (
                    self._as_text(
                        h5f.attrs.get(self.SESSION_STATE_ATTR),
                        "",
                    )
                    .strip()
                    .lower()
                )
                if persisted_state in self.VALID_SESSION_STATES:
                    self.session_state = persisted_state
        except Exception:
            pass

        transfer_status = "unsent"
        try:
            with h5py.File(self.session_path, "r") as h5f:
                explicit_transfer_status = self._as_text(
                    h5f.attrs.get("transfer_status"),
                    "",
                ).strip()
                if explicit_transfer_status.lower() in {"not_complete", "req_resend"}:
                    transfer_status = explicit_transfer_status
                else:
                    get_transfer_status = getattr(
                        self.container_manager, "get_transfer_status", None
                    )
                    if callable(get_transfer_status):
                        try:
                            transfer_status = str(
                                get_transfer_status(self.session_path) or "unsent"
                            )
                        except Exception:
                            transfer_status = "unsent"
        except Exception:
            get_transfer_status = getattr(
                self.container_manager, "get_transfer_status", None
            )
            if callable(get_transfer_status):
                try:
                    transfer_status = str(
                        get_transfer_status(self.session_path) or "unsent"
                    )
                except Exception:
                    transfer_status = "unsent"

        return {
            "active": True,
            "session_id": self.session_id,
            "session_path": str(self.session_path),
            "sample_id": self.sample_id,
            "specimenId": self.specimen_id or self.sample_id,
            "study_name": self.study_name,
            "operator_id": self.operator_id,
            "machine_name": self.machine_name,
            "beam_energy_kev": self.beam_energy_kev,
            "technical_container_path": str(self.technical_container_path or ""),
            "technical_container_id": str(self.technical_container_id or ""),
            "is_locked": self.is_locked(),
            "transfer_status": transfer_status,
            "session_state": str(self.session_state or self.SESSION_STATE_DRAFT),
            "i0_recorded": self.i0_counter is not None,
            "i_recorded": self.i_counter is not None,
            "attenuation_complete": (
                self.i0_counter is not None and self.i_counter is not None
            ),
        }
