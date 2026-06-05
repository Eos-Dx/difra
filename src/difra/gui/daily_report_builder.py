"""Daily report build/run orchestration."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from difra.gui.daily_report_common import (
    DEFAULT_DPI,
    DEFAULT_POINTS,
    _append_report_attempt,
    _as_int,
    _as_text,
    _candidate_containers,
    _filter_containers_for_date,
    _load_report_state,
    _parse_report_datetime,
    _report_state_path,
    _write_report_state,
    load_report_config,
)
from difra.gui.daily_report_models import DailyReportResult, DetectorSeries
from difra.gui.daily_report_rendering import (
    _write_report_poni_files,
    build_report_manifest_diagnostics,
    create_zip,
    render_report_images,
    render_report_overview_image,
    write_report_diagnostics_h5,
    write_report_manifest,
)


def collect_report_series(
    container_paths: Iterable[Path],
    *,
    points: int = DEFAULT_POINTS,
) -> tuple[List[DetectorSeries], List[str], int]:
    raise RuntimeError("collect_report_series dependency was not injected")


def summarize_valid_containers(container_paths: Iterable[Path]) -> Dict[str, Any]:
    raise RuntimeError("summarize_valid_containers dependency was not injected")


def send_daily_report_email(
    *,
    config: Optional[Dict[str, Any]],
    zip_path: Path,
    manifest: Dict[str, Any],
    test: bool = False,
    allow_interactive_setup: bool = False,
) -> Dict[str, Any]:
    raise RuntimeError("send_daily_report_email dependency was not injected")


def _no_report_images_email_result() -> Dict[str, Any]:
    raise RuntimeError("_no_report_images_email_result dependency was not injected")


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
    overview_image = (
        render_report_overview_image(series, out / "overview_report.png", dpi=DEFAULT_DPI)
        if series
        else None
    )
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
    manifest["diagnosticH5"] = "report_diagnostics.h5"
    if overview_image is not None:
        manifest["overviewImage"] = "overview_report.png"
    result.manifest = manifest
    diagnostics_h5 = write_report_diagnostics_h5(out, series, manifest=manifest)
    write_report_manifest(out, manifest)
    if create_archive:
        extra_files = dict(poni_files)
        extra_files["report_diagnostics.h5"] = diagnostics_h5
        if overview_image is not None:
            extra_files["overview_report.png"] = overview_image
        result.zip_path = create_zip(
            out / f"difra_daily_valid_container_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
            result.images,
            manifest=manifest,
            extra_files=extra_files,
        )
    if send_email:
        if not result.images:
            result.email_result = _no_report_images_email_result()
        elif not result.zip_path:
            result.email_result = {
                "sent": False,
                "skipped": False,
                "message": "ZIP archive was not created",
            }
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
    create_archive: bool = True,
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
    overview_image = (
        render_report_overview_image(series, out / "overview_report.png", dpi=DEFAULT_DPI)
        if series
        else None
    )
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
    manifest["diagnosticH5"] = "report_diagnostics.h5"
    if overview_image is not None:
        manifest["overviewImage"] = "overview_report.png"
    result.manifest = manifest
    diagnostics_h5 = write_report_diagnostics_h5(out, series, manifest=manifest)
    write_report_manifest(out, manifest)
    if create_archive:
        extra_files = dict(poni_files)
        extra_files["report_diagnostics.h5"] = diagnostics_h5
        if overview_image is not None:
            extra_files["overview_report.png"] = overview_image
        result.zip_path = create_zip(
            out / f"difra_selected_valid_container_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
            result.images,
            manifest=manifest,
            extra_files=extra_files,
        )
    if send_email:
        if not result.images:
            result.email_result = _no_report_images_email_result()
        elif not result.zip_path:
            result.email_result = {
                "sent": False,
                "skipped": False,
                "message": "ZIP archive was not created",
            }
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


def build_report_overview_image_for_containers(
    *,
    config: Optional[Dict[str, Any]],
    container_paths: Iterable[Path],
    image_path: Path,
) -> DailyReportResult:
    paths = sorted({Path(path) for path in container_paths if Path(path).exists()})
    result = DailyReportResult(scanned=len(paths))
    series, skipped, valid_count = collect_report_series(paths, points=DEFAULT_POINTS)
    result.skipped.extend(skipped)
    result.valid_containers = valid_count
    result.images = [
        render_report_overview_image(series, Path(image_path), dpi=DEFAULT_DPI)
    ]
    result.manifest = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "scanned": result.scanned,
        "validContainers": result.valid_containers,
        "selectedContainers": [str(path) for path in paths],
        "overviewImage": str(Path(image_path)),
        "skipped": result.skipped[:200],
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
