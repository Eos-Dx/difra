import os
import sys
import time
import uuid
import asyncio
from pathlib import Path

import numpy as np

SRC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from difra.grpc_server.difra_service_state import DifraServiceState
from difra.grpc_server.server import hub_pb2


class _SleepyDetector:
    def __init__(self, alias: str):
        self.alias = alias
        self.size = (16, 16)

    def capture_point(self, Nframes, Nseconds, filename_base):
        time.sleep(float(Nseconds) * max(int(Nframes), 1))
        data = np.ones((8, 8), dtype=np.float64)
        np.savetxt(str(filename_base) + ".txt", data)
        return True


def _dummy_config(base_folder: Path):
    return {
        "DEV": True,
        "measurements_folder": str(base_folder),
        "technical_folder": str(base_folder / "technical"),
        "detectors": [],
        "dev_active_detectors": [],
        "active_detectors": [],
        "translation_stages": [],
        "dev_active_stages": [],
        "active_translation_stages": [],
    }


def test_capture_detectors_runs_in_parallel(tmp_path: Path):
    state = DifraServiceState(config=_dummy_config(tmp_path))
    state.detector_controllers = {
        "A": _SleepyDetector("A"),
        "B": _SleepyDetector("B"),
    }

    run_id = str(uuid.uuid4())
    t0 = time.perf_counter()
    outputs = state._capture_detectors(run_id=run_id, exposure_time_ms=800)
    elapsed = time.perf_counter() - t0

    assert set(outputs.keys()) == {"A", "B"}
    assert Path(outputs["A"]).exists()
    assert Path(outputs["B"]).exists()
    # Sequential would be ~1.6s; parallel should stay near single-detector time.
    assert elapsed < 1.35


def test_emit_system_event_logs_queue_overflow(tmp_path: Path, caplog):
    state = DifraServiceState(config=_dummy_config(tmp_path))
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait(hub_pb2.SystemEvent())
    state._run_stream_subscribers.append(queue)

    caplog.set_level("WARNING")
    asyncio.run(state.emit_system_event(hub_pb2.SystemEvent()))

    assert state._dropped_telemetry_counts["run_events"] == 1
    assert "Telemetry queue overflow on run_events stream" in caplog.text


def test_telemetry_overflow_is_mirrored_to_container_runtime_logs(tmp_path: Path):
    class _Writer:
        def __init__(self):
            self.calls = []

        def append_runtime_log(
            self,
            *,
            file_path,
            message,
            level="INFO",
            event_type="event",
            source="difra",
            timestamp=None,
            details=None,
        ):
            self.calls.append(
                {
                    "file_path": str(file_path),
                    "message": str(message),
                    "level": str(level),
                    "event_type": str(event_type),
                    "source": str(source),
                    "details": dict(details or {}),
                }
            )

    class _ContainerManager:
        @staticmethod
        def is_container_locked(_path):
            return False

    measurements_dir = tmp_path / "measurements"
    technical_dir = tmp_path / "technical"
    measurements_dir.mkdir(parents=True, exist_ok=True)
    technical_dir.mkdir(parents=True, exist_ok=True)

    session_path = measurements_dir / "session_20260101_000001.nxs.h5"
    technical_path = technical_dir / "technical_20260101_000001.nxs.h5"
    session_path.write_bytes(b"")
    technical_path.write_bytes(b"")

    state = DifraServiceState(
        config={
            **_dummy_config(tmp_path),
            "measurements_folder": str(measurements_dir),
            "technical_folder": str(technical_dir),
        }
    )
    writer = _Writer()
    state._runtime_log_bridge_ready = True
    state._runtime_log_writer = writer
    state._runtime_log_container_manager = _ContainerManager()

    state._append_telemetry_drop_to_container_logs(
        "run_events",
        3,
        1,
        "Telemetry queue overflow test message",
    )

    targets = {call["file_path"] for call in writer.calls}
    assert str(session_path) in targets
    assert str(technical_path) in targets
    assert all(call["level"] == "WARNING" for call in writer.calls)
    assert all(call["event_type"] == "telemetry_queue_overflow" for call in writer.calls)
