import json
import os
import threading
import time

import pytest

from kinetic_agents.native.dispatch import DispatchReceiver
from kinetic_agents.observability.transcript import LiveTranscript


def rows(root):
    return [json.loads(line) for line in (root / "transcript.jsonl").read_text().splitlines()]


def test_records_are_visible_immediately_without_close_or_review(tmp_path):
    log = LiveTranscript(tmp_path)
    log.outgoing({"id": 1, "method": "turn/start", "params": {"input": [{"text": "原任务"}]}})
    log.incoming({"id": 1, "result": {"turn": {"id": "t1"}}})
    params = {
        "threadId": "s1",
        "turnId": "t1",
        "callId": "c1",
        "tool": "research_compute",
        "arguments": {"script": 'print("你好")', "search": "USC II reduction"},
    }
    log.incoming({"id": 10, "method": "item/tool/call", "params": params})
    assert rows(tmp_path)[-1]["payload"]["params"] == params
    log.outgoing({"id": 10, "result": {"success": False, "contentItems": [{"text": "实际错误"}]}})
    assert rows(tmp_path)[-1]["kind"] == "tool_return"
    text = (tmp_path / "transcript.md").read_text()
    assert "USC II reduction" in text and "实际错误" in text and "原任务" in text
    log.close()


def test_auth_and_private_reasoning_never_serialized(tmp_path):
    log = LiveTranscript(tmp_path)
    log.outgoing({"id": 1, "method": "account/login/start", "params": {"accessToken": "SECRET_A"}})
    log.incoming({"id": 1, "result": {"refresh_token": "SECRET_B"}})
    log.incoming(
        {"id": 2, "method": "account/chatgptAuthTokens/refresh", "params": {"secret": "SECRET_C"}}
    )
    log.outgoing({"id": 2, "result": {"accessToken": "SECRET_D"}})
    log.incoming({"method": "item/reasoning/textDelta", "params": {"delta": "PRIVATE_A"}})
    log.incoming(
        {"method": "item/completed", "params": {"item": {"type": "reasoning", "text": "PRIVATE_B"}}}
    )
    log.incoming(
        {
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "phase": "analysis", "text": "PRIVATE_C"}},
        }
    )
    log.incoming({"method": "codex/event/raw_response_item", "params": {"text": "PRIVATE_D"}})
    log.incoming(
        {
            "method": "rawResponseItem/completed",
            "params": {"item": {"type": "reasoning", "text": "PRIVATE_RAW"}},
        }
    )
    log.outgoing({"id": 3, "method": "thread/resume", "params": {"threadId": "own"}})
    log.incoming(
        {"id": 3, "result": {"thread": {"id": "own", "turns": [{"reasoning": "PRIVATE_E"}]}}}
    )
    log.record(
        "synthetic", {"access_token": "SECRET_E", "text": "Bearer SECRET_F", "api_key": "SECRET_G"}
    )
    log.close()
    for name in ("transcript.md", "transcript.jsonl"):
        text = (tmp_path / name).read_text()
        assert "SECRET_" not in text and "PRIVATE_" not in text


def test_partial_previous_jsonl_preserved_and_next_events_are_separated(tmp_path):
    (tmp_path / "transcript.jsonl").write_bytes(b'{"interrupted":')
    log = LiveTranscript(tmp_path)
    log.close()
    lines = (tmp_path / "transcript.jsonl").read_text().splitlines()
    assert lines[0] == '{"interrupted":'
    assert json.loads(lines[1])["kind"] == "capture_gap"
    assert json.loads(lines[-1])["kind"] == "capture_closed"


def test_unknown_fields_truncation_and_missing_returns_are_explicit(tmp_path):
    log = LiveTranscript(tmp_path, max_event_bytes=600)
    log.incoming(
        {"id": 7, "method": "item/tool/call", "params": {"callId": "pending", "tool": "solve"}}
    )
    log.incoming(
        {
            "method": "item/started",
            "params": {"threadId": "s", "turnId": "t", "item": {"type": "agentMessage", "id": "i"}},
        }
    )
    log.incoming(
        {
            "method": "item/completed",
            "params": {"item": {"type": "futurePrivateType", "text": "do-not-guess"}},
        }
    )
    log.record("large", {"text": "x" * 2000})
    log.close()
    data = rows(tmp_path)
    assert data[-2]["capture_truncated"]
    assert data[-2]["payload"]["original_sanitized_bytes"] > 2000
    assert data[-1]["payload"]["unreturned_tools"][0]["callId"] == "pending"
    assert data[-1]["payload"]["unfinished_items"] == [["s", "t", "i"]]
    assert "do-not-guess" not in (tmp_path / "transcript.jsonl").read_text()


def test_no_overwrite_restart_and_markdown_fence(tmp_path):
    first = LiveTranscript(tmp_path)
    first.record("one", {"text": "```````"})
    first.close()
    before = (tmp_path / "transcript.jsonl").read_bytes()
    second = LiveTranscript(tmp_path)
    second.close()
    assert (tmp_path / "transcript.jsonl").read_bytes().startswith(before)
    assert first.stream != second.stream
    assert "````````json" in (tmp_path / "transcript.md").read_text()


def test_symlinks_and_hardlinks_rejected(tmp_path):
    other = tmp_path / "other"
    other.write_text("untouched")
    run = tmp_path / "run"
    run.mkdir()
    (run / "transcript.jsonl").symlink_to(other)
    with pytest.raises(PermissionError):
        LiveTranscript(run)
    (run / "transcript.jsonl").unlink()
    os.link(other, run / "transcript.jsonl")
    with pytest.raises(PermissionError):
        LiveTranscript(run)
    assert other.read_text() == "untouched"


def test_capture_before_discard_even_when_dispatch_is_blocked(tmp_path):
    log = LiveTranscript(tmp_path)
    r, w = os.pipe()
    er, ew = os.pipe()
    streams = [os.fdopen(r, "rb", buffering=0), os.fdopen(er, "rb", buffering=0)]
    receiver = DispatchReceiver(*streams, observer=log.incoming)
    events = [
        {"method": "item/agentMessage/delta", "params": {"delta": "半句也保留"}},
        {"method": "item/reasoning/textDelta", "params": {"delta": "PRIVATE_A"}},
        {"id": 8, "method": "item/tool/call", "params": {"callId": "tool_8", "tool": "solve"}},
    ]
    try:
        os.write(w, b"".join(json.dumps(e).encode() + b"\n" for e in events))
        deadline = time.monotonic() + 3
        while receiver.metrics()["queued_messages"] != 1 and time.monotonic() < deadline:
            threading.Event().wait(0.005)
        text = (tmp_path / "transcript.jsonl").read_text()
        assert "半句也保留" in text and "tool_8" in text and "PRIVATE_A" not in text
        assert receiver.metrics()["display_deltas"] == 2
        assert receiver.receive(time.monotonic() + 1) == events[-1]
    finally:
        receiver.close()
        log.close()
        for s in streams:
            s.close()
        os.close(w)
        os.close(ew)


def test_capture_flood_has_no_database_or_hash_dependency(tmp_path, monkeypatch):
    import hashlib
    import sqlite3

    def forbidden(*args, **kwargs):
        raise AssertionError("per-I/O DB/hash is forbidden")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(hashlib, "sha256", forbidden)
    log = LiveTranscript(tmp_path)
    started = time.monotonic()
    for i in range(3000):
        log.incoming(
            {"method": "item/agentMessage/delta", "params": {"itemId": "one", "delta": str(i)}}
        )
    log.incoming(
        {"id": 1, "method": "item/tool/call", "params": {"callId": "last", "tool": "solve"}}
    )
    assert rows(tmp_path)[-1]["payload"]["params"]["callId"] == "last"
    assert time.monotonic() - started < 5  # Large CI margin; report measured cost separately.
    log.close()
