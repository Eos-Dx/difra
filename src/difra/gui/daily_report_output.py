"""Daily valid session-container plot report email."""

from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage
import json
from pathlib import Path
import smtplib
import socket
import sys
from typing import Any, Dict, Iterable, List, Optional
import zipfile

import numpy as np

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402


from .daily_report_config import (
    _as_bool,
    _as_email_recipients,
    _as_int,
    _as_text,
    _config_value,
)
from .daily_report_series import (
    DetectorSeries,
    _detector_sort_key,
    _report_image_name,
)

DEFAULT_REPORT_RECIPIENT = "sdenisov@matur.co.uk"
DEFAULT_REPORT_SENDER = "difra-upload@company.co.uk"
DEFAULT_POINTS = 100
DEFAULT_DPI = 200
DEFAULT_KEYCHAIN_SERVICE = "difra_daily_report_smtp_password"
DEFAULT_EMAIL_SETUP_PASSWORD = "Ulster2025!"
DEFAULT_ENCRYPTED_PASSWORD_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "secrets"
    / "daily_report_smtp_password.enc.json"
)
DEFAULT_EMAIL_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "config"
    / "daily_report_email.json"
)
DEFAULT_REPORT_STATE_FILENAME = "daily_report_state.json"
SAXS_RANGE = (1.0, 3.0)
WAXS_RANGE = (2.0, 21.0)
SAXS_DISTANCE_THRESHOLD_CM = 10.0
def render_report_images(
    series: Iterable[DetectorSeries],
    output_dir: Path,
    *,
    dpi: int = DEFAULT_DPI,
) -> List[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    grouped: Dict[str, List[DetectorSeries]] = {}
    for item in series:
        grouped.setdefault(item.specimen_id, []).append(item)

    images: List[Path] = []
    for specimen_id, items in sorted(grouped.items()):
        detector_keys = []
        for item in sorted(items, key=_detector_sort_key):
            if item.detector_key not in detector_keys:
                detector_keys.append(item.detector_key)
        panel_count = max(len(detector_keys), 1)
        ncols = min(panel_count, 3)
        nrows = int(np.ceil(panel_count / ncols))
        fig, axes = plt.subplots(
            nrows,
            ncols,
            figsize=(6.2 * ncols, 4.6 * nrows),
            dpi=dpi,
            squeeze=False,
        )
        for axis in axes.reshape(-1):
            axis.set_visible(False)
        for panel_index, detector_key in enumerate(detector_keys):
            ax = axes.reshape(-1)[panel_index]
            ax.set_visible(True)
            panel_items = [item for item in items if item.detector_key == detector_key]
            panel_items = sorted(panel_items, key=lambda item: item.source_dataset)
            if not panel_items:
                continue
            first = panel_items[0]
            q_range = tuple(first.q_range)
            for index, item in enumerate(panel_items, start=1):
                label = f"{item.detector_alias} #{index}"
                ax.plot(item.q, item.intensity, linewidth=1.1, alpha=0.85, label=label)
            side = f" ({first.detector_side})" if first.detector_side else ""
            ax.set_title(f"{first.detector_alias}{side} | {first.range_label}")
            ax.set_xlabel("q (nm^-1)")
            ax.set_ylabel("I(q)")
            ax.set_xlim(q_range)
            ax.grid(True, alpha=0.25)
            if len(panel_items) <= 12:
                ax.legend(fontsize=7)
        fig.suptitle(str(specimen_id), fontsize=12)
        fig.tight_layout()
        image_path = output / _report_image_name(specimen_id)
        fig.savefig(image_path, dpi=dpi)
        plt.close(fig)
        images.append(image_path)
    return images


def _no_report_images_email_result() -> Dict[str, Any]:
    return {
        "sent": False,
        "skipped": True,
        "message": "daily report has no PNG images; email not sent",
    }


def create_zip(
    zip_path: Path,
    image_paths: Iterable[Path],
    *,
    manifest: Dict[str, Any],
    extra_files: Optional[Dict[str, Path]] = None,
) -> Path:
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        for image_path in image_paths:
            path = Path(image_path)
            archive.write(path, arcname=path.name)
        for arcname, source_path in sorted((extra_files or {}).items()):
            path = Path(source_path)
            if path.exists():
                archive.write(path, arcname=str(arcname))
    return target


def create_simple_test_image_zip(output_dir: Path, *, dpi: int = DEFAULT_DPI) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    image_paths = []
    for name, q_range, fn in (
        ("test_PRIMARY_SAXS_1-3nm-1.png", SAXS_RANGE, lambda q: np.sin(q * 4.0) + 2.0),
        ("test_SECONDARY_WAXS_2-21nm-1.png", WAXS_RANGE, lambda q: np.cos(q * 3.0) + 2.0),
    ):
        q = np.linspace(float(q_range[0]), float(q_range[1]), DEFAULT_POINTS)
        y = fn(q)
        fig, ax = plt.subplots(figsize=(6, 4), dpi=dpi)
        ax.plot(q, y, linewidth=1.5)
        ax.set_xlabel("q (nm^-1)")
        ax.set_ylabel("I(q)")
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        image_path = output / name
        fig.savefig(image_path, dpi=dpi)
        plt.close(fig)
        image_paths.append(image_path)
    return create_zip(
        output / "difra_daily_report_test_images.zip",
        image_paths,
        manifest={"kind": "test", "imageCount": len(image_paths)},
    )


def build_daily_report_email(
    *,
    config: Optional[Dict[str, Any]],
    zip_path: Path,
    manifest: Dict[str, Any],
    test: bool = False,
) -> EmailMessage:
    recipients = _as_email_recipients(
        _config_value(
            config,
            "daily_report_email_recipient",
            "DIFRA_DAILY_REPORT_EMAIL_TO",
            DEFAULT_REPORT_RECIPIENT,
            fallback_key="upload_error_email_recipient",
            fallback_env_key="DIFRA_UPLOAD_ERROR_EMAIL_TO",
        ),
        DEFAULT_REPORT_RECIPIENT,
    )
    sender = _as_text(
        _config_value(
            config,
            "daily_report_email_sender",
            "DIFRA_DAILY_REPORT_EMAIL_FROM",
            DEFAULT_REPORT_SENDER,
            fallback_key="upload_error_email_sender",
            fallback_env_key="DIFRA_UPLOAD_ERROR_EMAIL_FROM",
        ),
        DEFAULT_REPORT_SENDER,
    )
    report_date = _as_text(manifest.get("reportDate"), "")
    if not report_date:
        generated = _as_text(manifest.get("generatedAt"), "")
        report_date = generated[:10] if len(generated) >= 10 else datetime.now().strftime("%Y-%m-%d")
    subject = f"DifraReport:{report_date}"
    if test:
        subject = f"{subject} TEST"

    project_ids = manifest.get("projectIds", [])
    if isinstance(project_ids, (list, tuple, set)):
        project_text = ", ".join(_as_text(item) for item in project_ids if _as_text(item))
    else:
        project_text = _as_text(project_ids, "-")
    if not project_text:
        project_text = "-"

    body = "\n".join(
        [
            "Daily DiFRA valid-container plot report.",
            "",
            f"Host: {socket.gethostname()}",
            f"Generated: {manifest.get('generatedAt', datetime.now().isoformat(timespec='seconds'))}",
            f"Period start: {manifest.get('periodStart') or manifest.get('since') or '-'}",
            f"Period end: {manifest.get('periodEnd') or '-'}",
            f"Tracking started: {manifest.get('trackingStartedAt') or '-'}",
            f"Project ID(s): {project_text}",
            f"Scanned: {manifest.get('scanned', 0)}",
            f"Containers: {manifest.get('validContainers', 0)} valid / {manifest.get('scanned', 0)} scanned",
            f"Successfully uploaded to Matador: {manifest.get('matadorUploaded', 0)}",
            f"Images: {manifest.get('imageCount', 0)}",
            "",
            "Attached ZIP contains 200 dpi PNG files and manifest.json.",
        ]
    )

    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(body)
    payload = Path(zip_path).read_bytes()
    message.add_attachment(
        payload,
        maintype="application",
        subtype="zip",
        filename=Path(zip_path).name,
    )
    return message


def send_daily_report_email(
    *,
    config: Optional[Dict[str, Any]],
    zip_path: Path,
    manifest: Dict[str, Any],
    test: bool = False,
    allow_interactive_setup: bool = False,
) -> Dict[str, Any]:
    enabled = _as_bool(
        _config_value(
            config,
            "daily_report_email_enabled",
            "DIFRA_DAILY_REPORT_EMAIL_ENABLED",
            True,
        ),
        True,
    )
    if not enabled:
        return {"sent": False, "skipped": True, "message": "daily report email disabled"}

    smtp_host = _as_text(
        _config_value(
            config,
            "daily_report_smtp_host",
            "DIFRA_DAILY_REPORT_SMTP_HOST",
            "",
            fallback_key="upload_error_smtp_host",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_HOST",
        )
    )
    if not smtp_host:
        return {"sent": False, "skipped": True, "message": "daily report SMTP host not configured"}

    smtp_port = _as_int(
        _config_value(
            config,
            "daily_report_smtp_port",
            "DIFRA_DAILY_REPORT_SMTP_PORT",
            587,
            fallback_key="upload_error_smtp_port",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_PORT",
        ),
        587,
    )
    username = _as_text(
        _config_value(
            config,
            "daily_report_smtp_username",
            "DIFRA_DAILY_REPORT_SMTP_USERNAME",
            "",
            fallback_key="upload_error_smtp_username",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_USERNAME",
        )
    )
    password = _as_text(
        _config_value(
            config,
            "daily_report_smtp_password",
            "DIFRA_DAILY_REPORT_SMTP_PASSWORD",
            "",
            fallback_key="upload_error_smtp_password",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_PASSWORD",
        )
    )
    if username and not password:
        keychain_service = _as_text(
            _config_value(
                config,
                "daily_report_smtp_keychain_service",
                "DIFRA_DAILY_REPORT_SMTP_KEYCHAIN_SERVICE",
                DEFAULT_KEYCHAIN_SERVICE,
            ),
            DEFAULT_KEYCHAIN_SERVICE,
        )
        password = _daily_report_dependency("_read_stored_smtp_password")(
            account=username,
            service=keychain_service,
        )
        if not password and allow_interactive_setup and sys.stdin.isatty():
            password = _daily_report_dependency("_interactive_keychain_password_setup")(
                config=config,
                account=username,
                service=keychain_service,
            )
    if username and not password:
        return {
            "sent": False,
            "skipped": True,
            "message": "daily report SMTP password not configured",
        }
    use_tls = _as_bool(
        _config_value(
            config,
            "daily_report_smtp_tls",
            "DIFRA_DAILY_REPORT_SMTP_TLS",
            True,
            fallback_key="upload_error_smtp_tls",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_TLS",
        ),
        True,
    )
    timeout_sec = float(
        _config_value(
            config,
            "daily_report_smtp_timeout_sec",
            "DIFRA_DAILY_REPORT_SMTP_TIMEOUT_SEC",
            10.0,
            fallback_key="upload_error_smtp_timeout_sec",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_TIMEOUT_SEC",
        )
    )

    message = build_daily_report_email(
        config=config,
        zip_path=Path(zip_path),
        manifest=manifest,
        test=test,
    )
    with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout_sec) as smtp:
        if use_tls:
            smtp.starttls()
        if username or password:
            smtp.login(username, password)
        smtp.send_message(message)
    return {
        "sent": True,
        "skipped": False,
        "message": f"daily report email sent to {message['To']}",
    }


def _daily_report_dependency(name: str):
    from difra.gui import daily_valid_container_reporter

    return getattr(daily_valid_container_reporter, name)

