from __future__ import annotations

from typing import Mapping


TRANSFER_STATUS_UNSENT = "UNSENT"
TRANSFER_STATUS_SENT = "SENT"
TRANSFER_STATUS_REQ_RESEND = "REQ_RESEND"
TRANSFER_STATUS_NOT_COMPLETE = "NOT_COMPLETE"

ARCHIVE_STATUS_FILTER_OPTIONS = [
    "All statuses",
    "Unsent",
    "REQ_RESEND",
    "Sent",
    "Not complete",
]


def normalize_transfer_status(value: object) -> str:
    raw = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if raw in {"REQ_RESEND", "REQUIRES_RESEND", "REQUIRED_RESEND"}:
        return TRANSFER_STATUS_REQ_RESEND
    if raw in {"NOT_COMPLETE", "INCOMPLETE"}:
        return TRANSFER_STATUS_NOT_COMPLETE
    if raw == "SENT":
        return TRANSFER_STATUS_SENT
    if raw == "UNSENT":
        return TRANSFER_STATUS_UNSENT
    return raw


def transfer_status_from_row(row: Mapping[str, object]) -> str:
    explicit = normalize_transfer_status(row.get("transfer_status"))
    if explicit:
        return explicit
    status_text = normalize_transfer_status(row.get("status"))
    if TRANSFER_STATUS_NOT_COMPLETE in status_text:
        return TRANSFER_STATUS_NOT_COMPLETE
    if TRANSFER_STATUS_REQ_RESEND in status_text:
        return TRANSFER_STATUS_REQ_RESEND
    if TRANSFER_STATUS_UNSENT in status_text:
        return TRANSFER_STATUS_UNSENT
    if TRANSFER_STATUS_SENT in status_text:
        return TRANSFER_STATUS_SENT
    return ""


def normalize_archive_status_filter(value: object) -> str:
    raw = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    if raw in {"", "all statuses"}:
        return ""
    if raw == "req resend":
        return TRANSFER_STATUS_REQ_RESEND
    if raw == "not complete":
        return TRANSFER_STATUS_NOT_COMPLETE
    if raw == "sent":
        return TRANSFER_STATUS_SENT
    if raw == "unsent":
        return TRANSFER_STATUS_UNSENT
    return raw.upper().replace(" ", "_")


def transfer_status_matches_filter(status: str, status_filter: str) -> bool:
    normalized_filter = normalize_archive_status_filter(status_filter)
    if not normalized_filter:
        return True
    return normalize_transfer_status(status) == normalized_filter
