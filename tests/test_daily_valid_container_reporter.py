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
    assert len([name for name in names if name.endswith(".png")]) == 2
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
            "daily_report_email_recipient": "sdenisov@matur.co.uk",
            "daily_report_email_sender": "difra-upload@company.co.uk",
        },
        zip_path=zip_path,
        manifest={"imageCount": 2, "validContainers": 0, "scanned": 0},
        test=True,
    )

    assert result["sent"] is True
    assert ("connect", "smtp.example.com", 2525, 10.0) in calls
    assert ("starttls",) in calls
    assert ("login", "user", "secret") in calls
    assert ("send_message", "sdenisov@matur.co.uk", "difra-upload@company.co.uk") in calls
