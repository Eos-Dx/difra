#!/usr/bin/env python3
"""Real-hardware motion stop drill over gRPC.

Run this only with a connected and initialized motion stage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Tuple

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

# Allow execution from repository root or arbitrary working directory.
REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from difra.grpc_server.server import hub_pb2, hub_pb2_grpc, load_difra_config


def _ctx(reason: str) -> hub_pb2.CommandContext:
    ts = Timestamp()
    ts.GetCurrentTime()
    return hub_pb2.CommandContext(
        command_id=str(uuid.uuid4()),
        user="motion_stop_drill",
        reason=reason,
        timestamp=ts,
        measurement_class=hub_pb2.SAMPLE,
    )


def _active_stage_limits(config: dict) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    stages = list(config.get("translation_stages", []) or [])
    active_ids = set(config.get("active_translation_stages", []) or [])
    selected = None
    for stage in stages:
        if stage.get("id") in active_ids:
            selected = stage
            break
    if selected is None and stages:
        selected = stages[0]
    if selected is None:
        raise RuntimeError("No translation stage configured")

    limits_cfg = ((selected.get("settings", {}) or {}).get("limits_mm", {}) or {})
    try:
        x = tuple(float(v) for v in limits_cfg.get("x", (-14.0, 14.0)))
        y = tuple(float(v) for v in limits_cfg.get("y", (-14.0, 14.0)))
        if len(x) != 2 or len(y) != 2:
            raise ValueError("invalid limits shape")
    except Exception as exc:
        raise RuntimeError(f"Invalid stage limits in config: {exc}") from exc
    return (x[0], x[1]), (y[0], y[1])


def _safe_axis_target(current: float, limits: Tuple[float, float], delta: float) -> float:
    lo, hi = float(limits[0]), float(limits[1])
    candidate = float(current) + float(delta)
    if lo <= candidate <= hi:
        return candidate
    candidate = float(current) - float(delta)
    if lo <= candidate <= hi:
        return candidate
    margin = 0.1
    return min(max(float(current), lo + margin), hi - margin)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run motion stop drill against DiFRA gRPC sidecar.")
    parser.add_argument("--host", default=os.environ.get("DIFRA_GRPC_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DIFRA_GRPC_PORT", "50061")))
    parser.add_argument("--axis", choices=("x", "y"), default="x")
    parser.add_argument("--delta-mm", type=float, default=4.0, help="Requested move delta before stop")
    parser.add_argument("--stop-delay-s", type=float, default=0.15, help="Delay between MoveTo and Stop")
    parser.add_argument("--move-timeout-s", type=float, default=45.0)
    parser.add_argument("--assert-partial-stop", action="store_true")
    parser.add_argument("--partial-tol-mm", type=float, default=0.05)
    parser.add_argument("--config", default=None, help="Optional setup config path for stage limits")
    return parser


async def _run(args: argparse.Namespace) -> int:
    config = load_difra_config(args.config)
    x_limits, y_limits = _active_stage_limits(config)

    target_addr = f"{args.host}:{args.port}"
    print(f"[INFO] Connecting to gRPC server: {target_addr}")
    channel = grpc.aio.insecure_channel(target_addr)
    await channel.channel_ready()
    init_stub = hub_pb2_grpc.DeviceInitializationStub(channel)
    motion_stub = hub_pb2_grpc.MotionStub(channel)
    state_stub = hub_pb2_grpc.StateMonitorStub(channel)

    init_motion = await init_stub.InitializeMotion(
        hub_pb2.InitializeMotionRequest(ctx=_ctx("motion_stop_drill_init_motion"))
    )
    if not bool(init_motion.initialized):
        raise RuntimeError("InitializeMotion failed in motion stop drill")

    before = await state_stub.GetMotionState(hub_pb2.Empty())
    start_x = float(before.position_x)
    start_y = float(before.position_y)
    axis = str(args.axis)

    if axis == "x":
        target = _safe_axis_target(start_x, x_limits, args.delta_mm)
    else:
        target = _safe_axis_target(start_y, y_limits, args.delta_mm)

    print(
        "[INFO] Drill input: "
        f"axis={axis} start=({start_x:.4f},{start_y:.4f}) target={target:.4f} "
        f"delta={float(args.delta_mm):.4f} stop_delay_s={float(args.stop_delay_s):.3f}"
    )

    move_req = hub_pb2.MoveToRequest(
        ctx=_ctx(f"motion_stop_drill_move axis:{axis}"),
        position_mm=float(target),
    )
    move_task = asyncio.create_task(
        motion_stub.MoveTo(move_req, timeout=float(args.move_timeout_s))
    )

    await asyncio.sleep(max(float(args.stop_delay_s), 0.0))
    stop_started = time.perf_counter()
    await motion_stub.Stop(hub_pb2.StopRequest(ctx=_ctx("motion_stop_drill_stop")))
    stop_latency_s = time.perf_counter() - stop_started

    move_status = "completed"
    move_error = ""
    try:
        await asyncio.wait_for(move_task, timeout=float(args.move_timeout_s))
    except asyncio.TimeoutError as exc:
        move_status = "timeout"
        move_error = str(exc)
    except grpc.aio.AioRpcError as exc:
        move_status = "rpc_error"
        move_error = f"{exc.code().name}: {exc.details()}"
    except Exception as exc:  # pragma: no cover - defensive
        move_status = "error"
        move_error = str(exc)

    after = await state_stub.GetMotionState(hub_pb2.Empty())
    final_x = float(after.position_x)
    final_y = float(after.position_y)
    final_axis = final_x if axis == "x" else final_y

    stopped_before_target = abs(final_axis - float(target)) > float(args.partial_tol_mm)
    summary = {
        "axis": axis,
        "start_x": start_x,
        "start_y": start_y,
        "target": float(target),
        "final_x": final_x,
        "final_y": final_y,
        "stop_latency_s": float(stop_latency_s),
        "move_status": move_status,
        "move_error": move_error,
        "stopped_before_target": bool(stopped_before_target),
        "partial_tol_mm": float(args.partial_tol_mm),
    }
    print(json.dumps(summary, indent=2))

    # Restore start position (best-effort) to keep machine state stable.
    try:
        await motion_stub.MoveTo(
            hub_pb2.MoveToRequest(
                ctx=_ctx("motion_stop_drill_restore axis:x"),
                position_mm=start_x,
            ),
            timeout=float(args.move_timeout_s),
        )
        await motion_stub.MoveTo(
            hub_pb2.MoveToRequest(
                ctx=_ctx("motion_stop_drill_restore axis:y"),
                position_mm=start_y,
            ),
            timeout=float(args.move_timeout_s),
        )
    except Exception as exc:
        print(f"[WARN] Failed to restore start position: {exc}")

    await channel.close()

    if args.assert_partial_stop and not stopped_before_target:
        print(
            "[ERROR] Stop drill expected partial stop but final position is at/near target."
        )
        return 2
    if move_status == "timeout":
        return 3
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
