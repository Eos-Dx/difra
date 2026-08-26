from __future__ import annotations

from pathlib import Path
import zipfile
from datetime import date

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


def _create_dated_container(path: Path, acquisition_date: str) -> Path:
    _create_container(path)
    with h5py.File(path, "a") as h5f:
        h5f.attrs["acquisition_date"] = acquisition_date
    return path


def _create_reportable_container(path: Path) -> Path:
    poni_text = "\n".join(
        [
            "poni_version: 2.1",
            "Detector: Detector",
            'Detector_config: {"pixel1": 5.5e-05, "pixel2": 5.5e-05, "max_shape": [256, 256]}',
            "Distance: 0.0240169241576942",
            "Poni1: 0.00728105874428302",
            "Poni2: 0.00018897223180544494",
            "Rot1: 0.0",
            "Rot2: 0.0",
            "Rot3: 0.0",
            "Wavelength: 1.5420920203134363e-10",
        ]
    )
    with h5py.File(path, "w") as h5f:
        h5f.attrs["specimenId"] = "SPEC_REPORT"
        h5f.attrs["sample_id"] = "SPEC_REPORT"
        h5f.attrs["session_state"] = "measuring"
        h5f.attrs["matadorProjectId"] = 6701
        poni_group = h5f.require_group("/entry/technical/poni")
        poni_group.create_dataset("poni_primary", data=poni_text)
        poni_group.create_dataset("poni_secondary", data=poni_text)
        meas = h5f.require_group("/entry/measurements/pt_001/meas_0001")
        yy, xx = np.indices((256, 256))
        signal = (xx + yy).astype(float)
        for det_name, alias in (
            ("det_primary", "PRIMARY"),
            ("det_secondary", "SECONDARY"),
        ):
            det = meas.require_group(det_name)
            det.attrs["detector_alias"] = alias
            det.create_dataset("processed_signal", data=signal)
    return path


def test_create_simple_test_image_zip_contains_two_pngs(tmp_path):
    zip_path = reporter.create_simple_test_image_zip(tmp_path)

    with zipfile.ZipFile(zip_path, "r") as archive:
        names = archive.namelist()

    assert "manifest.json" in names
    assert len([name for name in names if name.endswith(".png")]) == 2


def test_build_daily_report_renders_one_combined_image_per_specimen(
    tmp_path, monkeypatch
):
    container = _create_container(tmp_path / "session_test.nxs.h5")

    def _fake_integrate(data, poni_text, *, npt=400, q_range=None):
        q = np.linspace(*(q_range or (0.5, 24.0)), int(npt))
        return q, np.full_like(q, float(np.asarray(data).mean()))

    monkeypatch.setattr(reporter, "integrate_detector_signal", _fake_integrate)
    monkeypatch.setattr(reporter, "_candidate_poni_infos", lambda *_args, **_kwargs: [("poni", "test")])

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
    assert len(result.images) == 1
    assert result.zip_path and result.zip_path.exists()
    with zipfile.ZipFile(result.zip_path, "r") as archive:
        names = archive.namelist()
        manifest = reporter.json.loads(archive.read("manifest.json").decode("utf-8"))
    png_names = sorted(name for name in names if name.endswith(".png"))
    assert png_names == ["SPEC_001_detectors.png", "overview_report.png"]
    assert "report_diagnostics.h5" in names
    assert any(name.startswith("poni/") and name.endswith(".poni") for name in names)
    assert manifest["projectIds"] == ["6701"]
    assert manifest["matadorUploaded"] == 1
    assert manifest["overviewImage"] == "overview_report.png"
    assert manifest["images"][0]["imageFile"] == "SPEC_001_detectors.png"
    assert len(manifest["images"][0]["detectorPanels"]) == 2
    assert len(manifest["poniFiles"]) == 2
    assert len(manifest["series"]) == 2
    for item in manifest["series"]:
        assert item["sourceDataSha256"]
        assert item["sourceDataShape"] == [8, 8]
        assert item["integrationBackend"]
    assert container.name in str(container)


def test_build_selected_report_can_write_folder_without_zip(tmp_path, monkeypatch):
    container = _create_container(tmp_path / "session_test.nxs.h5")

    def _fake_integrate(data, poni_text, *, npt=400, q_range=None):
        q = np.linspace(*(q_range or (0.5, 24.0)), int(npt))
        return q, np.full_like(q, float(np.asarray(data).mean()))

    monkeypatch.setattr(reporter, "integrate_detector_signal", _fake_integrate)
    monkeypatch.setattr(
        reporter,
        "_candidate_poni_infos",
        lambda *_args, **_kwargs: [("poni", "test")],
    )

    output_dir = tmp_path / "report_folder"
    result = reporter.build_daily_report_for_containers(
        config={},
        container_paths=[container],
        output_dir=output_dir,
        send_email=False,
        create_archive=False,
    )

    assert result.zip_path is None
    assert (output_dir / "manifest.json").exists()
    assert (output_dir / "report_diagnostics.h5").exists()
    assert (output_dir / "overview_report.png").exists()
    assert (output_dir / "images" / "SPEC_001_detectors.png").exists()
    manifest = reporter.json.loads((output_dir / "manifest.json").read_text())
    assert manifest["selectedContainers"] == [str(container)]
    assert manifest["imageCount"] == 1
    assert manifest["diagnosticH5"] == "report_diagnostics.h5"
    assert manifest["overviewImage"] == "overview_report.png"
    assert manifest["series"][0]["sourceContainer"] == str(container)
    with h5py.File(output_dir / "report_diagnostics.h5", "r") as h5f:
        assert h5f.attrs["kind"] == "difra_daily_report_diagnostics"
        h5_manifest = reporter.json.loads(h5f["manifest/json"][()].decode("utf-8"))
        assert h5_manifest["diagnosticH5"] == "report_diagnostics.h5"
        first = h5f["series/00000"]
        assert first.attrs["specimen_id"] == "SPEC_001"
        assert first.attrs["source_container"] == str(container)
        assert first["q_nm^-1"].shape == (reporter.DEFAULT_POINTS,)
        assert first["intensity"].shape == (reporter.DEFAULT_POINTS,)
        assert first["raw_data"].shape == (8, 8)
        assert first["raw_data"].compression == "gzip"
        assert first["poni_text"][()].decode("utf-8") == "poni"
        assert first.attrs["poni_source"] == "test"


def test_selected_report_email_uses_compact_analyst_zip_without_full_zip(tmp_path, monkeypatch):
    container = _create_container(tmp_path / "session_test.nxs.h5")
    with h5py.File(container, "a") as h5f:
        h5f.attrs["operator_id"] = "alice"

    def _fake_integrate(data, poni_text, *, npt=400, q_range=None):
        q = np.linspace(*(q_range or (0.5, 24.0)), int(npt))
        return q, np.full_like(q, float(np.asarray(data).mean()))

    calls = []
    monkeypatch.setattr(reporter, "integrate_detector_signal", _fake_integrate)
    monkeypatch.setattr(
        reporter,
        "_candidate_poni_infos",
        lambda *_args, **_kwargs: [("poni", "test")],
    )
    monkeypatch.setattr(
        reporter,
        "send_daily_report_email",
        lambda **kwargs: calls.append(kwargs) or {"sent": True, "skipped": False, "message": "ok"},
    )

    result = reporter.build_daily_report_for_containers(
        config={},
        container_paths=[container],
        output_dir=tmp_path / "report_folder",
        send_email=True,
        create_archive=False,
    )

    assert result.zip_path is None
    assert result.email_result["sent"] is True
    assert calls
    email_zip = Path(calls[0]["zip_path"])
    assert email_zip.name.startswith("difra_selected_analyst_report_")
    assert email_zip.suffix == ".zip"
    with zipfile.ZipFile(email_zip, "r") as archive:
        names = archive.namelist()
    assert "manifest.json" in names
    assert "analyst/analyst_overview_alice.png" in names


def test_build_report_overview_image_for_containers(tmp_path, monkeypatch):
    container = _create_container(tmp_path / "session_test.nxs.h5")

    def _fake_integrate(data, poni_text, *, npt=400, q_range=None):
        q = np.linspace(*(q_range or (0.5, 24.0)), int(npt))
        return q, np.full_like(q, float(np.asarray(data).mean()))

    monkeypatch.setattr(reporter, "integrate_detector_signal", _fake_integrate)
    monkeypatch.setattr(
        reporter,
        "_candidate_poni_infos",
        lambda *_args, **_kwargs: [("poni", "test")],
    )

    image_path = tmp_path / "overview.png"
    result = reporter.build_report_overview_image_for_containers(
        config={},
        container_paths=[container],
        image_path=image_path,
    )

    assert result.valid_containers == 1
    assert result.images == [image_path]
    assert image_path.exists()
    assert result.manifest["overviewImage"] == str(image_path)


def test_collect_report_series_uses_container_distance_for_all_detector_ranges(
    tmp_path, monkeypatch
):
    near_container = _create_container(tmp_path / "session_2cm.nxs.h5")
    far_container = _create_container(tmp_path / "session_17cm.nxs.h5")
    with h5py.File(near_container, "a") as h5f:
        h5f.attrs["specimenId"] = "SPEC_NEAR"
        h5f.attrs["distance_cm"] = 2.0
    with h5py.File(far_container, "a") as h5f:
        h5f.attrs["specimenId"] = "SPEC_FAR"
        h5f.attrs["distance_cm"] = 17.0

    def _fake_integrate(data, poni_text, *, npt=400, q_range=None):
        q = np.linspace(*(q_range or (1.0, 3.0)), int(npt))
        return q, np.ones_like(q)

    monkeypatch.setattr(reporter, "integrate_detector_signal", _fake_integrate)
    monkeypatch.setattr(
        reporter,
        "_candidate_poni_infos",
        lambda *_args, **_kwargs: [("poni", "test")],
    )

    series, skipped, valid_count = reporter.collect_report_series(
        [near_container, far_container],
        points=100,
    )

    assert skipped == []
    assert valid_count == 2
    by_specimen = {}
    for item in series:
        by_specimen.setdefault(item.specimen_id, set()).add(item.range_name)
    assert by_specimen["SPEC_NEAR"] == {"WAXS"}
    assert by_specimen["SPEC_FAR"] == {"SAXS"}


def test_resolve_poni_text_prefers_explicit_detector_poni_path(tmp_path):
    path = tmp_path / "session_poni_priority.nxs.h5"
    legacy_text = "Distance: 0.024\n# legacy"
    detector_text = "Distance: 0.172\n# detector-specific"
    with h5py.File(path, "w") as h5f:
        poni_group = h5f.require_group("/entry/technical/poni")
        poni_group.create_dataset("poni_primary", data=legacy_text)
        poni_group.create_dataset("poni_det_minipix g08-w0299", data=detector_text)
        det = h5f.require_group("/entry/measurements/pt_001/meas_0001/det_primary")
        det.attrs["detector_alias"] = "PRIMARY"
        det.attrs["detector_id"] = "MiniPIX G08-W0299"
        det.attrs["poni_path"] = "/entry/technical/poni/poni_primary"
        det.create_dataset("processed_signal", data=np.ones((8, 8)))

        text = reporter._resolve_poni_text(h5f, det, "PRIMARY")

    assert "legacy" in text
    assert "detector-specific" not in text


def test_collect_report_series_uses_next_poni_when_explicit_integration_is_empty(
    tmp_path, monkeypatch
):
    path = tmp_path / "session_poni_fallback.nxs.h5"
    with h5py.File(path, "w") as h5f:
        h5f.attrs["specimenId"] = "SPEC_FALLBACK"
        h5f.attrs["session_state"] = "measuring"
        poni_group = h5f.require_group("/entry/technical/poni")
        poni_group.create_dataset("poni_primary", data="explicit-poni")
        poni_group.create_dataset("poni_det_minipix g08-w0299", data="detector-poni")
        det = h5f.require_group("/entry/measurements/pt_001/meas_0001/det_primary")
        det.attrs["detector_alias"] = "PRIMARY"
        det.attrs["detector_id"] = "MiniPIX G08-W0299"
        det.attrs["poni_path"] = "/entry/technical/poni/poni_primary"
        det.create_dataset("processed_signal", data=np.ones((8, 8)))

    def _fake_integrate(data, poni_text, *, npt=400, q_range=None):
        q = np.linspace(*(q_range or (1.0, 3.0)), int(npt))
        if "explicit" in poni_text:
            return q, np.zeros(int(npt))
        return q, np.ones(int(npt))

    monkeypatch.setattr(reporter, "integrate_detector_signal", _fake_integrate)

    series, skipped, valid_count = reporter.collect_report_series([path], points=100)

    assert valid_count == 1
    assert skipped == []
    assert len(series) == 1
    assert series[0].poni_source == "/entry/technical/poni/poni_det_minipix g08-w0299"


def test_build_daily_report_does_not_fallback_when_backend_q_range_is_empty(tmp_path, monkeypatch):
    _create_reportable_container(tmp_path / "session_reportable.nxs.h5")

    def _wrong_range_integrate(data, poni_text, *, npt=400, q_range=None):
        q = np.linspace(100.0, 120.0, int(npt))
        return q, np.ones_like(q)

    monkeypatch.setattr(reporter, "integrate_detector_signal", _wrong_range_integrate)

    result = reporter.build_daily_report(
        config={
            "measurements_archive_folder": str(tmp_path),
            "measurements_folder": str(tmp_path / "missing"),
        },
        output_dir=tmp_path / "report",
        since=None,
        send_email=False,
    )

    assert result.valid_containers == 1
    assert result.skipped
    assert len(result.images) == 0
    with zipfile.ZipFile(result.zip_path, "r") as archive:
        assert len([name for name in archive.namelist() if name.endswith(".png")]) == 0


def test_resample_range_requires_full_q_coverage():
    q = np.linspace(2.0, 4.0, 20)
    intensity = np.ones_like(q)

    q_out, i_out = reporter._resample_range(q, intensity, (2.0, 21.0), points=100)

    assert q_out.size == 0
    assert i_out.size == 0


def test_build_daily_report_does_not_send_email_without_images(tmp_path, monkeypatch):
    _create_reportable_container(tmp_path / "session_reportable.nxs.h5")
    monkeypatch.setattr(
        reporter,
        "integrate_detector_signal",
        lambda data, poni_text, *, npt=400, q_range=None: (np.linspace(100.0, 120.0, int(npt)), np.ones(int(npt))),
    )
    monkeypatch.setattr(
        reporter,
        "send_daily_report_email",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not send")),
    )

    result = reporter.build_daily_report(
        config={
            "measurements_archive_folder": str(tmp_path),
            "measurements_folder": str(tmp_path / "missing"),
        },
        output_dir=tmp_path / "report",
        since=None,
        send_email=True,
    )

    assert len(result.images) == 0
    assert result.email_result["sent"] is False
    assert result.email_result["skipped"] is True
    assert "no PNG images" in result.email_result["message"]


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
    monkeypatch.setattr(
        reporter,
        "integrate_detector_signal",
        lambda data, poni_text, *, npt=400, q_range=None: (np.linspace(*(q_range or (0.5, 24.0)), int(npt)), np.ones(int(npt))),
    )
    monkeypatch.setattr(reporter, "_candidate_poni_infos", lambda *_args, **_kwargs: [("poni", "test")])

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
    monkeypatch.setattr(
        reporter,
        "integrate_detector_signal",
        lambda data, poni_text, *, npt=400, q_range=None: (np.linspace(*(q_range or (0.5, 24.0)), int(npt)), np.ones(int(npt))),
    )
    monkeypatch.setattr(reporter, "_candidate_poni_infos", lambda *_args, **_kwargs: [("poni", "test")])

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


def test_run_daily_report_for_date_sends_missed_previous_day(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    _create_dated_container(archive / "session_previous.nxs.h5", "2026-05-20")
    _create_dated_container(archive / "session_today.nxs.h5", "2026-05-21")
    state_path = tmp_path / "report_state.json"
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
            "sent": True,
            "skipped": False,
            "message": "daily report email sent",
        },
    )
    monkeypatch.setattr(reporter, "integrate_detector_signal", lambda data, poni_text, *, npt=400, q_range=None: (np.linspace(*(q_range or (0.5, 24.0)), int(npt)), np.ones(int(npt))))
    monkeypatch.setattr(reporter, "_candidate_poni_infos", lambda *_args, **_kwargs: [("poni", "test")])

    result = reporter.run_daily_report_for_date_from_config(
        config_path=config_path,
        output_dir=tmp_path / "reports",
        report_date=date(2026, 5, 20),
        send_email=True,
    )

    state = reporter.json.loads(state_path.read_text(encoding="utf-8"))
    assert result.scanned == 1
    assert result.valid_containers == 1
    assert result.email_result["sent"] is True
    assert state["byDate"]["2026-05-20"]["sent"] is True
    assert state["byDate"]["2026-05-20"]["imageCount"] == 1


def test_run_daily_report_for_date_skips_when_already_sent(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    container = _create_dated_container(archive / "session_previous.nxs.h5", "2026-05-20")
    fingerprint = reporter.hashlib.sha256(
        f"{container}:{container.stat().st_mtime_ns}:{container.stat().st_size}".encode("utf-8")
    ).hexdigest()
    state_path = tmp_path / "report_state.json"
    state_path.write_text(
        reporter.json.dumps(
            {
                "byDate": {
                    "2026-05-20": {
                        "sent": True,
                        "fingerprint": fingerprint,
                        "imageCount": 2,
                    }
                }
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

    result = reporter.run_daily_report_for_date_from_config(
        config_path=config_path,
        output_dir=tmp_path / "reports",
        report_date=date(2026, 5, 20),
        send_email=True,
    )

    assert result.email_result["skipped"] is True
    assert "already sent" in result.email_result["message"]


def test_run_daily_report_for_date_resends_previous_empty_image_report(monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    container = _create_dated_container(archive / "session_previous.nxs.h5", "2026-05-20")
    fingerprint = reporter.hashlib.sha256(
        f"{container}:{container.stat().st_mtime_ns}:{container.stat().st_size}".encode("utf-8")
    ).hexdigest()
    state_path = tmp_path / "report_state.json"
    state_path.write_text(
        reporter.json.dumps(
            {
                "byDate": {
                    "2026-05-20": {
                        "sent": True,
                        "fingerprint": fingerprint,
                    }
                }
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
            "sent": True,
            "skipped": False,
            "message": "daily report email sent",
        },
    )
    monkeypatch.setattr(
        reporter,
        "integrate_detector_signal",
        lambda data, poni_text, *, npt=400, q_range=None: (np.linspace(*(q_range or (0.5, 24.0)), int(npt)), np.ones(int(npt))),
    )
    monkeypatch.setattr(reporter, "_candidate_poni_infos", lambda *_args, **_kwargs: [("poni", "test")])

    result = reporter.run_daily_report_for_date_from_config(
        config_path=config_path,
        output_dir=tmp_path / "reports",
        report_date=date(2026, 5, 20),
        send_email=True,
    )

    assert result.email_result["sent"] is True
    assert len(result.images) == 1


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
