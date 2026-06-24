#!/usr/bin/env python3
"""Export DIFRA archive days to OneDrive as ZIP-only folders."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = (
    REPO_ROOT / "src" / "difra" / "resources" / "config" / "main_win.json"
)
ARCHIVE_KINDS = ("measurements", "technical")


@dataclass
class DayZipRecord:
    kind: str
    day_token: str
    zip_name: str
    manifest_name: str
    source_items: list[Path]
    source_fingerprints: list[dict[str, str]] = field(default_factory=list)
    h5_summaries: list[dict[str, str]] = field(default_factory=list)
    zip_bytes: int = 0
    zip_sha256: str = ""


class SyncSummary:
    def __init__(
        self,
        *,
        source_root: Path,
        destination_root: Path,
        scanned_files: int = 0,
        copied_files: int = 0,
        updated_files: int = 0,
        skipped_files: int = 0,
        permission_error_files: int = 0,
        transferred_bytes: int = 0,
        removed_destination_items: int = 0,
        created_zip_files: int = 0,
        removed_staging_files: int = 0,
    ) -> None:
        self.source_root = Path(source_root)
        self.destination_root = Path(destination_root)
        self.scanned_files = int(scanned_files)
        self.copied_files = int(copied_files)
        self.updated_files = int(updated_files)
        self.skipped_files = int(skipped_files)
        self.permission_error_files = int(permission_error_files)
        self.transferred_bytes = int(transferred_bytes)
        self.removed_destination_items = int(removed_destination_items)
        self.created_zip_files = int(created_zip_files)
        self.removed_staging_files = int(removed_staging_files)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export DIFRA archive days to OneDrive as ZIP files plus text manifests.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="JSON config used to resolve archive roots.",
    )
    parser.add_argument(
        "--source-root",
        default="",
        help="Archive root containing measurements/ and technical/.",
    )
    parser.add_argument(
        "--mirror-root",
        default="",
        help="OneDrive root before Archive/ is appended.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report work without writing/deleting files.",
    )
    return parser


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as file_handle:
        payload = json.load(file_handle)
    return payload if isinstance(payload, dict) else {}


def _coerce_path(value: str) -> Path:
    text = str(value or "").strip()
    if not text:
        return Path()
    if ":\\" in text or text.startswith("\\\\"):
        return Path(PureWindowsPath(text).as_posix())
    return Path(text)


def resolve_sync_roots_from_config(
    config: dict | None,
    *,
    source_root: str = "",
    mirror_root: str = "",
) -> tuple[Path, Path]:
    cfg = config if isinstance(config, dict) else {}
    resolved_source = str(source_root or "").strip()
    resolved_mirror = str(mirror_root or "").strip()

    if not resolved_source:
        measurements_archive = str(cfg.get("measurements_archive_folder") or "").strip()
        technical_archive = str(cfg.get("technical_archive_folder") or "").strip()
        if measurements_archive:
            resolved_source = str(_coerce_path(measurements_archive).parent)
        elif technical_archive:
            resolved_source = str(_coerce_path(technical_archive).parent)
        else:
            base_folder = str(cfg.get("difra_base_folder") or "").strip()
            if base_folder:
                resolved_source = str(_coerce_path(base_folder) / "Archive")
    if not resolved_source:
        raise ValueError("Could not resolve archive source root from config.")

    if not resolved_mirror:
        resolved_mirror = str(
            cfg.get("technical_archive_mirror_folder")
            or cfg.get("measurements_archive_mirror_folder")
            or cfg.get("session_archive_mirror_folder")
            or ""
        ).strip()
    if not resolved_mirror:
        raise ValueError("Could not resolve OneDrive mirror root from config.")

    return _coerce_path(resolved_source), _coerce_path(resolved_mirror)


def resolve_sync_roots(
    *,
    config_path: Path,
    source_root: str = "",
    mirror_root: str = "",
) -> tuple[Path, Path]:
    if str(source_root or "").strip() and str(mirror_root or "").strip():
        return resolve_sync_roots_from_config(
            {},
            source_root=source_root,
            mirror_root=mirror_root,
        )
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")
    return resolve_sync_roots_from_config(
        _read_json(config_file),
        source_root=source_root,
        mirror_root=mirror_root,
    )


def archive_zip_sync_command(
    *,
    source_root: Path,
    mirror_root: Path,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> list[str]:
    command = [
        python_executable or sys.executable,
        str(Path(__file__).resolve()),
        "--source-root",
        str(source_root),
        "--mirror-root",
        str(mirror_root),
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def start_archive_zip_sync_process(
    *,
    source_root: Path,
    mirror_root: Path,
    dry_run: bool = False,
) -> subprocess.Popen:
    return subprocess.Popen(
        archive_zip_sync_command(
            source_root=source_root,
            mirror_root=mirror_root,
            dry_run=dry_run,
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=os.name != "nt",
    )


def _day_token_from_text(text: str) -> str | None:
    import re

    match = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", str(text or ""))
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}{match.group(3)}"


def _h5_summary(path: Path) -> dict[str, str]:
    keys = (
        "session_id",
        "sample_id",
        "sample_name",
        "matadorSampleId",
        "matadorSpecimenId",
        "project_id",
        "operator_id",
        "acquisition_date",
        "creation_timestamp",
        "distance_cm",
        "technical_type",
        "container_type",
    )
    summary = {"file": str(path.name)}
    try:
        import h5py

        with h5py.File(path, "r") as h5f:
            for key in keys:
                value = h5f.attrs.get(key)
                if value is None:
                    continue
                if isinstance(value, bytes):
                    text = value.decode("utf-8", errors="replace")
                else:
                    text = str(value)
                if text.strip():
                    summary[key] = text.strip()
    except Exception:
        pass
    return summary


def _day_token_for_item(item: Path) -> str:
    for candidate in (item.name, item.stem, item.parent.name):
        token = _day_token_from_text(candidate)
        if token:
            return token
    h5_files = []
    if item.is_file() and item.suffix.lower() in {".h5", ".nxs"}:
        h5_files = [item]
    elif item.is_dir():
        h5_files = sorted(item.rglob("*.nxs.h5")) + sorted(item.rglob("*.h5"))
    for h5_path in h5_files:
        summary = _h5_summary(h5_path)
        for key in ("acquisition_date", "creation_timestamp"):
            token = _day_token_from_text(summary.get(key, ""))
            if token:
                return token
    return time.strftime("%Y%m%d")


def _source_items_by_kind(source_root: Path, kind: str) -> list[Path]:
    kind_root = Path(source_root) / kind
    if not kind_root.exists() or not kind_root.is_dir():
        return []
    return sorted(
        item
        for item in kind_root.iterdir()
        if item.name != ".onedrive_zip_staging" and not item.name.startswith(".")
    )


def _count_files(path: Path) -> int:
    if path.is_file():
        return 1
    return sum(1 for child in path.rglob("*") if child.is_file())


def _add_to_zip(zf: zipfile.ZipFile, item: Path, *, arc_prefix: str) -> int:
    written = 0
    if item.is_file():
        zf.write(item, f"{arc_prefix}/{item.name}")
        return 1
    for file_path in sorted(item.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(item.parent).as_posix()
        zf.write(file_path, f"{arc_prefix}/{rel}")
        written += 1
    return written


def _source_files(items: Iterable[Path]) -> list[Path]:
    files = []
    for item in items:
        if item.is_file():
            files.append(item)
            continue
        if item.is_dir():
            files.extend(path for path in item.rglob("*") if path.is_file())
    return sorted(files)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file_hash(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    if left.stat().st_size != right.stat().st_size:
        return False
    return _sha256(left) == _sha256(right)


def _source_file_fingerprints(
    items: Iterable[Path],
    *,
    source_root: Path,
) -> list[dict[str, str]]:
    fingerprints = []
    for file_path in _source_files(items):
        try:
            rel = file_path.relative_to(source_root).as_posix()
        except Exception:
            rel = str(file_path)
        try:
            size = str(int(file_path.stat().st_size))
            digest = _sha256(file_path)
        except Exception:
            continue
        fingerprints.append({"path": rel, "size": size, "sha256": digest})
    return sorted(fingerprints, key=lambda item: item["path"])


def _parse_key_value_line(line: str) -> dict[str, str]:
    text = str(line or "").strip()
    if text.startswith("- "):
        text = text[2:].strip()
    payload = {}
    for part in text.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def _manifest_fingerprints(manifest_path: Path) -> list[dict[str, str]]:
    if not manifest_path.exists():
        return []
    in_section = False
    fingerprints = []
    for line in manifest_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped == "Source file fingerprints:":
            in_section = True
            continue
        if in_section and not stripped:
            break
        if not in_section or not stripped.startswith("- "):
            continue
        parsed = _parse_key_value_line(stripped)
        if {"path", "size", "sha256"} <= set(parsed):
            fingerprints.append(
                {
                    "path": parsed["path"],
                    "size": parsed["size"],
                    "sha256": parsed["sha256"],
                }
            )
    return sorted(fingerprints, key=lambda item: item["path"])


def _read_manifest_value(manifest_path: Path, prefix: str) -> str:
    if not manifest_path.exists():
        return ""
    for line in manifest_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def _manifest_text(record: DayZipRecord, *, source_root: Path) -> str:
    lines = [
        f"DIFRA OneDrive archive manifest",
        f"Kind: {record.kind}",
        f"Day: {record.day_token}",
        f"ZIP: {record.zip_name}",
        f"ZIP bytes: {record.zip_bytes}",
        f"ZIP sha256: {record.zip_sha256}",
        "",
        "Source items:",
    ]
    for item in record.source_items:
        try:
            rel = item.relative_to(source_root).as_posix()
        except Exception:
            rel = str(item)
        lines.append(f"- {rel}")
    lines.extend(["", "H5 containers:"])
    for summary in record.h5_summaries:
        parts = [f"{key}={value}" for key, value in sorted(summary.items())]
        lines.append("- " + "; ".join(parts))
    if not record.h5_summaries:
        lines.append("- none found")
    lines.extend(["", "Source file fingerprints:"])
    for fingerprint in record.source_fingerprints:
        lines.append(
            "- "
            f"path={fingerprint.get('path', '')}; "
            f"size={fingerprint.get('size', '')}; "
            f"sha256={fingerprint.get('sha256', '')}"
        )
    if not record.source_fingerprints:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _global_manifest_text(records: list[DayZipRecord], *, kind: str) -> str:
    lines = [
        "DIFRA OneDrive archive index",
        f"Kind: {kind}",
        "",
        "Days:",
    ]
    for record in sorted(records, key=lambda item: item.day_token):
        samples = []
        for summary in record.h5_summaries:
            sample = (
                summary.get("sample_id")
                or summary.get("sample_name")
                or summary.get("matadorSampleId")
                or summary.get("matadorSpecimenId")
                or summary.get("file")
                or ""
            )
            if sample and sample not in samples:
                samples.append(sample)
        sample_text = ", ".join(samples) if samples else "no H5 metadata"
        lines.append(
            f"- {record.day_token}: {record.zip_name}; containers={len(record.h5_summaries)}; samples={sample_text}"
        )
    if not records:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _group_source_items_by_day(source_root: Path, kind: str) -> tuple[dict[str, list[Path]], int]:
    grouped: dict[str, list[Path]] = {}
    scanned_files = 0
    for item in _source_items_by_kind(source_root, kind):
        day = _day_token_for_item(item)
        grouped.setdefault(day, []).append(item)
        scanned_files += _count_files(item)
    return grouped, scanned_files


def _h5_summaries_for_items(
    items: Iterable[Path],
    *,
    source_root: Path,
) -> list[dict[str, str]]:
    h5_summaries = []
    for item in items:
        h5_candidates: Iterable[Path]
        if item.is_file() and item.suffix.lower() in {".h5", ".nxs"}:
            h5_candidates = [item]
        elif item.is_dir():
            h5_candidates = [*item.rglob("*.nxs.h5"), *item.rglob("*.h5")]
        else:
            h5_candidates = []
        for h5_path in sorted(h5_candidates):
            summary = _h5_summary(h5_path)
            try:
                summary["path"] = h5_path.relative_to(source_root).as_posix()
            except Exception:
                summary["path"] = str(h5_path)
            h5_summaries.append(summary)
    return h5_summaries


def _day_record_from_sources(
    *,
    source_root: Path,
    kind: str,
    day: str,
    items: list[Path],
    destination_manifest: Path,
) -> DayZipRecord:
    zip_name = f"{kind}_{day}.zip"
    manifest_name = f"{kind}_{day}.txt"
    zip_bytes = _read_manifest_value(destination_manifest, "ZIP bytes:")
    zip_sha256 = _read_manifest_value(destination_manifest, "ZIP sha256:")
    return DayZipRecord(
        kind=kind,
        day_token=day,
        zip_name=zip_name,
        manifest_name=manifest_name,
        source_items=items,
        source_fingerprints=_source_file_fingerprints(
            items,
            source_root=source_root,
        ),
        h5_summaries=_h5_summaries_for_items(items, source_root=source_root),
        zip_bytes=int(zip_bytes) if zip_bytes.isdigit() else 0,
        zip_sha256=zip_sha256,
    )


def _build_day_zip(
    *,
    record: DayZipRecord,
    staging_root: Path,
) -> Path:
    zip_path = staging_root / record.kind / record.zip_name
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in record.source_items:
            _add_to_zip(zf, item, arc_prefix=record.day_token)
    record.zip_bytes = int(zip_path.stat().st_size)
    record.zip_sha256 = _sha256(zip_path)
    return zip_path


def _day_manifest_is_current(manifest_path: Path, fingerprints: list[dict[str, str]]) -> bool:
    return bool(manifest_path.exists()) and _manifest_fingerprints(manifest_path) == fingerprints


def _clean_destination_kind(kind_root: Path, *, bootstrap: bool, dry_run: bool) -> int:
    removed = 0
    if not bootstrap or not kind_root.exists():
        return 0
    for child in sorted(kind_root.iterdir()):
        removed += 1
        if dry_run:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return removed


def _copy_artifact(source: Path, destination: Path, *, dry_run: bool) -> tuple[str, int]:
    destination_exists = destination.exists()
    if _same_file_hash(source, destination):
        return "skipped", 0
    size = int(source.stat().st_size)
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return ("updated" if destination_exists else "copied"), size


def sync_archive_tree(
    *,
    source_root: Path,
    mirror_root: Path,
    dry_run: bool = False,
) -> SyncSummary:
    source = Path(source_root)
    if not source.exists():
        raise FileNotFoundError(f"Source archive root not found: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"Source archive root is not a directory: {source}")

    destination_root = Path(mirror_root) / "Archive"
    if not dry_run:
        destination_root.mkdir(parents=True, exist_ok=True)

    scanned_files = copied_files = updated_files = skipped_files = 0
    transferred_bytes = removed_destination_items = created_zip_files = 0
    removed_staging_files = 0

    with tempfile.TemporaryDirectory(prefix="difra_onedrive_zip_") as tmp_dir:
        staging_root = Path(tmp_dir)
        for kind in ARCHIVE_KINDS:
            grouped, kind_scanned = _group_source_items_by_day(source, kind)
            scanned_files += kind_scanned
            kind_root = destination_root / kind
            bootstrap = not (kind_root / f"{kind}_manifest.txt").exists()
            if not dry_run:
                kind_root.mkdir(parents=True, exist_ok=True)
            removed_destination_items += _clean_destination_kind(
                kind_root,
                bootstrap=bootstrap,
                dry_run=dry_run,
            )

            records = []
            for day, items in sorted(grouped.items()):
                destination_manifest = kind_root / f"{kind}_{day}.txt"
                record = _day_record_from_sources(
                    source_root=source,
                    kind=kind,
                    day=day,
                    items=items,
                    destination_manifest=destination_manifest,
                )
                records.append(record)
                if (
                    not bootstrap
                    and (kind_root / record.zip_name).exists()
                    and _day_manifest_is_current(
                        destination_manifest,
                        record.source_fingerprints,
                    )
                ):
                    skipped_files += 2
                    continue

                zip_source = _build_day_zip(
                    record=record,
                    staging_root=staging_root,
                )
                created_zip_files += 1
                manifest_source = staging_root / kind / record.manifest_name
                manifest_source.write_text(
                    _manifest_text(record, source_root=source),
                    encoding="utf-8",
                )
                for artifact_source in (zip_source, manifest_source):
                    status, size = _copy_artifact(
                        artifact_source,
                        kind_root / artifact_source.name,
                        dry_run=dry_run,
                    )
                    copied_files += int(status == "copied")
                    updated_files += int(status == "updated")
                    skipped_files += int(status == "skipped")
                    transferred_bytes += size

            global_manifest = staging_root / kind / f"{kind}_manifest.txt"
            global_manifest.parent.mkdir(parents=True, exist_ok=True)
            global_manifest.write_text(
                _global_manifest_text(records, kind=kind),
                encoding="utf-8",
            )
            status, size = _copy_artifact(
                global_manifest,
                kind_root / global_manifest.name,
                dry_run=dry_run,
            )
            copied_files += int(status == "copied")
            updated_files += int(status == "updated")
            skipped_files += int(status == "skipped")
            transferred_bytes += size
            removed_staging_files += len(list((staging_root / kind).glob("*")))

    return SyncSummary(
        source_root=source,
        destination_root=destination_root,
        scanned_files=scanned_files,
        copied_files=copied_files,
        updated_files=updated_files,
        skipped_files=skipped_files,
        permission_error_files=0,
        transferred_bytes=transferred_bytes,
        removed_destination_items=removed_destination_items,
        created_zip_files=created_zip_files,
        removed_staging_files=removed_staging_files,
    )


def main() -> int:
    args = _build_parser().parse_args()
    try:
        source_root, mirror_root = resolve_sync_roots(
            config_path=Path(args.config),
            source_root=args.source_root,
            mirror_root=args.mirror_root,
        )
        summary = sync_archive_tree(
            source_root=source_root,
            mirror_root=mirror_root,
            dry_run=bool(args.dry_run),
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"Source archive root: {summary.source_root}")
    print(f"Destination archive root: {summary.destination_root}")
    print(f"Scanned source files: {summary.scanned_files}")
    print(f"Created day ZIPs: {summary.created_zip_files}")
    print(f"New artifacts: {summary.copied_files}")
    print(f"Updated artifacts: {summary.updated_files}")
    print(f"Skipped artifacts: {summary.skipped_files}")
    print(f"Removed destination items: {summary.removed_destination_items}")
    print(f"Removed staging files: {summary.removed_staging_files}")
    print(f"Transferred bytes: {summary.transferred_bytes}")
    if args.dry_run:
        print("Dry run only: no files were written or deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
