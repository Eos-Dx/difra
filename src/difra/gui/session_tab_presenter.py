"""Presenter/service helpers for Session tab data and rendering."""

from dataclasses import dataclass
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

import h5py
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem


@dataclass(frozen=True)
class ActiveSessionViewState:
    """View state used by Session tab active-session section."""

    info_text: str
    close_enabled: bool
    upload_enabled: bool


class SessionTabPresenter:
    """Helpers for reading session metadata and rendering Session tab tables."""

    _ARCHIVE_STAMP_RE = re.compile(r"(\d{8}_\d{6})(?:_\d+)?$")
    _TRANSFER_STATUS_NOT_COMPLETE = "NOT_COMPLETE"

    @staticmethod
    def decode_attr(value):
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    @classmethod
    def _extract_archive_stamp(cls, folder_name: str) -> str:
        """Extract archive timestamp token from archive folder naming conventions."""
        raw = str(folder_name or "").strip()
        if not raw:
            return ""
        match = cls._ARCHIVE_STAMP_RE.search(raw)
        return match.group(1) if match else ""

    @staticmethod
    def scan_pending_session_containers(measurements_folder: Path) -> List[Path]:
        folder = Path(measurements_folder)
        if not folder.exists():
            return []
        return sorted(
            [path for path in folder.glob("session_*.nxs.h5") if path.is_file()]
        )

    @staticmethod
    def scan_archived_session_containers(archive_folder: Path) -> List[Path]:
        folder = Path(archive_folder)
        if not folder.exists():
            return []
        return sorted(
            [path for path in folder.rglob("session_*.nxs.h5") if path.is_file()]
        )

    @classmethod
    def read_session_container_metadata(
        cls,
        container_path: Path,
        *,
        schema: Any,
        container_manager: Any,
    ) -> Dict[str, str]:
        """Read core session metadata used by queue/archive tables."""
        path = Path(container_path)
        info: Dict[str, str] = {
            "file_name": path.name,
            "path": str(path),
            "sample_id": "UNKNOWN",
            "specimenId": "UNKNOWN",
            "study_name": "UNSPECIFIED",
            "project_id": "UNSPECIFIED",
            "operator_id": "UNKNOWN",
            "uploaded_by": "",
            "upload_timestamp": "",
            "upload_session_id": "",
            "upload_status": "",
            "upload_response_checksum_sha256": "",
            "created": "",
            "status": "UNKNOWN",
            "lock_status": "UNKNOWN",
            "transfer_status": "UNSENT",
            "session_id": "",
            "archived": "",
            "session_state": "",
        }

        try:
            with h5py.File(path, "r") as h5f:
                specimen_id = h5f.attrs.get("specimenId")
                if specimen_id is None:
                    specimen_id = h5f.attrs.get(schema.ATTR_SAMPLE_ID, "UNKNOWN")
                info["sample_id"] = str(cls.decode_attr(specimen_id))
                info["specimenId"] = info["sample_id"]
                info["study_name"] = str(
                    cls.decode_attr(
                        h5f.attrs.get(schema.ATTR_STUDY_NAME, "UNSPECIFIED")
                    )
                )
                info["project_id"] = str(
                    cls.decode_attr(
                        h5f.attrs.get(
                            "matadorProjectName",
                            h5f.attrs.get("project_id", info["study_name"]),
                        )
                    )
                )
                info["operator_id"] = str(
                    cls.decode_attr(h5f.attrs.get(schema.ATTR_OPERATOR_ID, "UNKNOWN"))
                )
                info["uploaded_by"] = str(
                    cls.decode_attr(
                        h5f.attrs.get(
                            getattr(schema, "ATTR_UPLOADED_BY", "uploaded_by"),
                            "",
                        )
                    )
                )
                info["upload_timestamp"] = str(
                    cls.decode_attr(
                        h5f.attrs.get(
                            getattr(schema, "ATTR_UPLOAD_TIMESTAMP", "upload_timestamp"),
                            "",
                        )
                    )
                )
                info["upload_session_id"] = str(
                    cls.decode_attr(
                        h5f.attrs.get("upload_session_id", "")
                    )
                )
                info["upload_status"] = str(
                    cls.decode_attr(
                        h5f.attrs.get("upload_status", "")
                    )
                )
                info["upload_response_checksum_sha256"] = str(
                    cls.decode_attr(
                        h5f.attrs.get("upload_response_checksum_sha256", "")
                    )
                )
                info["created"] = str(
                    cls.decode_attr(
                        h5f.attrs.get(schema.ATTR_CREATION_TIMESTAMP, "")
                    )
                )
                info["session_id"] = str(
                    cls.decode_attr(h5f.attrs.get(schema.ATTR_SESSION_ID, ""))
                )
                info["session_state"] = str(
                    cls.decode_attr(
                        h5f.attrs.get("session_state", "")
                    )
                )
                explicit_transfer_status = str(
                    cls.decode_attr(
                        h5f.attrs.get(
                            getattr(schema, "ATTR_TRANSFER_STATUS", "transfer_status"),
                            "",
                        )
                    )
                    or ""
                ).strip()
                if explicit_transfer_status.upper() == cls._TRANSFER_STATUS_NOT_COMPLETE:
                    transfer_status = explicit_transfer_status
                else:
                    transfer_status = ""
                locked = container_manager.is_container_locked(path)
                info["lock_status"] = "LOCKED" if locked else "UNLOCKED"
                if not transfer_status:
                    get_transfer_status = getattr(container_manager, "get_transfer_status", None)
                    if callable(get_transfer_status):
                        try:
                            transfer_status = str(get_transfer_status(path) or "")
                        except Exception:
                            transfer_status = ""
                if not transfer_status:
                    transfer_status = str(
                        cls.decode_attr(
                            h5f.attrs.get(
                                getattr(schema, "ATTR_TRANSFER_STATUS", "transfer_status"),
                                getattr(schema, "TRANSFER_STATUS_UNSENT", "unsent"),
                            )
                        )
                        or getattr(schema, "TRANSFER_STATUS_UNSENT", "unsent")
                    )
                info["transfer_status"] = str(transfer_status).upper()
                info["status"] = (
                    f"{info['lock_status']} / {info['transfer_status']}"
                )
        except Exception as exc:
            info["status"] = f"ERROR ({exc})"

        try:
            parent_name = path.parent.name
            info["archived"] = cls._extract_archive_stamp(parent_name)
        except Exception:
            pass
        if not info["archived"]:
            info["archived"] = time.strftime(
                "%Y%m%d_%H%M%S", time.localtime(path.stat().st_mtime)
            )

        return info

    @classmethod
    def build_pending_rows(
        cls,
        measurements_folder: Path,
        *,
        schema: Any,
        container_manager: Any,
    ) -> List[Dict[str, str]]:
        """Return metadata rows for pending session queue."""
        return [
            cls.read_session_container_metadata(
                container_path=container_path,
                schema=schema,
                container_manager=container_manager,
            )
            for container_path in cls.scan_pending_session_containers(measurements_folder)
        ]

    @classmethod
    def build_archived_rows(
        cls,
        archive_folder: Path,
        *,
        schema: Any,
        container_manager: Any,
    ) -> List[Dict[str, str]]:
        """Return metadata rows for archived session list."""
        return [
            cls.read_session_container_metadata(
                container_path=container_path,
                schema=schema,
                container_manager=container_manager,
            )
            for container_path in cls.scan_archived_session_containers(archive_folder)
        ]

    @staticmethod
    def _readonly_item(value: Any) -> QTableWidgetItem:
        item = QTableWidgetItem(str(value))
        item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        return item

    @classmethod
    def populate_pending_table(
        cls,
        table: QTableWidget,
        rows: Sequence[Dict[str, str]],
    ) -> None:
        """Populate pending queue table from presenter row data."""
        table.setRowCount(0)
        for row_index, row_data in enumerate(rows):
            table.insertRow(row_index)
            for col, key in enumerate(
                [
                    "file_name",
                    "sample_id",
                    "study_name",
                    "operator_id",
                    "uploaded_by",
                    "created",
                    "status",
                ],
                start=0,
            ):
                table.setItem(row_index, col, cls._readonly_item(row_data.get(key, "")))

            table.setItem(row_index, 7, cls._readonly_item(row_data.get("path", "")))

    @classmethod
    def populate_archive_table(
        cls,
        table: QTableWidget,
        rows: Sequence[Dict[str, str]],
    ) -> None:
        """Populate archive table from presenter row data."""
        table.setRowCount(0)
        for row_index, row_data in enumerate(rows):
            table.insertRow(row_index)
            for col, key in enumerate(
                [
                    "file_name",
                    "sample_id",
                    "project_id",
                    "study_name",
                    "operator_id",
                    "uploaded_by",
                    "created",
                    "archived",
                    "status",
                ]
            ):
                table.setItem(row_index, col, cls._readonly_item(row_data.get(key, "")))

            table.setItem(row_index, 9, cls._readonly_item(row_data.get("path", "")))

    @staticmethod
    def format_active_session_info(info: Dict[str, Any]) -> str:
        """Format active session info as HTML for QLabel."""
        transfer_status = str(info.get("transfer_status") or "unsent").upper()
        session_state = str(info.get("session_state") or "").strip()
        state_suffix = f" ({session_state})" if session_state else ""
        return (
            f"<b>Specimen ID:</b> {info['sample_id']}<br>"
            f"<b>Study:</b> {info.get('study_name', 'UNSPECIFIED')}<br>"
            f"<b>Session ID:</b> {info['session_id']}<br>"
            f"<b>Operator:</b> {info['operator_id']}<br>"
            f"<b>Container:</b> {Path(info['session_path']).name}<br>"
            f"<b>Status:</b> {'🔒 Locked' if info['is_locked'] else '🔓 Unlocked'} / {transfer_status}{state_suffix}"
        )

    @classmethod
    def build_active_session_view_state(
        cls, info: Dict[str, Any]
    ) -> ActiveSessionViewState:
        """Build button/text state for active session info panel."""
        if not info.get("active"):
            return ActiveSessionViewState(
                info_text="No active session",
                close_enabled=False,
                upload_enabled=False,
            )

        is_locked = bool(info["is_locked"])
        return ActiveSessionViewState(
            info_text=cls.format_active_session_info(info),
            close_enabled=not is_locked,
            upload_enabled=is_locked,
        )
