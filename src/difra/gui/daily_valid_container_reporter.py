"""Daily valid session-container plot report email."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import smtplib
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

import h5py
import numpy as np

from difra.gui.daily_report_common import (  # noqa: E402
    DEFAULT_DPI,
    DEFAULT_EMAIL_CONFIG_PATH,
    DEFAULT_EMAIL_SETUP_PASSWORD,
    DEFAULT_ENCRYPTED_PASSWORD_PATH,
    DEFAULT_KEYCHAIN_SERVICE,
    DEFAULT_POINTS,
    DEFAULT_REPORT_RECIPIENT,
    DEFAULT_REPORT_SENDER,
    SAXS_DISTANCE_THRESHOLD_CM,
    SAXS_RANGE,
    WAXS_RANGE,
    _append_report_attempt,
    _as_bool,
    _as_email_recipients,
    _as_int,
    _as_text,
    _candidate_containers,
    _config_value,
    _filter_containers_for_date,
    _load_json_config,
    _load_report_state,
    _parse_report_datetime,
    _report_state_path,
    _safe_token,
    _write_report_state,
)
from difra.gui.daily_report_common import load_report_config  # noqa: E402
from difra.gui.daily_report_credentials import (  # noqa: E402
    _decrypt_secret_blob,
    _delete_macos_keychain_password,
    _delete_windows_credential_password,
    _encrypt_secret_blob,
    _read_macos_keychain_password,
    _read_windows_credential_password,
    _write_macos_keychain_password,
    _write_windows_credential_password,
)
from difra.gui.daily_report_models import (  # noqa: E402
    DailyReportResult,
    DetectorSeries,
)
from difra.gui.daily_report_integration import (  # noqa: E402
    _integrated_range_is_complete,
    _integrated_signal_fraction,
    _resample_range,
    integrate_detector_signal,
)
from difra.gui.daily_report_rendering import (  # noqa: E402
    _detector_sort_key,
    _poni_arcname,
    _report_image_name,
    _write_report_poni_files,
    build_report_manifest_diagnostics,
    create_simple_test_image_zip,
    create_zip,
    render_report_images,
    render_report_overview_image,
)
from difra.gui.daily_report_poni_qc import (  # noqa: E402
    build_poni_qc_manifest,
    collect_poni_qc_panels,
    render_poni_qc_images_by_operator,
)
from difra.gui import daily_report_email as _email_impl  # noqa: E402
from difra.gui import daily_report_builder as _builder_impl  # noqa: E402
from difra.gui import daily_report_series as _series_impl  # noqa: E402

_ORIGINAL_EMAIL_READ_ENCRYPTED_BUNDLED_PASSWORD = (
    _email_impl._read_encrypted_bundled_password
)
_REPORTER_READ_ENCRYPTED_BUNDLED_PASSWORD_WRAPPER = None


def _read_stored_smtp_password(*, account: str, service: str) -> str:
    if platform.system() == "Windows":
        return _read_windows_credential_password(account=account, service=service)
    return _read_macos_keychain_password(account=account, service=service)


def _write_stored_smtp_password(
    *,
    account: str,
    service: str,
    password: str,
) -> bool:
    if platform.system() == "Windows":
        return _write_windows_credential_password(
            account=account,
            service=service,
            password=password,
        )
    return _write_macos_keychain_password(
        account=account,
        service=service,
        password=password,
    )


def _delete_stored_smtp_password(*, account: str, service: str) -> bool:
    if platform.system() == "Windows":
        return _delete_windows_credential_password(account=account, service=service)
    return _delete_macos_keychain_password(account=account, service=service)


def _sync_email_impl_deps() -> None:
    _email_impl.platform = platform
    _email_impl.getpass = getpass
    _email_impl.smtplib = smtplib
    _email_impl.sys = sys
    _email_impl._read_windows_credential_password = _read_windows_credential_password
    _email_impl._write_windows_credential_password = _write_windows_credential_password
    _email_impl._delete_windows_credential_password = _delete_windows_credential_password
    _email_impl._read_macos_keychain_password = _read_macos_keychain_password
    _email_impl._write_macos_keychain_password = _write_macos_keychain_password
    _email_impl._delete_macos_keychain_password = _delete_macos_keychain_password
    _email_impl._read_stored_smtp_password = _read_stored_smtp_password
    _email_impl._write_stored_smtp_password = _write_stored_smtp_password
    _email_impl._delete_stored_smtp_password = _delete_stored_smtp_password
    if (
        _REPORTER_READ_ENCRYPTED_BUNDLED_PASSWORD_WRAPPER is not None
        and _read_encrypted_bundled_password
        is _REPORTER_READ_ENCRYPTED_BUNDLED_PASSWORD_WRAPPER
    ):
        _email_impl._read_encrypted_bundled_password = (
            _ORIGINAL_EMAIL_READ_ENCRYPTED_BUNDLED_PASSWORD
        )
    else:
        _email_impl._read_encrypted_bundled_password = _read_encrypted_bundled_password


def _encrypted_password_path(config: Optional[Dict[str, Any]]) -> Path:
    return _email_impl._encrypted_password_path(config)


def _read_encrypted_bundled_password(
    *,
    config: Optional[Dict[str, Any]],
    passphrase: str,
) -> str:
    return _ORIGINAL_EMAIL_READ_ENCRYPTED_BUNDLED_PASSWORD(
        config=config,
        passphrase=passphrase,
    )


_REPORTER_READ_ENCRYPTED_BUNDLED_PASSWORD_WRAPPER = _read_encrypted_bundled_password


def _interactive_keychain_password_setup(
    *,
    config: Optional[Dict[str, Any]],
    account: str,
    service: str,
) -> str:
    _sync_email_impl_deps()
    return _email_impl._interactive_keychain_password_setup(
        config=config,
        account=account,
        service=service,
    )


def ensure_daily_report_email_password_configured_gui(
    *,
    parent: Any = None,
    config_path: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _sync_email_impl_deps()
    return _email_impl.ensure_daily_report_email_password_configured_gui(
        parent=parent,
        config_path=config_path,
        config=config,
    )


_is_container_valid = _series_impl._is_container_valid
_container_distance_cm = _series_impl._container_distance_cm
_detector_group = _series_impl._detector_group
_detector_side_label = _series_impl._detector_side_label
_detector_range_config = _series_impl._detector_range_config
_sha256_text = _series_impl._sha256_text
_sha256_array = _series_impl._sha256_array
_array_stats = _series_impl._array_stats
_integration_backend_name = _series_impl._integration_backend_name
_candidate_poni_infos = _series_impl._candidate_poni_infos


def _sync_series_impl_deps() -> None:
    _series_impl.integrate_detector_signal = integrate_detector_signal
    _series_impl._candidate_poni_infos = _candidate_poni_infos


def _resolve_poni_info(
    h5f: h5py.File,
    det_group: h5py.Group,
    alias: str,
) -> Tuple[str, str]:
    _sync_series_impl_deps()
    return _series_impl._resolve_poni_info(h5f, det_group, alias)


def _resolve_poni_text(h5f: h5py.File, det_group: h5py.Group, alias: str) -> str:
    _sync_series_impl_deps()
    return _series_impl._resolve_poni_text(h5f, det_group, alias)


def collect_report_series(
    container_paths: Iterable[Path],
    *,
    points: int = DEFAULT_POINTS,
) -> Tuple[List[DetectorSeries], List[str], int]:
    _sync_series_impl_deps()
    return _series_impl.collect_report_series(container_paths, points=points)


def summarize_valid_containers(container_paths: Iterable[Path]) -> Dict[str, Any]:
    return _series_impl.summarize_valid_containers(container_paths)


def _no_report_images_email_result() -> Dict[str, Any]:
    return _email_impl._no_report_images_email_result()


def build_daily_report_email(
    *,
    config: Optional[Dict[str, Any]],
    zip_path: Path,
    manifest: Dict[str, Any],
    attachment_paths: Optional[Iterable[Path]] = None,
    test: bool = False,
) -> Any:
    return _email_impl.build_daily_report_email(
        config=config,
        zip_path=zip_path,
        manifest=manifest,
        attachment_paths=attachment_paths,
        test=test,
    )


def send_daily_report_email(
    *,
    config: Optional[Dict[str, Any]],
    zip_path: Path,
    manifest: Dict[str, Any],
    attachment_paths: Optional[Iterable[Path]] = None,
    test: bool = False,
    allow_interactive_setup: bool = False,
) -> Dict[str, Any]:
    _sync_email_impl_deps()
    return _email_impl.send_daily_report_email(
        config=config,
        zip_path=zip_path,
        manifest=manifest,
        attachment_paths=attachment_paths,
        test=test,
        allow_interactive_setup=allow_interactive_setup,
    )


def _sync_builder_impl_deps() -> None:
    _builder_impl.collect_report_series = collect_report_series
    _builder_impl.summarize_valid_containers = summarize_valid_containers
    _builder_impl.send_daily_report_email = send_daily_report_email
    _builder_impl._no_report_images_email_result = _no_report_images_email_result
    _builder_impl.collect_poni_qc_panels = collect_poni_qc_panels
    _builder_impl.render_poni_qc_images_by_operator = render_poni_qc_images_by_operator
    _builder_impl.build_poni_qc_manifest = build_poni_qc_manifest
    _builder_impl.render_report_overview_image = render_report_overview_image


def build_daily_report(
    *,
    config: Optional[Dict[str, Any]],
    output_dir: Path,
    since: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
    tracking_started_at: Optional[str] = None,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
    create_archive: bool = True,
) -> DailyReportResult:
    _sync_builder_impl_deps()
    return _builder_impl.build_daily_report(
        config=config,
        output_dir=output_dir,
        since=since,
        period_end=period_end,
        tracking_started_at=tracking_started_at,
        send_email=send_email,
        allow_interactive_setup=allow_interactive_setup,
        create_archive=create_archive,
    )


def build_daily_report_for_containers(
    *,
    config: Optional[Dict[str, Any]],
    container_paths: Iterable[Path],
    output_dir: Path,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
    report_date: Optional[date] = None,
    tracking_started_at: Optional[str] = None,
    create_archive: bool = True,
) -> DailyReportResult:
    _sync_builder_impl_deps()
    return _builder_impl.build_daily_report_for_containers(
        config=config,
        container_paths=container_paths,
        output_dir=output_dir,
        send_email=send_email,
        allow_interactive_setup=allow_interactive_setup,
        report_date=report_date,
        tracking_started_at=tracking_started_at,
        create_archive=create_archive,
    )


def build_report_overview_image_for_containers(
    *,
    config: Optional[Dict[str, Any]],
    container_paths: Iterable[Path],
    image_path: Path,
) -> DailyReportResult:
    _sync_builder_impl_deps()
    return _builder_impl.build_report_overview_image_for_containers(
        config=config,
        container_paths=container_paths,
        image_path=image_path,
    )


def run_daily_report_from_config(
    *,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    since_days: float = 1.0,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
) -> DailyReportResult:
    _sync_builder_impl_deps()
    return _builder_impl.run_daily_report_from_config(
        config_path=config_path,
        output_dir=output_dir,
        since_days=since_days,
        send_email=send_email,
        allow_interactive_setup=allow_interactive_setup,
    )


def run_daily_report_for_date_from_config(
    *,
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    report_date: date,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
    skip_if_sent: bool = True,
    resend_if_changed: bool = True,
    skip_if_no_containers: bool = True,
) -> DailyReportResult:
    _sync_builder_impl_deps()
    return _builder_impl.run_daily_report_for_date_from_config(
        config=config,
        config_path=config_path,
        output_dir=output_dir,
        report_date=report_date,
        send_email=send_email,
        allow_interactive_setup=allow_interactive_setup,
        skip_if_sent=skip_if_sent,
        resend_if_changed=resend_if_changed,
        skip_if_no_containers=skip_if_no_containers,
    )


def send_simple_test_email(
    *,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    allow_interactive_setup: bool = False,
) -> Dict[str, Any]:
    _sync_email_impl_deps()
    return _email_impl.send_simple_test_email(
        config_path=config_path,
        output_dir=output_dir,
        allow_interactive_setup=allow_interactive_setup,
    )


def run_keychain_setup_self_test(
    *,
    config_path: Optional[Path] = None,
    service: str = "difra_daily_report_smtp_password_self_test",
) -> Dict[str, Any]:
    _sync_email_impl_deps()
    return _email_impl.run_keychain_setup_self_test(
        config_path=config_path,
        service=service,
    )
