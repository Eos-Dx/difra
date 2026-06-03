from __future__ import annotations

from difra.gui.session_transfer_status import (
    TRANSFER_STATUS_REQ_RESEND,
    TRANSFER_STATUS_SENT,
    TRANSFER_STATUS_UNSENT,
    normalize_archive_status_filter,
    normalize_transfer_status,
    transfer_status_from_row,
    transfer_status_matches_filter,
)


def test_normalize_transfer_status_accepts_archive_spellings():
    assert normalize_transfer_status("req_resend") == TRANSFER_STATUS_REQ_RESEND
    assert normalize_transfer_status("REQ RESEND") == TRANSFER_STATUS_REQ_RESEND
    assert normalize_transfer_status("sent") == TRANSFER_STATUS_SENT
    assert normalize_transfer_status("unsent") == TRANSFER_STATUS_UNSENT


def test_transfer_status_from_row_detects_req_resend_status_text():
    row = {
        "status": "LOCKED / REQ_RESEND",
        "transfer_status": "",
    }

    assert transfer_status_from_row(row) == TRANSFER_STATUS_REQ_RESEND


def test_transfer_status_matches_archive_filters():
    assert normalize_archive_status_filter("All statuses") == ""
    assert transfer_status_matches_filter("REQ_RESEND", "REQ_RESEND")
    assert transfer_status_matches_filter("req_resend", "req resend")
    assert not transfer_status_matches_filter("SENT", "REQ_RESEND")
