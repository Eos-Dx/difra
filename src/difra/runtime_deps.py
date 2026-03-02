"""Runtime bootstrap helpers for standalone DiFRA deployments."""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class RuntimeDependency:
    name: str
    import_name: str
    env_var: str
    default_spec: str

    @property
    def pip_spec(self) -> str:
        override = str(os.environ.get(self.env_var, "")).strip()
        if override:
            return override
        return self.default_spec


DEPENDENCIES = {
    "container": RuntimeDependency(
        name="container",
        import_name="container",
        env_var="DIFRA_CONTAINER_PIP_SPEC",
        default_spec="https://github.com/Eos-Dx/container/archive/refs/heads/main.zip",
    ),
    "protocol": RuntimeDependency(
        name="protocol",
        import_name="protocol",
        env_var="DIFRA_PROTOCOL_PIP_SPEC",
        default_spec="https://github.com/Eos-Dx/protocol/archive/refs/heads/main.zip",
    ),
}


def _import_available(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except Exception:
        return False


def ensure_dependency(name: str, *, python_executable: str | None = None) -> bool:
    dep = DEPENDENCIES.get(str(name))
    if dep is None:
        raise KeyError(f"Unknown runtime dependency: {name}")

    if _import_available(dep.import_name):
        print(f"[INFO] Runtime dependency already available: {dep.import_name}")
        return False

    python_exe = python_executable or sys.executable
    cmd = [
        python_exe,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--upgrade",
        dep.pip_spec,
    ]
    print(f"[INFO] Installing missing runtime dependency: {dep.import_name}")
    print(f"[INFO] pip source: {dep.pip_spec}")
    subprocess.run(cmd, check=True)
    importlib.invalidate_caches()

    if not _import_available(dep.import_name):
        raise RuntimeError(
            f"Dependency installation completed but import still fails: {dep.import_name}"
        )

    print(f"[INFO] Installed runtime dependency: {dep.import_name}")
    return True


def ensure_dependencies(
    names: Iterable[str],
    *,
    python_executable: str | None = None,
) -> List[str]:
    installed = []
    for name in names:
        if ensure_dependency(name, python_executable=python_executable):
            installed.append(str(name))
    return installed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ensure standalone DiFRA runtime dependencies are installed."
    )
    parser.add_argument(
        "--require",
        dest="required",
        action="append",
        choices=sorted(DEPENDENCIES.keys()),
        help="Dependency key to require (default: container + protocol).",
    )
    args = parser.parse_args(argv)

    required = args.required or ["container", "protocol"]
    ensure_dependencies(required)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
