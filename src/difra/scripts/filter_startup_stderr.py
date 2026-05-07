#!/usr/bin/env python3
"""Run a child process while suppressing known noisy startup stderr lines."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading


SUPPRESSED_PATTERNS = (
    re.compile(r"^WARNING: All log messages before absl::InitializeLog\(\)"),
    re.compile(r"^E\d+\s+\d+:\d+:\d+\.\d+\s+\d+\s+instrument\.cc:\d+\] Metric with name 'grpc\.resource_quota\."),
)


def should_suppress(line: str) -> bool:
    text = str(line or "").rstrip("\r\n")
    return any(pattern.search(text) for pattern in SUPPRESSED_PATTERNS)


def _relay(pipe, target, *, filter_lines: bool) -> None:
    try:
        for raw in iter(pipe.readline, b""):
            try:
                text = raw.decode(errors="replace")
            except Exception:
                text = str(raw)
            if filter_lines and should_suppress(text):
                continue
            target.write(text)
            target.flush()
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("No command provided.", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    stdout_thread = threading.Thread(
        target=_relay,
        args=(proc.stdout, sys.stdout),
        kwargs={"filter_lines": False},
    )
    stderr_thread = threading.Thread(
        target=_relay,
        args=(proc.stderr, sys.stderr),
        kwargs={"filter_lines": True},
    )
    stdout_thread.daemon = True
    stderr_thread.daemon = True
    stdout_thread.start()
    stderr_thread.start()

    try:
        return_code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return_code = proc.wait()

    stdout_thread.join(timeout=2.0)
    stderr_thread.join(timeout=2.0)
    return int(return_code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
