"""Application lifecycle hooks for daily valid-container reports."""

from __future__ import annotations

from datetime import date, timedelta
import logging
from threading import Lock, Thread
from typing import Any, Dict, Optional

from difra.gui.daily_valid_container_reporter import (
    run_daily_report_for_date_from_config,
)

logger = logging.getLogger(__name__)

_running_lock = Lock()
_running_keys = set()


def _start_report_thread(
    *,
    config: Optional[Dict[str, Any]],
    report_date: date,
    reason: str,
    daemon: bool,
) -> Optional[Thread]:
    key = f"{reason}:{report_date.isoformat()}"
    with _running_lock:
        if key in _running_keys:
            return None
        _running_keys.add(key)

    def _run() -> None:
        try:
            result = run_daily_report_for_date_from_config(
                config=config,
                report_date=report_date,
                send_email=True,
                allow_interactive_setup=False,
                skip_if_sent=True,
                resend_if_changed=True,
                skip_if_no_containers=True,
            )
            logger.info(
                "Daily report lifecycle job finished",
                extra={
                    "report_reason": reason,
                    "report_date": report_date.isoformat(),
                    "scanned": result.scanned,
                    "valid_containers": result.valid_containers,
                    "email_result": result.email_result,
                },
            )
        except Exception:
            logger.warning(
                "Daily report lifecycle job failed",
                extra={
                    "report_reason": reason,
                    "report_date": report_date.isoformat(),
                },
                exc_info=True,
            )
        finally:
            with _running_lock:
                _running_keys.discard(key)

    thread = Thread(
        target=_run,
        name=f"difra-daily-report-{reason}-{report_date.isoformat()}",
        daemon=daemon,
    )
    thread.start()
    return thread


def start_previous_day_report_on_startup(
    config: Optional[Dict[str, Any]],
) -> Optional[Thread]:
    return _start_report_thread(
        config=config,
        report_date=date.today() - timedelta(days=1),
        reason="startup-previous-day",
        daemon=True,
    )


def start_today_report_on_shutdown(
    config: Optional[Dict[str, Any]],
) -> Optional[Thread]:
    return _start_report_thread(
        config=config,
        report_date=date.today(),
        reason="shutdown-current-day",
        daemon=False,
    )
