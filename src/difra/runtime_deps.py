"""Runtime bootstrap helpers for standalone DiFRA deployments."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from difra._local_dependency_aliases import ensure_local_dependency


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


_GITHUB_BRANCH_ARCHIVE_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/archive/refs/heads/(?P<branch>[^/]+)\.zip$"
)


def _runtime_state_path() -> Path:
    cache_root = os.environ.get("XDG_CACHE_HOME")
    if cache_root:
        base = Path(cache_root)
    else:
        base = Path.home() / ".cache"
    return base / "difra" / "runtime_deps_state.json"


def _load_runtime_state() -> dict:
    state_path = _runtime_state_path()
    if not state_path.is_file():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_runtime_state(state: dict) -> None:
    state_path = _runtime_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _parse_github_branch_archive(pip_spec: str):
    match = _GITHUB_BRANCH_ARCHIVE_RE.match(str(pip_spec or "").strip())
    if not match:
        return None
    return (
        match.group("owner"),
        match.group("repo"),
        match.group("branch"),
    )


def _fetch_github_branch_sha(owner: str, repo: str, branch: str) -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{branch}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "difra-runtime-deps",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    except Exception:
        return None

    obj = payload.get("object", {})
    sha = str(obj.get("sha") or "").strip()
    return sha or None


def _resolve_branch_sha(dep: RuntimeDependency) -> str | None:
    parsed = _parse_github_branch_archive(dep.pip_spec)
    if not parsed:
        return None
    owner, repo, branch = parsed
    return _fetch_github_branch_sha(owner, repo, branch)


def _can_skip_refresh(dep: RuntimeDependency, resolved_sha: str | None) -> bool:
    if not _import_available(dep.import_name):
        return False

    state = _load_runtime_state()
    dep_state = state.get(dep.name, {})
    if not isinstance(dep_state, dict):
        return False
    same_spec = str(dep_state.get("pip_spec") or "").strip() == dep.pip_spec
    if not same_spec:
        return False

    if _parse_github_branch_archive(dep.pip_spec):
        if not resolved_sha:
            return False
        return str(dep_state.get("resolved_sha") or "").strip() == resolved_sha

    return True


def _record_resolved_state(dep: RuntimeDependency, resolved_sha: str | None) -> None:
    state = _load_runtime_state()
    payload = {
        "pip_spec": dep.pip_spec,
    }
    if resolved_sha:
        payload["resolved_sha"] = str(resolved_sha)
    state[str(dep.name)] = payload
    _save_runtime_state(state)


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
    "xrdanalysis": RuntimeDependency(
        name="xrdanalysis",
        import_name="xrdanalysis",
        env_var="DIFRA_XRDANALYSIS_PIP_SPEC",
        default_spec="https://github.com/Eos-Dx/xrd-analysis/releases/download/v0.2/xrdanalysis-0.2.0-py3-none-any.whl",
    ),
}


def _import_available(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ModuleNotFoundError as exc:
        if exc.name != import_name:
            return False
    except Exception:
        return False

    try:
        return ensure_local_dependency(import_name)
    except Exception:
        return False


def ensure_dependency(name: str, *, python_executable: str | None = None) -> bool:
    dep = DEPENDENCIES.get(str(name))
    if dep is None:
        raise KeyError(f"Unknown runtime dependency: {name}")

    resolved_sha = _resolve_branch_sha(dep)
    if _can_skip_refresh(dep, resolved_sha):
        detail = f" ({resolved_sha[:12]})" if resolved_sha else ""
        print(
            f"[INFO] Runtime dependency unchanged; skipping refresh: "
            f"{dep.import_name}{detail}"
        )
        return False

    if resolved_sha is None and _parse_github_branch_archive(dep.pip_spec) and _import_available(dep.import_name):
        print(
            "[WARN] Could not verify remote branch tip; keeping existing runtime "
            f"dependency without re-download: {dep.import_name}"
        )
        return False

    python_exe = python_executable or sys.executable
    cmd = [
        python_exe,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--no-deps",
        "--upgrade",
        "--force-reinstall",
        dep.pip_spec,
    ]
    print(f"[INFO] Refreshing runtime dependency from source: {dep.import_name}")
    print(f"[INFO] pip source: {dep.pip_spec}")
    if resolved_sha:
        print(f"[INFO] remote branch tip: {resolved_sha}")
    subprocess.run(cmd, check=True)
    importlib.invalidate_caches()

    if not _import_available(dep.import_name):
        raise RuntimeError(
            f"Dependency installation completed but import still fails: {dep.import_name}"
        )

    _record_resolved_state(dep, resolved_sha)
    print(f"[INFO] Runtime dependency ready: {dep.import_name}")
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
        help="Dependency key to require (default: container + protocol + xrdanalysis).",
    )
    args = parser.parse_args(argv)

    required = args.required or ["container", "protocol", "xrdanalysis"]
    ensure_dependencies(required)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
