from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import difra.utils.logger as logger_module


class _FakeBackendLogger:
    def __init__(self) -> None:
        self.calls = []

    def debug(self, msg, *args, **kwargs):
        self.calls.append(("debug", msg, args, kwargs))

    def info(self, msg, *args, **kwargs):
        self.calls.append(("info", msg, args, kwargs))

    def warning(self, msg, *args, **kwargs):
        self.calls.append(("warning", msg, args, kwargs))

    def error(self, msg, *args, **kwargs):
        self.calls.append(("error", msg, args, kwargs))

    def exception(self, msg, *args, **kwargs):
        self.calls.append(("exception", msg, args, kwargs))

    def critical(self, msg, *args, **kwargs):
        self.calls.append(("critical", msg, args, kwargs))


def test_split_log_kwargs_moves_control_fields_to_log_kwargs():
    kwargs = {
        "exc_info": True,
        "stacklevel": 3,
        "detector": "det_a",
        "extra": {"ignore": True},
    }

    log_kwargs, extra_kwargs = logger_module.UlsterLogger._split_log_kwargs(kwargs)

    assert log_kwargs == {"exc_info": True, "stacklevel": 3}
    assert extra_kwargs == {"detector": "det_a"}


def test_ulster_logger_routes_messages_with_structured_extra(monkeypatch):
    backend = _FakeBackendLogger()
    monkeypatch.setattr(logger_module, "get_logger", lambda name: backend)
    ul = logger_module.UlsterLogger("difra.test")

    ul.debug("dbg", detector="det_a")
    ul.info("inf", sample="s1")
    ul.warning("warn", step=2)
    ul.error("err", exc_info=True, code=500)
    ul.exception("oops", reason="boom")
    ul.critical("crit", severity="high")

    levels = [entry[0] for entry in backend.calls]
    assert levels == ["debug", "info", "warning", "error", "exception", "critical"]
    assert backend.calls[0][3]["extra"] == {"detector": "det_a"}
    assert backend.calls[3][3]["exc_info"] is True
    assert backend.calls[3][3]["extra"] == {"code": 500}


def test_hardware_and_measurement_context_methods_use_log_context(monkeypatch):
    backend = _FakeBackendLogger()
    monkeypatch.setattr(logger_module, "get_logger", lambda name: backend)
    context_calls = []

    @contextmanager
    def _fake_log_context(**kwargs):
        context_calls.append(kwargs)
        yield

    monkeypatch.setattr(logger_module, "log_context", _fake_log_context)
    ul = logger_module.UlsterLogger("difra.test")

    ul.hardware_state("READY", "hardware up", source="hw")
    ul.measurement("M1", "measurement done", detector="det_a")

    assert context_calls == [
        {"hardware_state": "READY"},
        {"measurement_id": "M1"},
    ]
    assert backend.calls[0][0] == "info"
    assert backend.calls[1][0] == "info"


def test_operation_helpers_emit_start_end_file_detector_stage_logs(monkeypatch):
    backend = _FakeBackendLogger()
    monkeypatch.setattr(logger_module, "get_logger", lambda name: backend)
    ul = logger_module.UlsterLogger("difra.test")

    ul.operation_start("scan", point=1)
    ul.operation_end("scan", success=False, reason="fail")
    ul.timing("scan", 0.123, point=1)
    ul.file_operation("write", "/tmp/file", success=True)
    ul.detector_event("det_a", "captured", frame=1)
    ul.stage_event((1.2, -3.4), "moved", speed="slow")

    messages = [entry[1] for entry in backend.calls]
    assert "Starting scan" in messages[0]
    assert "Operation scan failed" in messages[1]
    assert "Operation scan took 0.123s" in messages[2]
    assert "File write successful: /tmp/file" in messages[3]
    assert "Detector det_a: captured" in messages[4]
    assert "Stage moved at (1.200, -3.400)" in messages[5]


def test_with_logging_decorator_logs_success_and_failure(monkeypatch):
    call_log = []

    class _FakeUlsterLogger:
        def operation_start(self, operation, **kwargs):
            call_log.append(("start", operation, kwargs))

        def operation_end(self, operation, success=True, **kwargs):
            call_log.append(("end", operation, success, kwargs))

    monkeypatch.setattr(logger_module, "get_module_logger", lambda module_name: _FakeUlsterLogger())

    @logger_module.with_logging(operation="work", log_args=True, log_result=True)
    def _ok(a, b):
        return a + b

    @logger_module.with_logging(operation="boom")
    def _fail():
        raise RuntimeError("boom")

    assert _ok(2, 3) == 5
    try:
        _fail()
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected RuntimeError")

    assert call_log[0][0] == "start"
    assert call_log[1][:3] == ("end", "work", True)
    assert call_log[3][:3] == ("end", "boom", False)


def test_log_hardware_state_and_log_measurement_decorators(monkeypatch):
    context_calls = []

    @contextmanager
    def _fake_log_context(**kwargs):
        context_calls.append(kwargs)
        yield

    monkeypatch.setattr(logger_module, "log_context", _fake_log_context)

    @logger_module.log_hardware_state("READY")
    def _hw():
        return "ok"

    class _MeasurementOwner:
        measurement_id = "M-42"

    @logger_module.log_measurement()
    def _measure(owner):
        return owner.measurement_id

    assert _hw() == "ok"
    assert _measure(_MeasurementOwner()) == "M-42"
    assert context_calls == [
        {"hardware_state": "READY"},
        {"measurement_id": "M-42"},
    ]


def test_get_module_logger_returns_ulster_logger_instance(monkeypatch):
    backend = _FakeBackendLogger()
    monkeypatch.setattr(logger_module, "get_logger", lambda name: backend)

    logger = logger_module.get_module_logger("difra.module")

    assert isinstance(logger, logger_module.UlsterLogger)
    assert logger.name == "difra.module"
