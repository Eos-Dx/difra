from __future__ import annotations

from pathlib import Path
import zipfile

import h5py
import numpy as np

from difra.gui import daily_valid_container_reporter as reporter


def _create_container(path: Path) -> Path:
    with h5py.File(path, "w") as h5f:
        h5f.attrs["specimenId"] = "SPEC_001"
        h5f.attrs["sample_id"] = "SPEC_001"
        h5f.attrs["session_state"] = "measuring"
        h5f.attrs["matadorProjectId"] = 6701
        h5f.attrs["upload_status"] = "success"
        h5f.attrs["matador_send_status"] = "successful"
        meas = h5f.require_group("/entry/measurements/pt_001/meas_0001")
        for det_name, alias, value in (
            ("det_primary", "PRIMARY", 2.0),
            ("det_secondary", "SECONDARY", 4.0),
        ):
            det = meas.require_group(det_name)
            det.attrs["detector_alias"] = alias
            det.create_dataset(
                "processed_signal",
                data=np.full((8, 8), value, dtype=float),
            )
    return path


def test_create_simple_test_image_zip_contains_two_pngs(tmp_path):
    zip_path = reporter.create_simple_test_image_zip(tmp_path)

    with zipfile.ZipFile(zip_path, "r") as archive:
        names = archive.namelist()

    assert "manifest.json" in names
    assert len([name for name in names if name.endswith(".png")]) == 2


def test_build_daily_report_renders_two_images_for_primary_secondary(
    tmp_path, monkeypatch
):
    container = _create_container(tmp_path / "session_test.nxs.h5")

    def _fake_integrate(data, poni_text, *, npt=400):
        q = np.linspace(0.5, 24.0, 400)
        return q, np.full_like(q, float(np.asarray(data).mean()))

    monkeypatch.setattr(reporter, "integrate_detector_signal", _fake_integrate)
    monkeypatch.setattr(reporter, "_resolve_poni_text", lambda *_args, **_kwargs: "poni")

    result = reporter.build_daily_report(
        config={
            "measurements_archive_folder": str(tmp_path),
            "measurements_folder": str(tmp_path / "missing"),
        },
        output_dir=tmp_path / "report",
        since=None,
        send_email=False,
    )

    assert result.scanned == 1
    assert result.valid_containers == 1
    assert len(result.images) == 2
    assert result.zip_path and result.zip_path.exists()
    with zipfile.ZipFile(result.zip_path, "r") as archive:
        names = archive.namelist()
        manifest = reporter.json.loads(archive.read("manifest.json").decode("utf-8"))
    assert len([name for name in names if name.endswith(".png")]) == 2
    assert manifest["projectIds"] == ["6701"]
    assert manifest["matadorUploaded"] == 1
    assert container.name in str(container)


def test_send_daily_report_email_uses_smtp(monkeypatch, tmp_path):
    zip_path = reporter.create_simple_test_image_zip(tmp_path)
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

    monkeypatch.setattr(reporter.smtplib, "SMTP", FakeSmtp)

    result = reporter.send_daily_report_email(
        config={
            "daily_report_smtp_host": "smtp.example.com",
            "daily_report_smtp_port": 2525,
            "daily_report_smtp_username": "user",
            "daily_report_smtp_password": "secret",
            "daily_report_email_recipient": [
                "sdenisov@matur.co.uk",
                "smurokh@matur.co.uk",
            ],
            "daily_report_email_sender": "difra-upload@company.co.uk",
        },
        zip_path=zip_path,
        manifest={
            "imageCount": 2,
            "validContainers": 3,
            "scanned": 4,
            "matadorUploaded": 2,
            "projectIds": ["6701"],
            "reportDate": "2026-05-16",
        },
        test=True,
    )

    assert result["sent"] is True
    assert ("connect", "smtp.example.com", 2525, 10.0) in calls
    assert ("starttls",) in calls
    assert ("login", "user", "secret") in calls
    assert (
        "send_message",
        "sdenisov@matur.co.uk, smurokh@matur.co.uk",
        "difra-upload@company.co.uk",
    ) in calls


def test_daily_report_email_accepts_comma_separated_recipients(tmp_path):
    zip_path = reporter.create_simple_test_image_zip(tmp_path)

    message = reporter.build_daily_report_email(
        config={
            "daily_report_email_recipient": "sdenisov@matur.co.uk, smurokh@matur.co.uk",
            "daily_report_email_sender": "saldenisov@gmail.com",
        },
        zip_path=zip_path,
        manifest={
            "imageCount": 2,
            "validContainers": 3,
            "scanned": 4,
            "matadorUploaded": 2,
            "projectIds": ["6701"],
            "reportDate": "2026-05-16",
        },
    )

    assert message["To"] == "sdenisov@matur.co.uk, smurokh@matur.co.uk"
    assert message["Subject"] == "DifraReport:2026-05-16"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "Project ID(s): 6701" in body
    assert "Containers: 3 valid / 4 scanned" in body
    assert "Successfully uploaded to Matador: 2" in body


def test_load_report_config_applies_daily_email_overlay(tmp_path):
    base_config = tmp_path / "global.json"
    base_config.write_text(
        reporter.json.dumps(
            {
                "measurements_archive_folder": "/tmp/archive",
                "daily_report_email_recipient": "old@example.com",
            }
        ),
        encoding="utf-8",
    )
    overlay_config = tmp_path / "daily_report_email.json"
    overlay_config.write_text(
        reporter.json.dumps(
            {
                "daily_report_email_recipient": [
                    "sdenisov@matur.co.uk",
                    "smurokh@matur.co.uk",
                ],
                "daily_report_smtp_host": "smtp.gmail.com",
            }
        ),
        encoding="utf-8",
    )

    config = reporter.load_report_config(base_config)

    assert config["measurements_archive_folder"] == "/tmp/archive"
    assert config["daily_report_email_recipient"] == [
        "sdenisov@matur.co.uk",
        "smurokh@matur.co.uk",
    ]
    assert config["daily_report_smtp_host"] == "smtp.gmail.com"


def test_send_daily_report_email_uses_keychain_fallback(monkeypatch, tmp_path):
    zip_path = reporter.create_simple_test_image_zip(tmp_path)
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

    monkeypatch.setattr(reporter.smtplib, "SMTP", FakeSmtp)
    monkeypatch.setattr(
        reporter,
        "_read_stored_smtp_password",
        lambda *, account, service: "keychain-secret",
    )

    result = reporter.send_daily_report_email(
        config={
            "daily_report_smtp_host": "smtp.gmail.com",
            "daily_report_smtp_username": "saldenisov@gmail.com",
            "daily_report_email_sender": "saldenisov@gmail.com",
        },
        zip_path=zip_path,
        manifest={"imageCount": 2, "validContainers": 0, "scanned": 0},
        test=True,
    )

    assert result["sent"] is True
    assert ("login", "saldenisov@gmail.com", "keychain-secret") in calls


def test_stored_password_wrapper_uses_windows_credential_manager(monkeypatch):
    calls = []
    monkeypatch.setattr(reporter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        reporter,
        "_read_windows_credential_password",
        lambda *, account, service: calls.append(("read", account, service)) or "win-secret",
    )
    monkeypatch.setattr(
        reporter,
        "_write_windows_credential_password",
        lambda *, account, service, password: calls.append(
            ("write", account, service, password)
        )
        or True,
    )
    monkeypatch.setattr(
        reporter,
        "_delete_windows_credential_password",
        lambda *, account, service: calls.append(("delete", account, service)) or True,
    )

    assert (
        reporter._read_stored_smtp_password(
            account="saldenisov@gmail.com",
            service="difra_daily_report_smtp_password",
        )
        == "win-secret"
    )
    assert reporter._write_stored_smtp_password(
        account="saldenisov@gmail.com",
        service="difra_daily_report_smtp_password",
        password="secret",
    )
    assert reporter._delete_stored_smtp_password(
        account="saldenisov@gmail.com",
        service="difra_daily_report_smtp_password",
    )
    assert calls == [
        ("read", "saldenisov@gmail.com", "difra_daily_report_smtp_password"),
        ("write", "saldenisov@gmail.com", "difra_daily_report_smtp_password", "secret"),
        ("delete", "saldenisov@gmail.com", "difra_daily_report_smtp_password"),
    ]


def test_interactive_setup_decrypts_bundled_secret_and_stores_keychain(
    monkeypatch, tmp_path
):
    secret_path = tmp_path / "smtp_password.enc.json"
    secret_path.write_text(
        reporter.json.dumps(
            reporter._encrypt_secret_blob("app-password", "Ulster2025!", iterations=100_000)
        ),
        encoding="utf-8",
    )
    stored = {}
    monkeypatch.setattr(reporter.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        reporter.getpass,
        "getpass",
        lambda prompt: "Ulster2025!",
    )
    monkeypatch.setattr(
        reporter,
        "_write_stored_smtp_password",
        lambda *, account, service, password: stored.setdefault(
            "value", (account, service, password)
        )
        or True,
    )

    password = reporter._interactive_keychain_password_setup(
        config={"daily_report_smtp_encrypted_password_path": str(secret_path)},
        account="saldenisov@gmail.com",
        service="difra_daily_report_smtp_password",
    )

    assert password == "app-password"
    assert stored["value"] == (
        "saldenisov@gmail.com",
        "difra_daily_report_smtp_password",
        "app-password",
    )


def test_keychain_setup_self_test_runs_write_read_delete_sequence(
    monkeypatch, tmp_path
):
    secret_path = tmp_path / "smtp_password.enc.json"
    secret_path.write_text(
        reporter.json.dumps(
            reporter._encrypt_secret_blob("app-password", "Ulster2025!", iterations=100_000)
        ),
        encoding="utf-8",
    )
    store = {}
    monkeypatch.setattr(reporter.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(reporter.getpass, "getpass", lambda prompt: "Ulster2025!")
    monkeypatch.setattr(
        reporter,
        "_write_stored_smtp_password",
        lambda *, account, service, password: store.setdefault(
            (account, service), password
        )
        or True,
    )
    monkeypatch.setattr(
        reporter,
        "_read_stored_smtp_password",
        lambda *, account, service: store.get((account, service), ""),
    )
    monkeypatch.setattr(
        reporter,
        "_delete_stored_smtp_password",
        lambda *, account, service: store.pop((account, service), None) is not None,
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        reporter.json.dumps(
            {
                "daily_report_smtp_username": "saldenisov@gmail.com",
                "daily_report_smtp_encrypted_password_path": str(secret_path),
            }
        ),
        encoding="utf-8",
    )

    result = reporter.run_keychain_setup_self_test(config_path=config_path)

    assert result["ok"] is True
    assert result["decrypted"] is True
    assert result["readBack"] is True
    assert result["removed"] is True
    assert store == {}


def test_daily_report_state_advances_only_after_success(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    _create_container(archive / "session_test.nxs.h5")
    config_path = tmp_path / "config.json"
    state_path = tmp_path / "report_state.json"
    config_path.write_text(
        reporter.json.dumps(
            {
                "measurements_archive_folder": str(archive),
                "measurements_folder": str(tmp_path / "missing"),
                "daily_report_state_path": str(state_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        reporter,
        "send_daily_report_email",
        lambda **_kwargs: {
            "sent": True,
            "skipped": False,
            "message": "daily report email sent",
        },
    )

    result = reporter.run_daily_report_from_config(
        config_path=config_path,
        output_dir=tmp_path / "reports",
        since_days=1.0,
        send_email=True,
    )

    state = reporter.json.loads(state_path.read_text(encoding="utf-8"))
    assert result.state_path == state_path
    assert state["trackingStartedAt"] == result.period_start
    assert state["lastSuccessfulUntil"] == result.period_end
    assert state["attempts"][-1]["sent"] is True
    assert state["attempts"][-1]["validContainers"] == 1


def test_daily_report_state_keeps_watermark_after_failed_send(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    _create_container(archive / "session_test.nxs.h5")
    state_path = tmp_path / "report_state.json"
    state_path.write_text(
        reporter.json.dumps(
            {
                "trackingStartedAt": "2026-05-09T08:00:00",
                "lastSuccessfulUntil": "2026-05-10T08:00:00",
                "attempts": [],
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        reporter.json.dumps(
            {
                "measurements_archive_folder": str(archive),
                "measurements_folder": str(tmp_path / "missing"),
                "daily_report_state_path": str(state_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        reporter,
        "send_daily_report_email",
        lambda **_kwargs: {
            "sent": False,
            "skipped": True,
            "message": "daily report SMTP password not configured",
        },
    )

    result = reporter.run_daily_report_from_config(
        config_path=config_path,
        output_dir=tmp_path / "reports",
        since_days=1.0,
        send_email=True,
    )

    state = reporter.json.loads(state_path.read_text(encoding="utf-8"))
    assert result.period_start == "2026-05-10T08:00:00"
    assert state["lastSuccessfulUntil"] == "2026-05-10T08:00:00"
    assert state["attempts"][-1]["sent"] is False
    assert state["attempts"][-1]["message"] == "daily report SMTP password not configured"


def test_gui_email_password_setup_skips_when_password_exists(monkeypatch):
    monkeypatch.setattr(
        reporter,
        "_read_stored_smtp_password",
        lambda *, account, service: "stored-secret",
    )

    result = reporter.ensure_daily_report_email_password_configured_gui(
        config={
            "daily_report_smtp_host": "smtp.gmail.com",
            "daily_report_smtp_username": "saldenisov@gmail.com",
        }
    )

    assert result["ok"] is True
    assert result["required"] is False


def test_gui_email_password_setup_prompts_and_saves(monkeypatch):
    from difra.gui import qt_compat

    prompts = []
    saved = {}
    monkeypatch.setattr(
        reporter,
        "_read_stored_smtp_password",
        lambda *, account, service: "",
    )
    monkeypatch.setattr(
        reporter,
        "_read_encrypted_bundled_password",
        lambda *, config, passphrase: "app-password",
    )
    monkeypatch.setattr(
        reporter,
        "_write_stored_smtp_password",
        lambda *, account, service, password: saved.setdefault(
            "value", (account, service, password)
        )
        or True,
    )
    monkeypatch.setattr(
        qt_compat.QInputDialog,
        "getText",
        lambda parent, title, prompt, mode: prompts.append(prompt)
        or ("Ulster2025!", True),
    )
    monkeypatch.setattr(qt_compat.QMessageBox, "information", lambda *args: None)

    result = reporter.ensure_daily_report_email_password_configured_gui(
        config={
            "daily_report_smtp_host": "smtp.gmail.com",
            "daily_report_smtp_username": "saldenisov@gmail.com",
        }
    )

    assert result["ok"] is True
    assert result["required"] is True
    assert prompts == ["Enter Ulster password to configure daily report email:"]
    assert saved["value"] == (
        "saldenisov@gmail.com",
        "difra_daily_report_smtp_password",
        "app-password",
    )


def test_gui_email_password_setup_rejects_wrong_ulster_password(monkeypatch):
    from difra.gui import qt_compat

    warnings = []
    monkeypatch.setattr(
        reporter,
        "_read_stored_smtp_password",
        lambda *, account, service: "",
    )
    monkeypatch.setattr(
        qt_compat.QInputDialog,
        "getText",
        lambda parent, title, prompt, mode: ("wrong", True),
    )
    monkeypatch.setattr(
        qt_compat.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )

    result = reporter.ensure_daily_report_email_password_configured_gui(
        config={
            "daily_report_smtp_host": "smtp.gmail.com",
            "daily_report_smtp_username": "saldenisov@gmail.com",
        }
    )

    assert result["ok"] is False
    assert result["required"] is True
    assert result["message"] == "incorrect Ulster password"
    assert warnings
