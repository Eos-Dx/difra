from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Dict, List, Optional


def _normalize_iso_date(value: Any) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace").strip()
    else:
        text = str(value or "").strip()
    if not text:
        return ""
    candidate = text[:10]
    try:
        time.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return ""
    return candidate


@dataclass
class SendArchiveResult:
    """Result summary for batch send+archive workflow."""

    moved: int = 0
    failed: List[str] = field(default_factory=list)
    archived_paths: List[Path] = field(default_factory=list)
    archived_active_session: bool = False
    old_format_paths: List[Path] = field(default_factory=list)
    old_format_failed: List[str] = field(default_factory=list)
    cleaned_artifacts: int = 0
    upload_session_id: str = ""
    upload_success: int = 0
    upload_pending: int = 0
    upload_failed: int = 0
    archived_complete: int = 0
    archived_not_complete: int = 0


@dataclass(frozen=True)
class UploadStubResult:
    """Upload response payload for Matador workflow."""

    success: bool
    upload_session_id: str
    message: str
    bytes_uploaded: int
    local_checksum_sha256: str
    response_checksum_sha256: str
    remote_container_id: str
    zip_file_id: str = ""
    zip_upload_status: str = ""
    zip_processing_status: str = ""
    zip_checksum_sha256: str = ""
    zip_size_bytes: int = 0
    zip_path: str = ""
    h5_file_id: str = ""
    h5_upload_status: str = ""
    h5_processing_status: str = ""
    verification_pending: bool = False
    resolved_matador_specimen_id: Optional[int] = None
    specimen_resolution_message: str = ""
