#!/usr/bin/env python3
"""Regenerate DiFRA gRPC stubs from the installed hardware.protocol package."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _ensure_init_files(base: Path, rel_pkg_path: Path) -> None:
    current = base
    for part in rel_pkg_path.parts:
        current = current / part
        current.mkdir(parents=True, exist_ok=True)
        init_file = current / "__init__.py"
        if not init_file.exists():
            init_file.write_text("")


def _run_protoc(proto_file: Path, include_dirs: list[Path], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        *(f"-I{inc}" for inc in include_dirs),
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
        str(proto_file),
    ]
    subprocess.run(cmd, check=True)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[4]

    try:
        from hardware.protocol import package_root
    except Exception as exc:
        print(
            "hardware.protocol is required before regenerating stubs. "
            "Install eosdx-protocol first.",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return 2

    protocol_root = package_root()
    proto_file = protocol_root / "hub" / "v1" / "hub.proto"
    if not proto_file.exists():
        print(f"Protocol file not found: {proto_file}", file=sys.stderr)
        return 3

    try:
        import grpc_tools
    except Exception as exc:
        print(f"grpc_tools is required to generate stubs: {exc}", file=sys.stderr)
        return 4
    grpc_include = Path(grpc_tools.__file__).resolve().parent / "_proto"

    include_dirs = [protocol_root, grpc_include]
    difra_out = repo_root / "src" / "hardware" / "difra" / "grpc_server" / "generated"
    _run_protoc(proto_file, include_dirs, difra_out)
    _ensure_init_files(difra_out, Path("hub/v1"))
    print(f"Generated DiFRA gRPC stubs from {proto_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
