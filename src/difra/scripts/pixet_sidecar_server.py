#!/usr/bin/env python3
"""PIXet sidecar server (Python 3.12+) using plain TCP sockets + JSON lines.

Development note:
- Ulster Windows sidecar runtime is `eosdx_pixet` with Python 3.12 x64.
- Default PIXet backend uses the ctypes bridge to Advacam PIXet 1.8.5.
- Set PIXET_SIDECAR_BACKEND=pypixet only for explicit legacy fallback.

Protocol (request/response):
- Request:  {"id": "...", "cmd": "...", "args": {...}}
- Response: {"id": "...", "ok": true,  "result": ...}
            {"id": "...", "ok": false, "error": "..."}
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import socketserver
import tempfile
import threading
import sys
from pathlib import Path
from typing import Any, Dict

import numpy as np

# Allow running directly from repository root or arbitrary working directory.
REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from difra.hardware.detectors import (
    DummyDetectorController,
    PixetDetectorController,
    PixetLegacyDetectorController,
)


class SidecarState:
    def __init__(self) -> None:
        self.controllers: Dict[str, Any] = {}
        self.controller_locks: Dict[str, threading.RLock] = {}
        self.map_lock = threading.RLock()


STATE = SidecarState()
LOGGER = logging.getLogger("difra.pixet_sidecar")


def _default_sidecar_log_path() -> Path:
    explicit = os.environ.get("DIFRA_SIDECAR_LOG_PATH", "").strip()
    if explicit:
        return Path(explicit)

    if os.name == "nt":
        base = Path(
            os.environ.get("LOCALAPPDATA")
            or Path.home() / "AppData" / "Local"
        )
        return base / "DiFRA" / "logs" / "pixet_sidecar.log"

    state_root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_root / "DiFRA" / "logs" / "pixet_sidecar.log"


def _configure_sidecar_logging() -> Path:
    log_path = _default_sidecar_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_difra_pixet_sidecar", False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    file_handler._difra_pixet_sidecar = True

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    console_handler._difra_pixet_sidecar = True

    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.setLevel(logging.DEBUG)

    LOGGER.info("PIXet sidecar logging initialized", extra={"log_path": str(log_path)})
    return log_path


def _require_alias(args: Dict[str, Any]) -> str:
    alias = str(args.get("alias", "")).strip()
    if not alias:
        raise ValueError("Missing required field: alias")
    return alias


def _resolve_detector_kind(args: Dict[str, Any]) -> str:
    detector_type = str(args.get("detector_type", "")).strip()
    if not detector_type:
        cfg = args.get("config", {}) or {}
        if isinstance(cfg, dict):
            detector_type = str(cfg.get("type", "")).strip()
    detector_type = detector_type.lower()
    if detector_type in {"dummydetector", "dummy"}:
        return "dummy"

    backend = str(
        os.environ.get("PIXET_SIDECAR_BACKEND")
        or os.environ.get("PIXET_BACKEND")
        or "ctypes"
    ).strip().lower()
    if backend in {"pypixet", "legacy", "pixet_legacy"}:
        return "pixet_legacy"
    return "pixet_ctypes"


def _get_or_create_controller(args: Dict[str, Any]):
    alias = _require_alias(args)
    with STATE.map_lock:
        ctrl = STATE.controllers.get(alias)
        if ctrl is not None:
            return ctrl

        size = args.get("size", [256, 256])
        if not isinstance(size, (list, tuple)) or len(size) != 2:
            raise ValueError("size must be [width, height]")
        width = int(size[0])
        height = int(size[1])

        config = args.get("config", {}) or {}
        if not isinstance(config, dict):
            raise ValueError("config must be an object")

        kind = _resolve_detector_kind(args)
        if kind == "dummy":
            ctrl = DummyDetectorController(alias=alias, size=(width, height))
        elif kind == "pixet_legacy":
            ctrl = PixetLegacyDetectorController(
                alias=alias,
                size=(width, height),
                config=config,
            )
        else:
            ctrl = PixetDetectorController(
                alias=alias,
                size=(width, height),
                config=config,
            )
        STATE.controllers[alias] = ctrl
        STATE.controller_locks[alias] = threading.RLock()
        return ctrl


def _get_controller_lock(alias: str) -> threading.RLock:
    with STATE.map_lock:
        lock = STATE.controller_locks.get(alias)
        if lock is None:
            lock = threading.RLock()
            STATE.controller_locks[alias] = lock
        return lock


def _cleanup_temp_capture(base_path: str) -> None:
    for suffix in (".txt", ".dsc"):
        try:
            os.remove(base_path + suffix)
        except OSError:
            pass


def _dispatch(cmd: str, args: Dict[str, Any]) -> Any:
    if cmd == "ping":
        return {"status": "ok", "pid": os.getpid()}

    if cmd == "init_detector":
        ctrl = _get_or_create_controller(args)
        alias = _require_alias(args)
        with _get_controller_lock(alias):
            ok = bool(ctrl.init_detector())
            return {
                "initialized": ok,
                "size": [int(ctrl.size[0]), int(ctrl.size[1])],
            }

    if cmd == "deinit_detector":
        alias = _require_alias(args)
        with STATE.map_lock:
            ctrl = STATE.controllers.pop(alias, None)
            lock = STATE.controller_locks.pop(alias, threading.RLock())
        with lock:
            if ctrl is not None:
                ctrl.deinit_detector()
        return {"deinitialized": True}

    if cmd == "capture_point":
        ctrl = _get_or_create_controller(args)
        alias = _require_alias(args)
        nframes = max(int(args.get("Nframes", 1)), 1)
        nseconds = float(args.get("Nseconds", 0.1))
        filename_base = str(args.get("filename_base", "")).strip()
        if not filename_base:
            raise ValueError("Missing required field: filename_base")

        with _get_controller_lock(alias):
            ok = bool(
                ctrl.capture_point(
                    Nframes=nframes,
                    Nseconds=nseconds,
                    filename_base=filename_base,
                )
            )
        return {"captured": ok}

    if cmd == "capture_frame":
        ctrl = _get_or_create_controller(args)
        alias = _require_alias(args)
        exposure_s = float(args.get("exposure_s", 0.1))
        frames = max(int(args.get("frames", 1)), 1)

        # Use normal capture path and return matrix to caller for real-time preview.
        fd, tmp_txt = tempfile.mkstemp(prefix="pixet_sidecar_rt_", suffix=".txt")
        os.close(fd)
        base_path = tmp_txt[:-4]
        _cleanup_temp_capture(base_path)

        try:
            with _get_controller_lock(alias):
                ok = bool(
                    ctrl.capture_point(
                        Nframes=frames,
                        Nseconds=exposure_s,
                        filename_base=base_path,
                    )
                )
            if not ok or not os.path.exists(tmp_txt):
                return {"frame": None}
            frame = np.loadtxt(tmp_txt)
            return {"frame": frame.tolist()}
        finally:
            _cleanup_temp_capture(base_path)

    if cmd == "shutdown":
        with STATE.map_lock:
            aliases = list(STATE.controllers.keys())
        for alias in aliases:
            with STATE.map_lock:
                ctrl = STATE.controllers.pop(alias, None)
                lock = STATE.controller_locks.pop(alias, threading.RLock())
            with lock:
                if ctrl is not None:
                    ctrl.deinit_detector()
        return {"shutdown": True}

    raise ValueError("Unknown command: %s" % cmd)


class JsonLineHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            req_id = None
            try:
                req = json.loads(raw.decode("utf-8"))
                req_id = req.get("id")
                cmd = str(req.get("cmd", "")).strip()
                args = req.get("args", {}) or {}
                if not isinstance(args, dict):
                    raise ValueError("args must be an object")
                result = _dispatch(cmd, args)
                resp = {"id": req_id, "ok": True, "result": result}
            except Exception as exc:
                resp = {"id": req_id, "ok": False, "error": str(exc)}

            payload = (json.dumps(resp, ensure_ascii=True) + "\n").encode("utf-8")
            self.wfile.write(payload)
            self.wfile.flush()


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False

    # Windows does not reliably support os.kill(pid, 0) as a liveness probe.
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                int(pid),
            )
            if handle:
                exit_code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                kernel32.CloseHandle(handle)
                if ok:
                    return int(exit_code.value) == STILL_ACTIVE
                # Inconclusive check: prefer keeping sidecar alive.
                return True

            err = ctypes.get_last_error()
            # Access denied usually means process exists but is protected.
            if err == 5:
                return True
            # Invalid parameter / not found.
            if err in (87, 1168):
                return False
            # Inconclusive check: prefer keeping sidecar alive.
            return True
        except Exception:
            # Conservative fallback: do not terminate sidecar on probe failure.
            return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _start_owner_watchdog(
    server: ThreadedTCPServer,
    owner_pid: int,
    check_interval_s: float,
    stop_event: threading.Event,
) -> None:
    if owner_pid <= 0:
        return

    interval = max(float(check_interval_s), 0.2)

    def _watch_owner() -> None:
        while not stop_event.wait(interval):
            if _pid_alive(owner_pid):
                continue
            LOGGER.warning("Owner pid %s is not alive; shutting down", owner_pid)
            try:
                _dispatch("shutdown", {})
            except Exception:
                pass
            try:
                server.shutdown()
            except Exception:
                pass
            return

    thread = threading.Thread(target=_watch_owner, name="owner-watchdog", daemon=True)
    thread.start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PIXet socket sidecar server.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=51001, help="Bind port.")
    parser.add_argument(
        "--owner-pid",
        type=int,
        default=0,
        help="Optional owner PID; sidecar exits when this process is gone.",
    )
    parser.add_argument(
        "--owner-check-interval-s",
        type=float,
        default=1.0,
        help="Seconds between owner PID checks.",
    )
    return parser.parse_args()


def main() -> int:
    log_path = _configure_sidecar_logging()
    args = parse_args()
    stop_event = threading.Event()
    with ThreadedTCPServer((args.host, int(args.port)), JsonLineHandler) as server:
        LOGGER.info(
            "PIXet sidecar listening on %s:%s pid=%s log=%s",
            args.host,
            args.port,
            os.getpid(),
            log_path,
        )
        _start_owner_watchdog(
            server=server,
            owner_pid=int(args.owner_pid),
            check_interval_s=float(args.owner_check_interval_s),
            stop_event=stop_event,
        )
        try:
            server.serve_forever()
        finally:
            stop_event.set()
            try:
                _dispatch("shutdown", {})
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
