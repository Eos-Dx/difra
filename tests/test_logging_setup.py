from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import difra.utils.logging_setup as logging_setup


def _record(message: str = "msg", level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_context_filter_adds_and_clears_context_values():
    context_filter = logging_setup.ContextFilter()
    record = _record()

    assert context_filter.filter(record) is True
    assert record.hardware_state == "unknown"
    assert not hasattr(record, "session_id")

    context_filter.set_context(session_id="S1", hardware_state="ready", measurement_id="M9")
    record2 = _record("second")
    assert context_filter.filter(record2) is True
    assert record2.session_id == "S1"
    assert record2.hardware_state == "ready"
    assert record2.measurement_id == "M9"

    context_filter.clear_context()
    record3 = _record("third")
    context_filter.filter(record3)
    assert record3.hardware_state == "unknown"
    assert not hasattr(record3, "session_id")


def test_performance_filter_sets_duration_between_start_and_end():
    perf_filter = logging_setup.PerformanceFilter()
    start = _record("start")
    start.operation = "scan_start"
    start.created = 10.0
    end = _record("end")
    end.operation = "scan_end"
    end.created = 14.5

    assert perf_filter.filter(start) is True
    assert perf_filter.filter(end) is True
    assert end.duration == 4.5


def test_structured_formatter_outputs_json_with_context_and_exception():
    formatter = logging_setup.StructuredFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = _record("failed", level=logging.ERROR)
        record.session_id = "S2"
        record.hardware_state = "error"
        record.measurement_id = "M2"
        record.exc_info = sys.exc_info()

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "failed"
    assert payload["level"] == "ERROR"
    assert payload["session_id"] == "S2"
    assert payload["hardware_state"] == "error"
    assert payload["measurement_id"] == "M2"
    assert "exception" in payload


def test_default_log_path_uses_state_home_on_non_windows(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))

    path = logging_setup._default_log_path("UlsterTest")

    assert path.name == "ulster.log"
    assert "UlsterTest" in str(path)
    assert path.parent.exists()


def test_env_truthy_recognizes_common_truthy_values():
    for value in ("1", "true", "TRUE", "yes", "on"):
        assert logging_setup._env_truthy(value) is True
    for value in ("0", "false", "", None):
        assert logging_setup._env_truthy(value) is False


def test_log_context_sets_thread_local_context_during_block():
    record_before = _record("before")
    logging_setup._context_filter.filter(record_before)
    assert not hasattr(record_before, "session_id")

    with logging_setup.log_context(session_id="SCTX", hardware_state="active"):
        record_inside = _record("inside")
        logging_setup._context_filter.filter(record_inside)
        assert record_inside.session_id == "SCTX"
        assert record_inside.hardware_state == "active"

    record_after = _record("after")
    logging_setup._context_filter.filter(record_after)
    assert not hasattr(record_after, "session_id")


def test_log_exceptions_decorator_reraises_or_swallows(monkeypatch):
    calls = []
    fake_logger = SimpleNamespace(
        exception=lambda *args, **kwargs: calls.append((args, kwargs))
    )

    @logging_setup.log_exceptions(fake_logger, reraise=False)
    def _safe():
        raise ValueError("safe")

    @logging_setup.log_exceptions(fake_logger, reraise=True)
    def _strict():
        raise ValueError("strict")

    assert _safe() is None
    try:
        _strict()
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")

    assert len(calls) == 2


def test_init_logging_from_env_returns_none_if_handlers_already_exist(tmp_path: Path):
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    try:
        file_handler = logging.handlers.RotatingFileHandler(tmp_path / "log.txt")
        stream_handler = logging.StreamHandler()
        root.handlers = [file_handler, stream_handler]
        path = logging_setup.init_logging_from_env()
        assert path is None
    finally:
        for handler in root.handlers:
            try:
                handler.close()
            except Exception:
                pass
        root.handlers = old_handlers


def test_init_logging_from_env_delegates_to_setup_logging(monkeypatch, tmp_path: Path):
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_stdout, old_stderr, old_excepthook = sys.stdout, sys.stderr, sys.excepthook
    for key in (
        "ULSTER_LOG_FILE",
        "ULSTER_LOG_DIR",
        "ULSTER_LOG_LEVEL",
        "ULSTER_LOG_CONSOLE_LEVEL",
        "ULSTER_LOG_FILE_LEVEL",
        "ULSTER_LOG_STRUCTURED",
        "ULSTER_LOG_STDIO",
        "ULSTER_LOG_MAX_BYTES",
        "ULSTER_LOG_BACKUP_COUNT",
    ):
        os.environ.pop(key, None)
    try:
        root.handlers = []
        monkeypatch.setenv("ULSTER_LOG_FILE", str(tmp_path / "env.log"))
        monkeypatch.setenv("ULSTER_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("ULSTER_LOG_CONSOLE_LEVEL", "INFO")
        monkeypatch.setenv("ULSTER_LOG_FILE_LEVEL", "WARNING")
        monkeypatch.setenv("ULSTER_LOG_STRUCTURED", "1")
        monkeypatch.setenv("ULSTER_LOG_STDIO", "0")
        monkeypatch.setenv("ULSTER_LOG_MAX_BYTES", "2048")
        monkeypatch.setenv("ULSTER_LOG_BACKUP_COUNT", "2")

        calls = []

        def _fake_setup_logging(**kwargs):
            calls.append(kwargs)
            return Path(kwargs["log_path"]).resolve()

        monkeypatch.setattr(logging_setup, "setup_logging", _fake_setup_logging)
        monkeypatch.setattr(logging_setup, "configure_third_party_logging", lambda: calls.append("third_party"))

        path = logging_setup.init_logging_from_env(default_level=logging.INFO)

        assert path == (tmp_path / "env.log").resolve()
        setup_kwargs = calls[0]
        assert setup_kwargs["structured"] is True
        assert setup_kwargs["capture_stdio"] is False
        assert setup_kwargs["config"]["max_bytes"] == 2048
        assert setup_kwargs["config"]["backup_count"] == 2
        assert "third_party" in calls
    finally:
        root.handlers = old_handlers
        sys.stdout, sys.stderr, sys.excepthook = old_stdout, old_stderr, old_excepthook
