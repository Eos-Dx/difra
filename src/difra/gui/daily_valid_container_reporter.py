"""Daily valid session-container plot report email."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import smtplib
import socket
import sys
import tempfile
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
)


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


def _encrypted_password_path(config: Optional[Dict[str, Any]]) -> Path:
    configured = _as_text(
        _config_value(
            config,
            "daily_report_smtp_encrypted_password_path",
            "DIFRA_DAILY_REPORT_SMTP_ENCRYPTED_PASSWORD_PATH",
            "",
        )
    )
    if configured:
        return Path(configured)
    return DEFAULT_ENCRYPTED_PASSWORD_PATH


def _read_encrypted_bundled_password(
    *,
    config: Optional[Dict[str, Any]],
    passphrase: str,
) -> str:
    path = _encrypted_password_path(config)
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(blob, dict):
        return ""
    return _decrypt_secret_blob(blob, passphrase)


def _interactive_keychain_password_setup(
    *,
    config: Optional[Dict[str, Any]],
    account: str,
    service: str,
) -> str:
    setup_password = _as_text(
        _config_value(
            config,
            "daily_report_email_setup_password",
            "DIFRA_DAILY_REPORT_EMAIL_SETUP_PASSWORD",
            DEFAULT_EMAIL_SETUP_PASSWORD,
        ),
        DEFAULT_EMAIL_SETUP_PASSWORD,
    )
    entered_setup_password = getpass.getpass("Enter Ulster password to configure email: ")
    if entered_setup_password != setup_password:
        return ""
    smtp_password = _read_encrypted_bundled_password(
        config=config,
        passphrase=entered_setup_password,
    )
    if not smtp_password:
        smtp_password = getpass.getpass(f"Enter Gmail App Password for {account}: ")
    smtp_password = str(smtp_password or "").replace(" ", "").strip()
    if not smtp_password:
        return ""
    if not _write_stored_smtp_password(
        account=account,
        service=service,
        password=smtp_password,
    ):
        return ""
    return smtp_password


def ensure_daily_report_email_password_configured_gui(
    *,
    parent: Any = None,
    config_path: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = load_report_config(config_path)
    if config:
        cfg.update(config)
    if not _as_bool(
        _config_value(
            cfg,
            "daily_report_email_enabled",
            "DIFRA_DAILY_REPORT_EMAIL_ENABLED",
            True,
        ),
        True,
    ):
        return {"ok": True, "required": False, "message": "daily report email disabled"}

    smtp_host = _as_text(
        _config_value(
            cfg,
            "daily_report_smtp_host",
            "DIFRA_DAILY_REPORT_SMTP_HOST",
            "",
            fallback_key="upload_error_smtp_host",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_HOST",
        )
    )
    username = _as_text(
        _config_value(
            cfg,
            "daily_report_smtp_username",
            "DIFRA_DAILY_REPORT_SMTP_USERNAME",
            "",
            fallback_key="upload_error_smtp_username",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_USERNAME",
        )
    )
    if not smtp_host or not username:
        return {
            "ok": True,
            "required": False,
            "message": "daily report SMTP host or username not configured",
        }

    configured_password = _as_text(
        _config_value(
            cfg,
            "daily_report_smtp_password",
            "DIFRA_DAILY_REPORT_SMTP_PASSWORD",
            "",
            fallback_key="upload_error_smtp_password",
            fallback_env_key="DIFRA_UPLOAD_ERROR_SMTP_PASSWORD",
        )
    )
    keychain_service = _as_text(
        _config_value(
            cfg,
            "daily_report_smtp_keychain_service",
            "DIFRA_DAILY_REPORT_SMTP_KEYCHAIN_SERVICE",
            DEFAULT_KEYCHAIN_SERVICE,
        ),
        DEFAULT_KEYCHAIN_SERVICE,
    )
    if configured_password or _read_stored_smtp_password(
        account=username,
        service=keychain_service,
    ):
        return {"ok": True, "required": False, "message": "daily report SMTP password configured"}

    try:
        from difra.gui.qt_compat import QInputDialog, QLineEdit, QMessageBox
    except Exception as exc:
        return {
            "ok": False,
            "required": True,
            "message": f"Qt password dialog unavailable: {type(exc).__name__}: {exc}",
        }

    setup_password = _as_text(
        _config_value(
            cfg,
            "daily_report_email_setup_password",
            "DIFRA_DAILY_REPORT_EMAIL_SETUP_PASSWORD",
            DEFAULT_EMAIL_SETUP_PASSWORD,
        ),
        DEFAULT_EMAIL_SETUP_PASSWORD,
    )
    entered_setup_password, ok = QInputDialog.getText(
        parent,
        "Daily Report Email",
        "Enter Ulster password to configure daily report email:",
        QLineEdit.Password,
    )
    if not ok:
        return {"ok": False, "required": True, "message": "daily report email setup cancelled"}
    if str(entered_setup_password or "") != setup_password:
        QMessageBox.warning(parent, "Daily Report Email", "Incorrect Ulster password.")
        return {"ok": False, "required": True, "message": "incorrect Ulster password"}

    smtp_password = _read_encrypted_bundled_password(
        config=cfg,
        passphrase=str(entered_setup_password or ""),
    )
    if not smtp_password:
        smtp_password, ok = QInputDialog.getText(
            parent,
            "Daily Report Email",
            f"Enter Gmail App Password for {username}:",
            QLineEdit.Password,
        )
        if not ok:
            return {
                "ok": False,
                "required": True,
                "message": "Gmail App Password entry cancelled",
            }
    smtp_password = str(smtp_password or "").replace(" ", "").strip()
    if not smtp_password:
        QMessageBox.warning(parent, "Daily Report Email", "SMTP password is empty.")
        return {"ok": False, "required": True, "message": "SMTP password is empty"}

    if not _write_stored_smtp_password(
        account=username,
        service=keychain_service,
        password=smtp_password,
    ):
        QMessageBox.critical(
            parent,
            "Daily Report Email",
            "Failed to save SMTP password in local credential storage.",
        )
        return {"ok": False, "required": True, "message": "failed to save SMTP password"}

    QMessageBox.information(
        parent,
        "Daily Report Email",
        "SMTP password saved. Daily reports can be sent automatically.",
    )
    return {
        "ok": True,
        "required": True,
        "message": "daily report SMTP password saved",
    }


def _is_container_valid(h5f: h5py.File) -> Tuple[bool, str]:
    specimen = _as_text(h5f.attrs.get("specimenId", h5f.attrs.get("sample_id", "")))
    if not specimen:
        return False, "missing specimenId"

    transfer_status = _as_text(h5f.attrs.get("transfer_status", "")).lower()
    if transfer_status in {"not_complete", "failed", "error"}:
        return False, f"transfer_status={transfer_status}"

    container_state = _as_text(h5f.attrs.get("container_state", "")).lower()
    session_state = _as_text(h5f.attrs.get("session_state", "")).lower()
    bad_states = {"error", "failed", "rejected", "rejected_blocked"}
    if container_state in bad_states:
        return False, f"container_state={container_state}"
    if session_state in {"draft", "recovery_required"}:
        return False, f"session_state={session_state}"

    measurements_group = h5f.get("/entry/measurements")
    if measurements_group is None:
        return False, "missing /entry/measurements"

    processed_count = 0
    for point_group in measurements_group.values():
        if not isinstance(point_group, h5py.Group):
            continue
        for measurement_group in point_group.values():
            if not isinstance(measurement_group, h5py.Group):
                continue
            status = _as_text(measurement_group.attrs.get("measurement_status", "completed")).lower()
            if status in {"failed", "aborted"}:
                continue
            for det_group in measurement_group.values():
                if isinstance(det_group, h5py.Group) and "processed_signal" in det_group:
                    processed_count += 1
    if processed_count <= 0:
        return False, "no processed_signal measurements"
    return True, ""


def _container_distance_cm(h5f: h5py.File) -> Optional[float]:
    for attr_name in ("distance_cm", "distanceCm", "technical_distance_cm"):
        try:
            value = h5f.attrs.get(attr_name)
            if value not in (None, ""):
                return float(value)
        except Exception:
            continue
    return None


def _detector_group(alias: str, detector_name: str) -> str:
    token = f"{alias} {detector_name}".upper()
    if any(item in token for item in ("PRIMARY", "SAXS", "DET_PRIMARY", "DET_SAXS")):
        return "PRIMARY"
    if any(item in token for item in ("SECONDARY", "WAXS", "DET_SECONDARY", "DET_WAXS")):
        return "SECONDARY"
    return _safe_token(str(alias or detector_name or "DETECTOR").upper(), "DETECTOR")


def _detector_side_label(detector_group: str, alias: str, detector_name: str) -> str:
    token = f"{detector_group} {alias} {detector_name}".upper()
    if any(item in token for item in ("PRIMARY", "LEFT", "SAXS", "DET_PRIMARY", "DET_SAXS")):
        return "LEFT"
    if any(item in token for item in ("SECONDARY", "RIGHT", "WAXS", "DET_SECONDARY", "DET_WAXS")):
        return "RIGHT"
    return ""


def _detector_range_config(
    detector_group: str,
    alias: str,
    detector_name: str,
    distance_cm: Optional[float] = None,
) -> Tuple[str, Tuple[float, float], str, str]:
    if distance_cm is not None:
        try:
            distance = float(distance_cm)
        except Exception:
            distance = None
        if distance is not None and np.isfinite(distance):
            if distance >= SAXS_DISTANCE_THRESHOLD_CM:
                return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", f"distance_cm={distance:g}"
            return "WAXS", WAXS_RANGE, "WAXS 2-21 nm^-1", f"distance_cm={distance:g}"

    token = f"{detector_group} {alias} {detector_name}".upper()
    if any(item in token for item in ("SECONDARY", "WAXS", "RIGHT", "DET_SECONDARY", "DET_WAXS")):
        return "WAXS", WAXS_RANGE, "WAXS 2-21 nm^-1", "alias/name matched WAXS/SECONDARY"
    if any(item in token for item in ("PRIMARY", "SAXS", "LEFT", "DET_PRIMARY", "DET_SAXS")):
        return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", "alias/name matched SAXS/PRIMARY"
    return "SAXS", SAXS_RANGE, "SAXS 1-3 nm^-1", "default: alias/name did not identify SAXS or WAXS"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _sha256_array(value: np.ndarray) -> str:
    arr = np.ascontiguousarray(np.asarray(value))
    digest = hashlib.sha256()
    digest.update(str(arr.dtype).encode("utf-8"))
    digest.update(str(tuple(arr.shape)).encode("utf-8"))
    digest.update(arr.tobytes())
    return digest.hexdigest()


def _array_stats(value: np.ndarray) -> Tuple[float, float, float]:
    arr = np.asarray(value, dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return float("nan"), float("nan"), float("nan")
    return (
        float(np.nanmin(finite)),
        float(np.nanmedian(finite)),
        float(np.nanmax(finite)),
    )


def _integration_backend_name() -> str:
    try:
        from difra.gui.technical.analysis_compat import backend_name

        return str(backend_name())
    except Exception:
        return "unknown"


def _candidate_poni_infos(
    h5f: h5py.File,
    det_group: h5py.Group,
    alias: str,
) -> List[Tuple[str, str]]:
    candidates = []
    role = str(det_group.name.rsplit("/", 1)[-1])
    detector_id = _as_text(det_group.attrs.get("detector_id")).strip().lower()
    detector_alias = _as_text(det_group.attrs.get("detector_alias")).strip().lower()
    for attr_name in ("poni_ref", "poni_path"):
        ref = _as_text(det_group.attrs.get(attr_name)).strip()
        if not ref:
            continue
        candidates.append(ref)
        if not ref.startswith("/"):
            candidates.append(f"/entry/technical/poni/{ref}")
            candidates.append(f"/entry/technical/poni/poni_{ref}")
    if detector_id:
        candidates.extend(
            [
                f"/entry/technical/poni/poni_det_{detector_id}",
                f"/entry/technical/poni/poni_{detector_id}",
            ]
        )
    if role.startswith("det_"):
        candidates.extend(
            [
                f"/entry/technical/poni/poni_{role}",
            ]
        )
    if role.startswith("det_"):
        candidates.append(f"/entry/technical/poni/poni_{role[4:]}")
    tokens = {
        alias.strip().lower(),
        role.lower(),
        role.replace("det_", "").lower(),
        detector_alias,
        detector_id,
    }
    poni_group = h5f.get("/entry/technical/poni")
    if poni_group is not None:
        for name in sorted(poni_group.keys()):
            lower_name = name.lower()
            if any(token and token in lower_name for token in tokens):
                candidates.append(f"/entry/technical/poni/{name}")
    seen = set()
    found: List[Tuple[str, str]] = []
    for candidate in candidates:
        if not candidate or candidate in seen or candidate not in h5f:
            continue
        seen.add(candidate)
        text = _as_text(h5f[candidate][()]).strip()
        if text:
            found.append((text, candidate))
    return found


def _resolve_poni_info(h5f: h5py.File, det_group: h5py.Group, alias: str) -> Tuple[str, str]:
    candidates = _candidate_poni_infos(h5f, det_group, alias)
    if candidates:
        return candidates[0]
    return "", ""


def _resolve_poni_text(h5f: h5py.File, det_group: h5py.Group, alias: str) -> str:
    text, _source = _resolve_poni_info(h5f, det_group, alias)
    return text


def collect_report_series(
    container_paths: Iterable[Path],
    *,
    points: int = DEFAULT_POINTS,
) -> Tuple[List[DetectorSeries], List[str], int]:
    series: List[DetectorSeries] = []
    skipped: List[str] = []
    valid_count = 0
    for container_path in container_paths:
        path = Path(container_path)
        try:
            with h5py.File(path, "r") as h5f:
                is_valid, reason = _is_container_valid(h5f)
                if not is_valid:
                    skipped.append(f"{path.name}: {reason}")
                    continue
                valid_count += 1
                specimen_id = _as_text(
                    h5f.attrs.get("specimenId", h5f.attrs.get("sample_id", "unknown")),
                    "unknown",
                )
                distance_cm = _container_distance_cm(h5f)
                measurements_group = h5f.get("/entry/measurements")
                for point_name in sorted(measurements_group.keys()):
                    point_group = measurements_group[point_name]
                    if not isinstance(point_group, h5py.Group):
                        continue
                    for measurement_name in sorted(point_group.keys()):
                        measurement_group = point_group[measurement_name]
                        if not isinstance(measurement_group, h5py.Group):
                            continue
                        status = _as_text(
                            measurement_group.attrs.get("measurement_status", "completed")
                        ).lower()
                        if status in {"failed", "aborted"}:
                            continue
                        for det_name in sorted(measurement_group.keys()):
                            det_group = measurement_group[det_name]
                            if not isinstance(det_group, h5py.Group):
                                continue
                            if "processed_signal" not in det_group:
                                continue
                            alias = _as_text(
                                det_group.attrs.get(
                                    "detector_alias",
                                    str(det_name).replace("det_", "").upper(),
                                )
                            ).upper()
                            group_name = _detector_group(alias, str(det_name))
                            side = _detector_side_label(group_name, alias, str(det_name))
                            range_name, q_range, range_label, range_reason = (
                                _detector_range_config(
                                    group_name,
                                    alias,
                                    str(det_name),
                                    distance_cm=distance_cm,
                                )
                            )
                            signal_ds = det_group["processed_signal"]
                            signal = signal_ds[()]
                            signal_sha256 = _sha256_array(signal)
                            signal_min, signal_median, signal_max = _array_stats(signal)
                            integration_backend = _integration_backend_name()
                            best: Optional[Tuple[str, str, np.ndarray, np.ndarray, float]] = None
                            for candidate_poni_text, candidate_poni_source in (
                                _candidate_poni_infos(h5f, det_group, alias) or [("", "")]
                            ):
                                q, intensity = integrate_detector_signal(
                                    signal,
                                    candidate_poni_text,
                                    npt=points,
                                    q_range=q_range,
                                )
                                if not _integrated_range_is_complete(
                                    q,
                                    intensity,
                                    q_range,
                                    points=points,
                                ):
                                    continue
                                signal_fraction = _integrated_signal_fraction(intensity)
                                candidate = (
                                    candidate_poni_text,
                                    candidate_poni_source,
                                    q,
                                    intensity,
                                    signal_fraction,
                                )
                                if best is None or signal_fraction > best[4]:
                                    best = candidate
                                if signal_fraction >= 0.5:
                                    break
                            if best is None:
                                skipped.append(
                                    f"{path.name}:{det_group.name}: no q data in {q_range[0]}-{q_range[1]} nm^-1"
                                )
                                continue
                            poni_text, poni_source, q, intensity, _signal_fraction = best
                            series.append(
                                DetectorSeries(
                                    specimen_id=specimen_id,
                                    detector_group=group_name,
                                    detector_alias=alias,
                                    detector_name=str(det_name),
                                    detector_side=side,
                                    range_name=range_name,
                                    q_range=q_range,
                                    range_label=range_label,
                                    range_assignment=range_reason,
                                    q=q,
                                    intensity=intensity,
                                    poni_text=poni_text,
                                    poni_source=poni_source,
                                    poni_sha256=_sha256_text(poni_text) if poni_text else "",
                                    source_data_sha256=signal_sha256,
                                    source_data_shape=tuple(int(item) for item in np.asarray(signal).shape),
                                    source_data_min=signal_min,
                                    source_data_median=signal_median,
                                    source_data_max=signal_max,
                                    integration_backend=integration_backend,
                                    source_container=path,
                                    source_dataset=signal_ds.name,
                                )
                            )
        except Exception as exc:
            skipped.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return series, skipped, valid_count


def summarize_valid_containers(container_paths: Iterable[Path]) -> Dict[str, Any]:
    project_ids: List[str] = []
    valid_containers = 0
    matador_uploaded = 0
    for container_path in container_paths:
        try:
            with h5py.File(container_path, "r") as h5f:
                is_valid, _reason = _is_container_valid(h5f)
                if not is_valid:
                    continue
                valid_containers += 1
                project_id = _as_text(
                    h5f.attrs.get(
                        "matadorProjectId",
                        h5f.attrs.get("project_id", h5f.attrs.get("matadorProjectName", "")),
                    )
                )
                if project_id and project_id not in project_ids:
                    project_ids.append(project_id)
                upload_status = _as_text(h5f.attrs.get("upload_status", "")).lower()
                matador_send_status = _as_text(
                    h5f.attrs.get("matador_send_status", "")
                ).lower()
                transfer_status = _as_text(h5f.attrs.get("transfer_status", "")).lower()
                if (
                    upload_status == "success"
                    or matador_send_status == "successful"
                    or transfer_status == "sent"
                ):
                    matador_uploaded += 1
        except Exception:
            continue
    return {
        "projectIds": project_ids,
        "validContainers": valid_containers,
        "matadorUploaded": matador_uploaded,
    }


def _no_report_images_email_result() -> Dict[str, Any]:
    return {
        "sent": False,
        "skipped": True,
        "message": "daily report has no PNG images; email not sent",
    }


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
        password = _read_stored_smtp_password(
            account=username,
            service=keychain_service,
        )
        if not password and allow_interactive_setup and sys.stdin.isatty():
            password = _interactive_keychain_password_setup(
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


def build_daily_report(
    *,
    config: Optional[Dict[str, Any]],
    output_dir: Path,
    since: Optional[datetime] = None,
    period_end: Optional[datetime] = None,
    tracking_started_at: Optional[str] = None,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
) -> DailyReportResult:
    cfg = dict(config or {})
    generated_at = datetime.now()
    period_end = period_end or generated_at
    roots = [
        Path(cfg.get("measurements_archive_folder") or ""),
        Path(cfg.get("measurements_folder") or ""),
    ]
    containers = _candidate_containers([root for root in roots if str(root)], since=since)
    result = DailyReportResult(scanned=len(containers))
    result.period_start = since.isoformat(timespec="seconds") if since else None
    result.period_end = period_end.isoformat(timespec="seconds")
    result.tracking_started_at = tracking_started_at
    series, skipped, valid_count = collect_report_series(containers, points=DEFAULT_POINTS)
    summary = summarize_valid_containers(containers)
    result.skipped.extend(skipped)
    result.valid_containers = valid_count
    out = Path(output_dir)
    image_dir = out / "images"
    result.images = render_report_images(series, image_dir, dpi=DEFAULT_DPI)
    poni_files = _write_report_poni_files(series, out)
    manifest = {
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "reportDate": generated_at.strftime("%Y-%m-%d"),
        "since": since.isoformat(timespec="seconds") if since else None,
        "periodStart": since.isoformat(timespec="seconds") if since else None,
        "periodEnd": period_end.isoformat(timespec="seconds"),
        "trackingStartedAt": tracking_started_at,
        "scanned": result.scanned,
        "validContainers": result.valid_containers,
        "projectIds": summary.get("projectIds", []),
        "matadorUploaded": int(summary.get("matadorUploaded", 0) or 0),
        "imageCount": len(result.images),
        "skipped": result.skipped[:200],
    }
    manifest.update(build_report_manifest_diagnostics(series, poni_files=poni_files))
    result.manifest = manifest
    result.zip_path = create_zip(
        out / f"difra_daily_valid_container_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
        result.images,
        manifest=manifest,
        extra_files=poni_files,
    )
    if send_email:
        if not result.images:
            result.email_result = _no_report_images_email_result()
        else:
            try:
                result.email_result = send_daily_report_email(
                    config=cfg,
                    zip_path=result.zip_path,
                    manifest=manifest,
                    allow_interactive_setup=allow_interactive_setup,
                )
            except Exception as exc:
                result.email_result = {
                    "sent": False,
                    "skipped": False,
                    "message": f"{type(exc).__name__}: {exc}",
                }
    return result


def build_daily_report_for_containers(
    *,
    config: Optional[Dict[str, Any]],
    container_paths: Iterable[Path],
    output_dir: Path,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
    report_date: Optional[date] = None,
    tracking_started_at: Optional[str] = None,
) -> DailyReportResult:
    cfg = dict(config or {})
    generated_at = datetime.now()
    paths = sorted({Path(path) for path in container_paths if Path(path).exists()})
    result = DailyReportResult(scanned=len(paths))
    result.period_start = None
    result.period_end = generated_at.isoformat(timespec="seconds")
    result.tracking_started_at = tracking_started_at
    series, skipped, valid_count = collect_report_series(paths, points=DEFAULT_POINTS)
    summary = summarize_valid_containers(paths)
    result.skipped.extend(skipped)
    result.valid_containers = valid_count
    out = Path(output_dir)
    image_dir = out / "images"
    result.images = render_report_images(series, image_dir, dpi=DEFAULT_DPI)
    poni_files = _write_report_poni_files(series, out)
    report_day = report_date or generated_at.date()
    manifest = {
        "generatedAt": generated_at.isoformat(timespec="seconds"),
        "reportDate": report_day.isoformat(),
        "since": None,
        "periodStart": None,
        "periodEnd": generated_at.isoformat(timespec="seconds"),
        "trackingStartedAt": tracking_started_at,
        "scanned": result.scanned,
        "validContainers": result.valid_containers,
        "projectIds": summary.get("projectIds", []),
        "matadorUploaded": int(summary.get("matadorUploaded", 0) or 0),
        "imageCount": len(result.images),
        "selectedContainers": [str(path) for path in paths],
        "skipped": result.skipped[:200],
    }
    manifest.update(build_report_manifest_diagnostics(series, poni_files=poni_files))
    result.manifest = manifest
    result.zip_path = create_zip(
        out / f"difra_selected_valid_container_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
        result.images,
        manifest=manifest,
        extra_files=poni_files,
    )
    if send_email:
        if not result.images:
            result.email_result = _no_report_images_email_result()
        else:
            try:
                result.email_result = send_daily_report_email(
                    config=cfg,
                    zip_path=result.zip_path,
                    manifest=manifest,
                    allow_interactive_setup=allow_interactive_setup,
                )
            except Exception as exc:
                result.email_result = {
                    "sent": False,
                    "skipped": False,
                    "message": f"{type(exc).__name__}: {exc}",
                }
    return result


def run_daily_report_from_config(
    *,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    since_days: float = 1.0,
    send_email: bool = False,
    allow_interactive_setup: bool = False,
) -> DailyReportResult:
    config = load_report_config(config_path)
    base = output_dir
    if base is None:
        base_folder = config.get("difra_base_folder") or Path.home() / "difra"
        base = Path(base_folder) / "daily_reports"
    base = Path(base)
    period_end = datetime.now()
    fallback_since = period_end - timedelta(days=float(since_days))
    since = fallback_since
    state_path = _report_state_path(config, base)
    state: Dict[str, Any] = {}
    tracking_started_at: Optional[str] = None
    if send_email:
        state = _load_report_state(state_path)
        tracking_started = _parse_report_datetime(state.get("trackingStartedAt"))
        last_successful = _parse_report_datetime(state.get("lastSuccessfulUntil"))
        if last_successful is not None:
            since = last_successful
        elif tracking_started is not None:
            since = tracking_started
        else:
            tracking_started = fallback_since
            state["trackingStartedAt"] = tracking_started.isoformat(timespec="seconds")
        tracking_started_at = state.get("trackingStartedAt")

    result = build_daily_report(
        config=config,
        output_dir=base,
        since=since,
        period_end=period_end,
        tracking_started_at=tracking_started_at,
        send_email=send_email,
        allow_interactive_setup=allow_interactive_setup,
    )
    result.state_path = state_path if send_email else None
    if send_email:
        _append_report_attempt(
            state,
            result=result,
            manifest=result.manifest,
            email_result=result.email_result,
            period_start=since,
            period_end=period_end,
        )
        if result.email_result.get("sent"):
            state["lastSuccessfulUntil"] = period_end.isoformat(timespec="seconds")
            state["lastSuccessfulAt"] = datetime.now().isoformat(timespec="seconds")
            state["lastSuccessfulZipPath"] = str(result.zip_path or "")
        _write_report_state(state_path, state)
    return result


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
    cfg = load_report_config(config_path)
    if config:
        cfg.update(config)
    base = output_dir
    if base is None:
        base_folder = cfg.get("difra_base_folder") or Path.home() / "difra"
        base = Path(base_folder) / "daily_reports"
    base = Path(base)
    state_path = _report_state_path(cfg, base)
    state: Dict[str, Any] = _load_report_state(state_path) if send_email else {}
    by_date = state.get("byDate")
    if not isinstance(by_date, dict):
        by_date = {}
    date_key = report_date.isoformat()
    date_state = by_date.get(date_key) if isinstance(by_date.get(date_key), dict) else {}

    roots = [
        Path(cfg.get("measurements_archive_folder") or ""),
        Path(cfg.get("measurements_folder") or ""),
    ]
    all_containers = _candidate_containers([root for root in roots if str(root)], since=None)
    containers = _filter_containers_for_date(all_containers, report_date)
    fingerprint = hashlib.sha256(
        "\n".join(
            f"{path}:{path.stat().st_mtime_ns}:{path.stat().st_size}" for path in containers
        ).encode("utf-8")
    ).hexdigest()

    previous_image_count = _as_int(date_state.get("imageCount"), 0)
    if (
        send_email
        and skip_if_sent
        and date_state.get("sent") is True
        and previous_image_count > 0
    ):
        if not resend_if_changed or date_state.get("fingerprint") == fingerprint:
            result = DailyReportResult(scanned=len(containers))
            result.period_start = datetime.combine(report_date, time.min).isoformat(timespec="seconds")
            result.period_end = datetime.combine(report_date, time.max).isoformat(timespec="seconds")
            result.state_path = state_path
            result.email_result = {
                "sent": False,
                "skipped": True,
                "message": f"daily report already sent for {date_key}",
            }
            return result

    if send_email and skip_if_no_containers and not containers:
        result = DailyReportResult(scanned=0)
        result.period_start = datetime.combine(report_date, time.min).isoformat(timespec="seconds")
        result.period_end = datetime.combine(report_date, time.max).isoformat(timespec="seconds")
        result.state_path = state_path
        result.email_result = {
            "sent": False,
            "skipped": True,
            "message": f"no containers for {date_key}",
        }
        date_state.update(
            {
                "lastAttemptAt": datetime.now().isoformat(timespec="seconds"),
                "sent": False,
                "fingerprint": fingerprint,
                "message": result.email_result["message"],
            }
        )
        by_date[date_key] = date_state
        state["byDate"] = by_date
        _write_report_state(state_path, state)
        return result

    result = build_daily_report_for_containers(
        config=cfg,
        container_paths=containers,
        output_dir=base / date_key,
        send_email=send_email,
        allow_interactive_setup=allow_interactive_setup,
        report_date=report_date,
        tracking_started_at=state.get("trackingStartedAt"),
    )
    result.period_start = datetime.combine(report_date, time.min).isoformat(timespec="seconds")
    result.period_end = datetime.combine(report_date, time.max).isoformat(timespec="seconds")
    result.state_path = state_path if send_email else None
    if send_email:
        _append_report_attempt(
            state,
            result=result,
            manifest=result.manifest,
            email_result=result.email_result,
            period_start=datetime.combine(report_date, time.min),
            period_end=datetime.combine(report_date, time.max),
        )
        sent = bool(result.email_result.get("sent"))
        date_state.update(
            {
                "lastAttemptAt": datetime.now().isoformat(timespec="seconds"),
                "sent": sent,
                "fingerprint": fingerprint,
                "message": _as_text(result.email_result.get("message"), ""),
                "zipPath": str(result.zip_path or ""),
                "validContainers": int(result.valid_containers),
                "imageCount": len(result.images),
            }
        )
        if sent:
            date_state["lastSentAt"] = datetime.now().isoformat(timespec="seconds")
        by_date[date_key] = date_state
        state["byDate"] = by_date
        _write_report_state(state_path, state)
    return result


def send_simple_test_email(
    *,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    allow_interactive_setup: bool = False,
) -> Dict[str, Any]:
    config = load_report_config(config_path)
    if output_dir is None:
        tmp = tempfile.mkdtemp(prefix="difra_daily_report_test_")
        output = Path(tmp)
    else:
        output = Path(output_dir)
    zip_path = create_simple_test_image_zip(output, dpi=DEFAULT_DPI)
    manifest = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "reportDate": datetime.now().strftime("%Y-%m-%d"),
        "kind": "test",
        "scanned": 0,
        "validContainers": 0,
        "projectIds": [],
        "matadorUploaded": 0,
        "imageCount": 2,
    }
    return send_daily_report_email(
        config=config,
        zip_path=zip_path,
        manifest=manifest,
        test=True,
        allow_interactive_setup=allow_interactive_setup,
    )


def run_keychain_setup_self_test(
    *,
    config_path: Optional[Path] = None,
    service: str = "difra_daily_report_smtp_password_self_test",
) -> Dict[str, Any]:
    config = load_report_config(config_path)
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
    if not username:
        return {"ok": False, "message": "daily report SMTP username not configured"}
    if not sys.stdin.isatty():
        return {"ok": False, "message": "interactive terminal required"}

    _delete_stored_smtp_password(account=username, service=service)
    password = _interactive_keychain_password_setup(
        config=config,
        account=username,
        service=service,
    )
    if not password:
        return {"ok": False, "message": "setup did not produce a password"}
    loaded = _read_stored_smtp_password(account=username, service=service)
    removed = _delete_stored_smtp_password(account=username, service=service)
    return {
        "ok": bool(loaded and loaded == password and removed),
        "account": username,
        "service": service,
        "decrypted": bool(password),
        "readBack": bool(loaded and loaded == password),
        "removed": bool(removed),
    }
