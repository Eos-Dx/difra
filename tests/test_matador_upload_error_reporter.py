from pathlib import Path
from types import SimpleNamespace

from difra.gui.matador_upload_error_reporter import (
    build_matador_upload_error_email,
    send_matador_upload_error_report,
)


def _result():
    return SimpleNamespace(
        upload_session_id="624559",
        upload_success=2,
        upload_failed=1,
        moved=0,
        failed=["session_a.nxs.h5: Matador HTTP 500"],
        old_format_failed=[],
    )


def test_build_matador_upload_error_email_uses_configured_addresses(tmp_path):
    message = build_matador_upload_error_email(
        config={
            "upload_error_email_recipient": "dev@example.com",
            "upload_error_email_sender": "difra@example.com",
        },
        workflow_result=_result(),
        log_path=tmp_path / "matador_send.log",
        context="archived-resend",
    )

    assert message["To"] == "dev@example.com"
    assert message["From"] == "difra@example.com"
    assert "1 failed" in message["Subject"]
    assert "Matador HTTP 500" in message.get_body(preferencelist=("plain",)).get_content()


def test_send_matador_upload_error_report_skips_without_smtp_host(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "difra.gui.matador_upload_error_reporter._load_email_fallback_config",
        lambda: {},
    )

    result = send_matador_upload_error_report(
        config={"upload_error_smtp_host": ""},
        workflow_result=_result(),
        log_path=tmp_path / "matador_send.log",
        context="send-and-archive",
    )

    assert result["sent"] is False
    assert result["skipped"] is True


def test_send_matador_upload_error_report_uses_smtp(monkeypatch, tmp_path):
    calls = []

    class FakeSmtp:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls.append(("close",))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message):
            calls.append(("send_message", message["To"], message["From"]))

    monkeypatch.setattr("difra.gui.matador_upload_error_reporter.smtplib.SMTP", FakeSmtp)

    result = send_matador_upload_error_report(
        config={
            "upload_error_smtp_host": "smtp.example.com",
            "upload_error_smtp_port": 2525,
            "upload_error_smtp_username": "user",
            "upload_error_smtp_password": "secret",
            "upload_error_email_recipient": "dev@example.com",
            "upload_error_email_sender": "difra@example.com",
        },
        workflow_result=_result(),
        log_path=Path(tmp_path / "matador_send.log"),
        context="send-and-archive",
    )

    assert result["sent"] is True
    assert ("connect", "smtp.example.com", 2525, 10.0) in calls
    assert ("starttls",) in calls
    assert ("login", "user", "secret") in calls
    assert ("send_message", "dev@example.com", "difra@example.com") in calls


def test_send_matador_upload_error_report_falls_back_to_daily_email_smtp(
    monkeypatch,
    tmp_path,
):
    calls = []

    class FakeSmtp:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls.append(("close",))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message):
            calls.append(("send_message", message["To"], message["From"]))

    monkeypatch.setattr("difra.gui.matador_upload_error_reporter.smtplib.SMTP", FakeSmtp)
    monkeypatch.setattr(
        "difra.gui.matador_upload_error_reporter._load_email_fallback_config",
        lambda: {
            "daily_report_smtp_host": "smtp.gmail.com",
            "daily_report_smtp_port": 587,
            "daily_report_smtp_tls": True,
            "daily_report_smtp_username": "saldenisov@gmail.com",
            "daily_report_smtp_keychain_service": "difra_daily_report_smtp_password",
        },
    )
    monkeypatch.setattr(
        "difra.gui.matador_upload_error_reporter._read_stored_upload_error_smtp_password",
        lambda *, account, service: "stored-app-password",
    )

    result = send_matador_upload_error_report(
        config={
            "upload_error_smtp_host": "",
            "upload_error_email_recipient": "dev@example.com",
            "upload_error_email_sender": "difra@example.com",
        },
        workflow_result=_result(),
        log_path=Path(tmp_path / "matador_send.log"),
        context="send-and-archive",
    )

    assert result["sent"] is True
    assert ("connect", "smtp.gmail.com", 587, 10.0) in calls
    assert ("starttls",) in calls
    assert ("login", "saldenisov@gmail.com", "stored-app-password") in calls
    assert ("send_message", "dev@example.com", "difra@example.com") in calls
