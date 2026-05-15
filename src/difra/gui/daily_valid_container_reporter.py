"""Daily valid session-container plot report email."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
import json
import os
from pathlib import Path
import smtplib
import socket
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple
import zipfile

import h5py
import numpy as np

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402


DEFAULT_REPORT_RECIPIENT = "sdenisov@matur.co.uk"
DEFAULT_REPORT_SENDER = "difra-upload@company.co.uk"
DEFAULT_POINTS = 100
DEFAULT_DPI = 200
SAXS_RANGE = (1.0, 3.0)
WAXS_RANGE = (2.0, 23.0)


@dataclass(frozen=True)
class DetectorSeries:
    specimen_id: str
    detector_group: str
    detector_alias: str
    q: np.ndarray
    intensity: np.ndarray
    source_container: Path
    source_dataset: str


@dataclass
class DailyReportResult:
    scanned: int = 0
    valid_containers: int = 0
    skipped: List[str] = field(default_factory=list)
    images: List[Path] = field(default_factory=list)
    zip_path: Optional[Path] = None
    email_result: Dict[str, Any] = field(default_factory=dict)


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    text = str(value).strip()
    return text if text else default


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _config_value(
    config: Optional[Dict[str, Any]],
    key: str,
    env_key: str,
    default: Any,
    *,
    fallback_key: str = "",
    fallback_env_key: str = "",
) -> Any:
    if env_key in os.environ:
        return os.environ.get(env_key)
    if fallback_env_key and fallback_env_key in os.environ:
        return os.environ.get(fallback_env_key)
    if isinstance(config, dict) and key in config:
        return config.get(key)
    if fallback_key and isinstance(config, dict) and fallback_key in config:
        return config.get(fallback_key)
    return default


def load_report_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    candidates = []
    if config_path is not None:
        candidates.append(Path(config_path))
    root = Path(__file__).resolve().parents[1]
    candidates.extend(
        [
            root / "resources" / "config" / "global.json",
            root / "resources" / "config" / "main.json",
        ]
    )
    for candidate in candidates:
        try:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def _safe_token(value: str, fallback: str = "unknown") -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    token = "_".join(part for part in token.split("_") if part)
    return token or fallback


def _candidate_containers(roots: Iterable[Path], *, since: Optional[datetime]) -> List[Path]:
    paths: List[Path] = []
    min_mtime = since.timestamp() if since is not None else None
    for root in roots:
        folder = Path(root)
        if not folder.exists():
            continue
        for path in folder.rglob("*.h5"):
            name_upper = path.name.upper()
            if "H5OLD" in name_upper:
                continue
            if not (path.name.endswith(".nxs.h5") or path.name.endswith(".h5")):
                continue
            if min_mtime is not None and path.stat().st_mtime < min_mtime:
                continue
            paths.append(path)
    return sorted(set(paths))


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


def _detector_group(alias: str, detector_name: str) -> str:
    token = f"{alias} {detector_name}".upper()
    if any(item in token for item in ("PRIMARY", "SAXS", "DET_PRIMARY", "DET_SAXS")):
        return "PRIMARY"
    if any(item in token for item in ("SECONDARY", "WAXS", "DET_SECONDARY", "DET_WAXS")):
        return "SECONDARY"
    return ""


def _resolve_poni_text(h5f: h5py.File, det_group: h5py.Group, alias: str) -> str:
    candidates = []
    for attr_name in ("poni_ref", "poni_path"):
        ref = _as_text(det_group.attrs.get(attr_name)).strip()
        if ref:
            candidates.append(ref)
    role = str(det_group.name.rsplit("/", 1)[-1])
    if role.startswith("det_"):
        candidates.extend(
            [
                f"/entry/technical/poni/poni_{role}",
                f"/entry/technical/poni/poni_{role[4:]}",
            ]
        )
    tokens = {
        alias.strip().lower(),
        role.lower(),
        role.replace("det_", "").lower(),
        _as_text(det_group.attrs.get("detector_alias")).lower(),
        _as_text(det_group.attrs.get("detector_id")).lower(),
    }
    poni_group = h5f.get("/entry/technical/poni")
    if poni_group is not None:
        for name in sorted(poni_group.keys()):
            lower_name = name.lower()
            if any(token and token in lower_name for token in tokens):
                candidates.append(f"/entry/technical/poni/{name}")
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen or candidate not in h5f:
            continue
        seen.add(candidate)
        text = _as_text(h5f[candidate][()]).strip()
        if text:
            return text
    return ""


def integrate_detector_signal(
    data: np.ndarray,
    poni_text: str,
    *,
    npt: int = 400,
) -> Tuple[np.ndarray, np.ndarray]:
    arr = np.asarray(data, dtype=float)
    if arr.ndim != 2 or not poni_text.strip():
        return np.asarray([]), np.asarray([])
    try:
        from difra.gui.technical.analysis_compat import (
            initialize_azimuthal_integrator_poni_text,
        )

        ai = initialize_azimuthal_integrator_poni_text(poni_text)
        result = ai.integrate1d(
            arr,
            max(int(npt), 2),
            unit="q_nm^-1",
            error_model="azimuthal",
        )
        q = np.asarray(result.radial, dtype=float).reshape(-1)
        intensity = np.asarray(result.intensity, dtype=float).reshape(-1)
        finite = np.isfinite(q) & np.isfinite(intensity)
        return q[finite], intensity[finite]
    except Exception:
        return np.asarray([]), np.asarray([])


def _resample_range(
    q: np.ndarray,
    intensity: np.ndarray,
    q_range: Tuple[float, float],
    *,
    points: int,
) -> Tuple[np.ndarray, np.ndarray]:
    q = np.asarray(q, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    finite = np.isfinite(q) & np.isfinite(intensity)
    q = q[finite]
    intensity = intensity[finite]
    if q.size < 2:
        return np.asarray([]), np.asarray([])
    order = np.argsort(q)
    q = q[order]
    intensity = intensity[order]
    mask = (q >= float(q_range[0])) & (q <= float(q_range[1]))
    if np.count_nonzero(mask) < 2:
        return np.asarray([]), np.asarray([])
    target_q = np.linspace(float(q_range[0]), float(q_range[1]), int(points))
    target_i = np.interp(target_q, q[mask], intensity[mask])
    return target_q, target_i


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
                            if group_name not in {"PRIMARY", "SECONDARY"}:
                                continue
                            q_range = SAXS_RANGE if group_name == "PRIMARY" else WAXS_RANGE
                            poni_text = _resolve_poni_text(h5f, det_group, alias)
                            q, intensity = integrate_detector_signal(
                                det_group["processed_signal"][()],
                                poni_text,
                                npt=400,
                            )
                            q_out, i_out = _resample_range(
                                q,
                                intensity,
                                q_range,
                                points=points,
                            )
                            if q_out.size != points:
                                skipped.append(
                                    f"{path.name}:{det_group.name}: no q data in {q_range[0]}-{q_range[1]} nm^-1"
                                )
                                continue
                            series.append(
                                DetectorSeries(
                                    specimen_id=specimen_id,
                                    detector_group=group_name,
                                    detector_alias=alias,
                                    q=q_out,
                                    intensity=i_out,
                                    source_container=path,
                                    source_dataset=det_group["processed_signal"].name,
                                )
                            )
        except Exception as exc:
            skipped.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return series, skipped, valid_count


def render_report_images(
    series: Iterable[DetectorSeries],
    output_dir: Path,
    *,
    dpi: int = DEFAULT_DPI,
) -> List[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    grouped: Dict[Tuple[str, str], List[DetectorSeries]] = {}
    for item in series:
        grouped.setdefault((item.specimen_id, item.detector_group), []).append(item)

    images: List[Path] = []
    for (specimen_id, group_name), items in sorted(grouped.items()):
        q_range = SAXS_RANGE if group_name == "PRIMARY" else WAXS_RANGE
        range_label = "SAXS 1-3 nm^-1" if group_name == "PRIMARY" else "WAXS 2-23 nm^-1"
        fig, ax = plt.subplots(figsize=(8, 5), dpi=dpi)
        for index, item in enumerate(items, start=1):
            label = f"{item.detector_alias} #{index}"
            ax.plot(item.q, item.intensity, linewidth=1.1, alpha=0.85, label=label)
        ax.set_title(f"{specimen_id} | {group_name} | {range_label}")
        ax.set_xlabel("q (nm^-1)")
        ax.set_ylabel("I(q)")
        ax.set_xlim(q_range)
        ax.grid(True, alpha=0.25)
        if len(items) <= 12:
            ax.legend(fontsize=7)
        fig.tight_layout()
        image_path = output / (
            f"{_safe_token(specimen_id)}_{group_name}_{range_label.replace(' ', '_').replace('^-', '-')}.png"
        )
        fig.savefig(image_path, dpi=dpi)
        plt.close(fig)
        images.append(image_path)
    return images


def create_zip(zip_path: Path, image_paths: Iterable[Path], *, manifest: Dict[str, Any]) -> Path:
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        for image_path in image_paths:
            path = Path(image_path)
            archive.write(path, arcname=path.name)
    return target


def create_simple_test_image_zip(output_dir: Path, *, dpi: int = DEFAULT_DPI) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    image_paths = []
    q = np.linspace(1.0, 3.0, DEFAULT_POINTS)
    for name, y in (
        ("test_PRIMARY_SAXS_1-3nm-1.png", np.sin(q * 4.0) + 2.0),
        ("test_SECONDARY_WAXS_2-23nm-1.png", np.cos(q * 3.0) + 2.0),
    ):
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
    recipient = _as_text(
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
    prefix = "[DiFRA] Daily valid container plot report"
    if test:
        prefix = "[DiFRA] TEST daily plot report"
    subject = (
        f"{prefix}: {manifest.get('imageCount', 0)} image(s), "
        f"{manifest.get('validContainers', 0)} valid container(s)"
    )

    body = "\n".join(
        [
            "Daily DiFRA valid-container plot report.",
            "",
            f"Host: {socket.gethostname()}",
            f"Generated: {manifest.get('generatedAt', datetime.now().isoformat(timespec='seconds'))}",
            f"Scanned: {manifest.get('scanned', 0)}",
            f"Valid containers: {manifest.get('validContainers', 0)}",
            f"Images: {manifest.get('imageCount', 0)}",
            "",
            "Attached ZIP contains 200 dpi PNG files and manifest.json.",
        ]
    )

    message = EmailMessage()
    message["To"] = recipient
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
    send_email: bool = False,
) -> DailyReportResult:
    cfg = dict(config or {})
    roots = [
        Path(cfg.get("measurements_archive_folder") or ""),
        Path(cfg.get("measurements_folder") or ""),
    ]
    containers = _candidate_containers([root for root in roots if str(root)], since=since)
    result = DailyReportResult(scanned=len(containers))
    series, skipped, valid_count = collect_report_series(containers, points=DEFAULT_POINTS)
    result.skipped.extend(skipped)
    result.valid_containers = valid_count
    out = Path(output_dir)
    image_dir = out / "images"
    result.images = render_report_images(series, image_dir, dpi=DEFAULT_DPI)
    manifest = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "since": since.isoformat(timespec="seconds") if since else None,
        "scanned": result.scanned,
        "validContainers": result.valid_containers,
        "imageCount": len(result.images),
        "skipped": result.skipped[:200],
    }
    result.zip_path = create_zip(
        out / f"difra_daily_valid_container_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
        result.images,
        manifest=manifest,
    )
    if send_email:
        result.email_result = send_daily_report_email(
            config=cfg,
            zip_path=result.zip_path,
            manifest=manifest,
        )
    return result


def run_daily_report_from_config(
    *,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    since_days: float = 1.0,
    send_email: bool = False,
) -> DailyReportResult:
    config = load_report_config(config_path)
    base = output_dir
    if base is None:
        base_folder = config.get("difra_base_folder") or Path.home() / "difra"
        base = Path(base_folder) / "daily_reports"
    since = datetime.now() - timedelta(days=float(since_days))
    return build_daily_report(
        config=config,
        output_dir=Path(base),
        since=since,
        send_email=send_email,
    )


def send_simple_test_email(
    *,
    config_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
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
        "kind": "test",
        "scanned": 0,
        "validContainers": 0,
        "imageCount": 2,
    }
    return send_daily_report_email(
        config=config,
        zip_path=zip_path,
        manifest=manifest,
        test=True,
    )
