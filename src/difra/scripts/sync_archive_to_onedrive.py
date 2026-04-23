#!/usr/bin/env python3
"""Mirror the Windows DIFRA archive tree into the configured OneDrive backup root."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path, PureWindowsPath


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = (
    REPO_ROOT / "src" / "difra" / "resources" / "config" / "main_win.json"
)


class SyncSummary:
    def __init__(
        self,
        *,
        source_root: Path,
        destination_root: Path,
        scanned_files: int,
        copied_files: int,
        updated_files: int,
        skipped_files: int,
        transferred_bytes: int,
    ) -> None:
        self.source_root = Path(source_root)
        self.destination_root = Path(destination_root)
        self.scanned_files = int(scanned_files)
        self.copied_files = int(copied_files)
        self.updated_files = int(updated_files)
        self.skipped_files = int(skipped_files)
        self.transferred_bytes = int(transferred_bytes)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy the DIFRA archive tree into the configured OneDrive mirror root.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the JSON config file used to resolve default archive roots.",
    )
    parser.add_argument(
        "--source-root",
        default="",
        help="Archive root to sync (defaults to the parent of measurements/technical archive folders).",
    )
    parser.add_argument(
        "--mirror-root",
        default="",
        help="OneDrive mirror root before the final Archive folder is appended.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be copied without writing files.",
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
    """Resolve source archive root and OneDrive mirror root from an in-memory config."""
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
    """Resolve source archive root and OneDrive mirror root."""
    if str(source_root or "").strip() and str(mirror_root or "").strip():
        return resolve_sync_roots_from_config(
            {},
            source_root=source_root,
            mirror_root=mirror_root,
        )
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")
    cfg = _read_json(config_file)
    return resolve_sync_roots_from_config(
        cfg,
        source_root=source_root,
        mirror_root=mirror_root,
    )


def _needs_copy(source_path: Path, destination_path: Path) -> bool:
    if not destination_path.exists():
        return True
    source_stat = source_path.stat()
    destination_stat = destination_path.stat()
    if source_stat.st_size != destination_stat.st_size:
        return True
    return int(source_stat.st_mtime_ns) != int(destination_stat.st_mtime_ns)


def sync_archive_tree(
    *,
    source_root: Path,
    mirror_root: Path,
    dry_run: bool = False,
) -> SyncSummary:
    """Copy one archive root into mirror_root/<archive_root_name>."""
    source = Path(source_root)
    if not source.exists():
        raise FileNotFoundError(f"Source archive root not found: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"Source archive root is not a directory: {source}")

    destination_root = Path(mirror_root) / source.name
    scanned_files = 0
    copied_files = 0
    updated_files = 0
    skipped_files = 0
    transferred_bytes = 0

    if not dry_run:
        destination_root.mkdir(parents=True, exist_ok=True)

    for file_path in sorted(source.rglob("*")):
        if not file_path.is_file():
            continue
        scanned_files += 1
        relative_path = file_path.relative_to(source)
        destination_path = destination_root / relative_path
        destination_exists = destination_path.exists()
        if not _needs_copy(file_path, destination_path):
            skipped_files += 1
            continue
        file_size = int(file_path.stat().st_size)
        if not dry_run:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(file_path), str(destination_path))
        if destination_exists:
            updated_files += 1
        else:
            copied_files += 1
        transferred_bytes += file_size

    return SyncSummary(
        source_root=source,
        destination_root=destination_root,
        scanned_files=scanned_files,
        copied_files=copied_files,
        updated_files=updated_files,
        skipped_files=skipped_files,
        transferred_bytes=transferred_bytes,
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
    print(f"Scanned files: {summary.scanned_files}")
    print(f"New copies: {summary.copied_files}")
    print(f"Updated copies: {summary.updated_files}")
    print(f"Skipped files: {summary.skipped_files}")
    print(f"Transferred bytes: {summary.transferred_bytes}")
    if args.dry_run:
        print("Dry run only: no files were copied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
