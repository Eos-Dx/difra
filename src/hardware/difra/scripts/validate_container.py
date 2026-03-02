#!/usr/bin/env python3
"""Validate DIFRA containers using schema_version from container metadata."""

import argparse
import json
import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hardware.difra.utils.container_validation import (  # noqa: E402
    format_report,
    validate_container,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate DIFRA HDF5/NeXus containers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s ./session_abc.nxs.h5
  %(prog)s ./technical_xyz.h5 --kind technical
  %(prog)s ./container.h5 --json

Exit codes:
  0 - Valid container
  1 - Invalid container (validation errors)
  2 - Usage or runtime error
        """,
    )
    parser.add_argument("file", help="Path to the HDF5 container")
    parser.add_argument(
        "--kind",
        default="auto",
        choices=["auto", "session", "technical"],
        help="Container kind to validate",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a text report",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress detailed output; emit only pass/fail",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    try:
        report = validate_container(
            file_path=args.file,
            container_kind=args.kind,
        )
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            "file": report.file_path,
            "schema_version": report.schema_version,
            "container_kind": report.container_kind,
            "is_valid": report.is_valid,
            "messages": [
                {
                    "severity": item.severity,
                    "path": item.path,
                    "message": item.message,
                }
                for item in report.messages
            ],
        }
        print(json.dumps(payload, indent=2))
    elif args.quiet:
        print(
            f"{'VALID' if report.is_valid else 'INVALID'} "
            f"{report.container_kind} v{report.schema_version}: {report.file_path}"
        )
    else:
        print(format_report(report))

    return 0 if report.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
