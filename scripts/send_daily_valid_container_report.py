#!/usr/bin/env python3
"""Send daily valid DiFRA container plot report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from difra.gui.daily_valid_container_reporter import (  # noqa: E402
    run_daily_report_from_config,
    run_keychain_setup_self_test,
    send_simple_test_email,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and optionally email daily DiFRA valid-container PNG report ZIP."
    )
    parser.add_argument("--config", type=Path, default=None, help="JSON config path.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Report output folder.")
    parser.add_argument("--since-days", type=float, default=1.0, help="Scan containers modified in last N days.")
    parser.add_argument("--send", action="store_true", help="Send report email.")
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="Send a test email with a ZIP containing two simple PNG plots.",
    )
    parser.add_argument(
        "--setup-email-password",
        action="store_true",
        help="If SMTP password is missing, prompt for Ulster password and seed macOS Keychain.",
    )
    parser.add_argument(
        "--self-test-keychain-setup",
        action="store_true",
        help="Dry-run the Ulster-password decrypt and macOS Keychain write/read/delete flow without sending email.",
    )
    args = parser.parse_args(argv)
    allow_interactive_setup = bool(
        args.setup_email_password or ((args.send or args.send_test) and sys.stdin.isatty())
    )

    if args.self_test_keychain_setup:
        result = run_keychain_setup_self_test(config_path=args.config)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.send_test:
        result = send_simple_test_email(
            config_path=args.config,
            output_dir=args.output_dir,
            allow_interactive_setup=allow_interactive_setup,
        )
        print(json.dumps(result, indent=2))
        return 0 if result.get("sent") or result.get("skipped") else 1

    result = run_daily_report_from_config(
        config_path=args.config,
        output_dir=args.output_dir,
        since_days=args.since_days,
        send_email=args.send,
        allow_interactive_setup=allow_interactive_setup,
    )
    payload = {
        "scanned": result.scanned,
        "validContainers": result.valid_containers,
        "imageCount": len(result.images),
        "zipPath": str(result.zip_path or ""),
        "email": result.email_result,
        "skippedCount": len(result.skipped),
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
