"""Synthetic OS-pipe/Codex-dispatch qualification, no provider, solver or SSH.

Uses the production receiver, native poll and dynamic request handler. Latency
is local receipt -> dispatch; not provider generation -> dispatch or science time.
"""

import argparse
import json
import math
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from kinetic_agents.native.client import NativeClient
from kinetic_agents.native.dispatch import DispatchReceiver
from kinetic_agents.core.encoding import sha


def notification_probe(*, display_count=50000, tool_count=100):
    if display_count < 50000 or tool_count < 100 or tool_count > display_count:
        raise ValueError("qualification requires at least 50000 deltas and 100 tool requests")
    messages = []
    for i in range(display_count):
        messages.append(
            {"method": "item/reasoning/textDelta", "params": {"delta": "synthetic-progress"}}
        )
        if (i + 1) * tool_count // display_count > i * tool_count // display_count:
            index = (i + 1) * tool_count // display_count - 1
            messages.append(
                {
                    "id": index,
                    "method": "item/tool/call",
                    "params": {
                        "threadId": "synthetic-thread",
                        "turnId": "synthetic-turn",
                        "callId": f"call-{index}",
                        "tool": "synthetic_read",
                        "arguments": {"index": index},
                    },
                }
            )
    # >256 control items exercise multi-poll draining AFTER process exit.
    messages.extend(
        {
            "method": "item/completed",
            "params": {"threadId": "synthetic-thread", "item": {"type": "reasoning"}},
        }
        for _ in range(600)
    )
    messages.append(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "synthetic-thread",
                "turn": {"id": "synthetic-turn", "status": "completed"},
            },
        }
    )
    wire = b"".join(json.dumps(m).encode() + b"\n" for m in messages)
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    streams = [os.fdopen(out_r, "rb", buffering=0), os.fdopen(err_r, "rb", buffering=0)]
    failures = []

    def writer():
        try:
            with os.fdopen(out_w, "wb") as output:
                output.write(wire)
        except Exception as exc:
            failures.append(type(exc).__name__)
        finally:
            os.close(err_w)

    client = NativeClient.__new__(NativeClient)
    client.receiver = DispatchReceiver(*streams)
    client.thread_id, client.turn_id = "synthetic-thread", "synthetic-turn"
    client.completed = client.usage = None
    client.calls, client.unexpected_items, client.rejected_requests = [], [], []
    received, sent, waits = [], [], []
    client.tools = {
        "synthetic_read": lambda args: received.append(args["index"]) or {"synthetic": True}
    }
    client.send = lambda message: sent.append(message["id"])
    client.dispatch_observer = lambda name, cid, timing: waits.append(timing["queue_wait_seconds"])
    client.process = SimpleNamespace(poll=lambda: 0)
    worker = threading.Thread(target=writer, daemon=True)
    started = time.monotonic()
    cpu_start = time.process_time()
    worker.start()
    try:
        while not client.completed:
            if time.monotonic() - started > 60:
                raise TimeoutError("synthetic notification workload stalled")
            client.poll()
        worker.join(2)
        metrics = client.receiver.metrics()
        ordered = sorted(waits)
        p99 = ordered[math.ceil(0.99 * len(ordered)) - 1] if ordered else None
        report = {
            "schema": "research-dispatch-probe.v1",
            "synthetic_only": True,
            "new_model_calls": 0,
            "new_solver_calls": 0,
            "display_notifications": display_count,
            "expected_tools": tool_count,
            "completed_tools": len(received),
            "tool_order_exact": received == list(range(tool_count)),
            "responses_exact": sent == list(range(tool_count)),
            "writer_errors": failures,
            "completion_after_exit_preserved": client.completed["status"] == "completed",
            "receiver": metrics,
            "receipt_to_dispatch_p99_seconds": p99,
            "receipt_to_dispatch_max_seconds": max(waits, default=None),
            "wall_seconds": time.monotonic() - started,
            "process_cpu_seconds": time.process_time() - cpu_start,
            "latency_boundary": "local frame receipt -> callback; no model generation timestamp",
            "source_sha256": {
                name: sha((Path(__file__).resolve().parents[1] / name).read_bytes())
                for name in (
                    "src/kinetic_agents/native/dispatch.py",
                    "src/kinetic_agents/native/client.py",
                    "src/kinetic_agents/native/dynamic.py",
                    "src/kinetic_agents/native/pipes.py",
                    "tests/dispatch_fixture.py",
                )
            },
        }
        report["status"] = (
            "PASS"
            if (
                report["tool_order_exact"]
                and report["responses_exact"]
                and not failures
                and not worker.is_alive()
                and metrics["display_deltas"] == display_count
                and report["completion_after_exit_preserved"]
                and p99 is not None
                and p99 < 1.0
            )
            else "FAIL"
        )
        return report
    finally:
        client.receiver.close()
        for stream in streams:
            stream.close()
        worker.join(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("do not replace qualification evidence")
    report = notification_probe()
    with args.output.open("x") as out:
        json.dump(report, out, indent=2)
    print(json.dumps(report, indent=2))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
