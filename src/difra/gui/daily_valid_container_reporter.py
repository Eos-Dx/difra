"""Daily valid session-container plot report email."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
import base64
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import smtplib
import socket
import subprocess
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple
import zipfile

import h5py
import numpy as np

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402


from .daily_report_config import (
    DailyReportResult,
    _append_report_attempt,
    _as_bool,
    _as_email_recipients,
    _as_int,
    _as_text,
    _blob_mac_payload,
    _config_value,
    _decrypt_secret_blob,
    _delete_macos_keychain_password,
    _delete_stored_smtp_password,
    _delete_windows_credential_password,
    _derive_secret_keys,
    _encrypt_secret_blob,
    _encrypted_password_path,
    _interactive_keychain_password_setup,
    _load_json_config,
    _load_report_state,
    _parse_report_datetime,
    _read_encrypted_bundled_password,
    _read_macos_keychain_password,
    _read_stored_smtp_password,
    _read_windows_credential_password,
    _report_state_path,
    _safe_token,
    _write_macos_keychain_password,
    _write_report_state,
    _write_stored_smtp_password,
    _write_windows_credential_password,
    _xor_bytes,
    ensure_daily_report_email_password_configured_gui,
    load_report_config,
)
from .daily_report_output import (
    _no_report_images_email_result,
    build_daily_report_email,
    create_simple_test_image_zip,
    create_zip,
    render_report_images,
    send_daily_report_email,
)
from .daily_report_series import (
    DetectorSeries,
    _candidate_containers,
    _candidate_poni_infos,
    _container_distance_cm,
    _container_report_datetime,
    _detector_group,
    _detector_range_config,
    _detector_side_label,
    _detector_sort_key,
    _filter_containers_for_date,
    _integrated_range_is_complete,
    _integrated_signal_fraction,
    _is_container_valid,
    _poni_arcname,
    _report_image_name,
    _resample_range,
    _resolve_poni_info,
    _resolve_poni_text,
    _sha256_text,
    _write_report_poni_files,
    build_report_manifest_diagnostics,
    collect_report_series,
    integrate_detector_signal,
    summarize_valid_containers,
)
from .daily_report_workflow import (
    build_daily_report,
    build_daily_report_for_containers,
    run_daily_report_for_date_from_config,
    run_daily_report_from_config,
    run_keychain_setup_self_test,
    send_simple_test_email,
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
