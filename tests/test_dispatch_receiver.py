"""Real pipes, bounded queues, progress floods and no external models."""

import json
import os
import threading
import time

import pytest

from kinetic_agents.native.dispatch import DispatchReceiver
from dispatch_fixture import notification_probe


@pytest.fixture
def transport():
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    streams = [os.fdopen(stdout_r, "rb", buffering=0), os.fdopen(stderr_r, "rb", buffering=0)]
    receiver = DispatchReceiver(*streams, max_messages=2)
    yield receiver, stdout_w, stderr_w
    receiver.close()
    for stream in streams:
        stream.close()
    for descriptor in (stdout_w, stderr_w):
        try:
            os.close(descriptor)
        except OSError:
            pass


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("test condition did not become true")
        threading.Event().wait(0.005)


def test_real_fifty_thousand_notification_replay():
    report = notification_probe()
    assert report["status"] == "PASS", report
    assert report["receiver"]["received_messages"] == 50701
    assert report["receipt_to_dispatch_p99_seconds"] < 1.0


def test_receive_continues_while_consumer_is_blocked_and_queue_is_bounded(transport):
    receiver, output, _ = transport
    deltas = [{"method": "item/reasoning/textDelta", "params": {"delta": "x"}} for _ in range(2000)]
    controls = [{"id": i, "method": "item/tool/call"} for i in range(20)]
    wire = b"".join(json.dumps(m).encode() + b"\n" for m in deltas + controls)
    worker = threading.Thread(target=lambda: os.write(output, wire), daemon=True)
    worker.start()
    # No calls to receive: represents a main thread blocked in a DB/backend call.
    wait_until(lambda: receiver.metrics()["queued_messages"] == 2)
    assert receiver.metrics()["display_deltas"] == 2000
    assert receiver.metrics()["peak_messages"] == 2
    values = [receiver.receive(time.monotonic() + 3) for _ in controls]
    assert values == controls
    worker.join(3)
    assert not worker.is_alive()


def test_only_display_notifications_drop_ids_errors_compaction_and_unknown_are_preserved(transport):
    receiver, output, _ = transport
    controls = [
        {"method": "item/agentMessage/delta", "id": 99, "params": {}},
        {"method": "error", "params": {"error": {"code": "synthetic"}}},
        {"method": "item/completed", "params": {"item": {"type": "contextCompaction"}}},
        {"method": "unknown/futureControl", "params": {}},
    ]
    os.write(output, b"".join(json.dumps(m).encode() + b"\n" for m in controls))
    assert [receiver.receive(time.monotonic() + 2) for _ in controls] == controls
    assert receiver.metrics()["display_deltas"] == 0


def test_partial_frame_survives_observation_timeout_and_eof_follows_last_response(transport):
    receiver, output, _ = transport
    os.write(output, b'{"id":1,')
    with pytest.raises(TimeoutError):
        receiver.receive(time.monotonic() + 0.03)
    os.write(output, b'"result":{"ok":true}}\n')
    os.close(output)
    assert receiver.receive(time.monotonic() + 2) == {"id": 1, "result": {"ok": True}}
    with pytest.raises(EOFError):
        receiver.receive(time.monotonic() + 2)


def test_malformed_input_never_becomes_tool_success(transport):
    receiver, output, _ = transport
    os.write(output, b"NOT_JSON\n")
    with pytest.raises(ValueError):
        receiver.receive(time.monotonic() + 2)
