from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping, MutableMapping


_PROBE_CODE = """
from difra.gui.qt_compat import QT_API, QtWidgets
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
app.processEvents()
print(QT_API)
"""


@dataclass(frozen=True)
class QtPreflightResult:
    selected: str
    fallback: bool
    reason: str = ""


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _probe_env(
    base_env: Mapping[str, str],
    src_root: str | Path | None,
) -> dict[str, str]:
    env = dict(base_env)
    env["DIFRA_QT_API"] = "pyqt6"
    env["DIFRA_QT_PREFLIGHT_CHILD"] = "1"
    if src_root is not None:
        existing_pythonpath = env.get("PYTHONPATH", "")
        src_text = str(src_root)
        env["PYTHONPATH"] = (
            src_text
            if not existing_pythonpath
            else src_text + os.pathsep + existing_pythonpath
        )
    if sys.platform == "win32":
        conda_lib_bin = Path(sys.executable).resolve().parent / "Library" / "bin"
        if conda_lib_bin.exists():
            path_text = str(conda_lib_bin)
            existing_path = env.get("PATH", "")
            parts = existing_path.split(os.pathsep) if existing_path else []
            if path_text not in parts:
                env["PATH"] = path_text + (
                    os.pathsep + existing_path if existing_path else ""
                )
    return env


def ensure_qt_api_for_gui(
    *,
    src_root: str | Path | None = None,
    env: MutableMapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    timeout_s: float | None = None,
) -> QtPreflightResult:
    """Probe PyQt6 QApplication startup before importing Qt in the GUI process."""
    target_env = os.environ if env is None else env
    requested = str(target_env.get("DIFRA_QT_API", "auto") or "auto").strip().lower()
    if requested not in {"", "auto"}:
        return QtPreflightResult(selected=requested, fallback=False)
    if _truthy(target_env.get("DIFRA_SKIP_QT_PREFLIGHT")):
        return QtPreflightResult(selected="auto", fallback=False, reason="skipped")

    run = subprocess.run if runner is None else runner
    timeout = timeout_s
    if timeout is None:
        try:
            timeout = float(target_env.get("DIFRA_QT_PREFLIGHT_TIMEOUT", "8"))
        except ValueError:
            timeout = 8.0

    try:
        completed = run(
            [sys.executable, "-c", _PROBE_CODE],
            env=_probe_env(target_env, src_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        target_env["DIFRA_QT_API"] = "pyqt5"
        reason = f"PyQt6 QApplication probe timed out after {exc.timeout}s"
        target_env["DIFRA_QT_PREFLIGHT_FALLBACK_REASON"] = reason
        return QtPreflightResult(selected="pyqt5", fallback=True, reason=reason)
    except (OSError, subprocess.SubprocessError) as exc:
        target_env["DIFRA_QT_API"] = "pyqt5"
        reason = f"PyQt6 QApplication probe failed: {type(exc).__name__}: {exc}"
        target_env["DIFRA_QT_PREFLIGHT_FALLBACK_REASON"] = reason
        return QtPreflightResult(selected="pyqt5", fallback=True, reason=reason)

    if completed.returncode == 0:
        return QtPreflightResult(selected="pyqt6", fallback=False)

    stderr = str(completed.stderr or "").strip()
    stdout = str(completed.stdout or "").strip()
    details = stderr or stdout or f"exit code {completed.returncode}"
    if len(details) > 500:
        details = details[:497] + "..."
    target_env["DIFRA_QT_API"] = "pyqt5"
    reason = f"PyQt6 QApplication probe failed: {details}"
    target_env["DIFRA_QT_PREFLIGHT_FALLBACK_REASON"] = reason
    return QtPreflightResult(selected="pyqt5", fallback=True, reason=reason)
