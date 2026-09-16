import json
import time

import pytest

from kinetic_agents.observability.rollout import LiveRollout
from kinetic_agents.observability.transcript import LiveTranscript


def make(tmp_path):
    base = tmp_path / "native/codex/sessions"
    base.mkdir(parents=True)
    path = base / "own.jsonl"
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "own"}})
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "input": "OLD_HISTORY"},
            }
        )
        + "\n"
    )
    return base, path


def test_new_bytes_only_live_before_close_private_filtered_and_partial_retained(tmp_path):
    base, path = make(tmp_path)
    capture = LiveTranscript(tmp_path)
    tail = LiveRollout(path, base, "own", capture)
    public = {
        "type": "response_item",
        "timestamp": "original-clock",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "new",
            "input": "NEW_IO",
        },
    }
    try:
        with path.open("a") as stream:
            stream.write(
                json.dumps(
                    {"type": "response_item", "payload": {"type": "reasoning", "text": "PRIVATE"}}
                )
                + "\n"
            )
            stream.write(json.dumps(public)[:-1])
            stream.flush()
        time.sleep(0.15)
        assert "NEW_IO" not in (tmp_path / "transcript.jsonl").read_text()
        with path.open("a") as stream:
            stream.write("}\n")
            stream.flush()
        deadline = time.monotonic() + 2
        while (
            "NEW_IO" not in (tmp_path / "transcript.jsonl").read_text()
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        text = (tmp_path / "transcript.jsonl").read_text()
        assert "NEW_IO" in text and "OLD_HISTORY" not in text and "PRIVATE" not in text
        events = [json.loads(line) for line in text.splitlines()]
        payload = next(e["payload"]["params"] for e in events if e["kind"] == "native_tool_io")
        assert payload["byte_offset"] > 0 and payload["source_timestamp"] == "original-clock"
        assert payload["turnId"] is None  # Never fabricate an absent turn ID.
    finally:
        tail.close()
        capture.close()


def test_foreign_path_identity_and_link_rejected(tmp_path):
    base, path = make(tmp_path)
    capture = LiveTranscript(tmp_path)
    try:
        with pytest.raises(PermissionError):
            LiveRollout(path, base, "other", capture)
        with pytest.raises(PermissionError):
            LiveRollout(tmp_path / "elsewhere", base, "own", capture)
        link = base / "linked.jsonl"
        link.symlink_to(path)
        with pytest.raises(PermissionError):
            LiveRollout(link, base, "own", capture)
    finally:
        capture.close()


def test_bad_json_and_unfinished_last_record_are_gaps_not_invented_io(tmp_path):
    base, path = make(tmp_path)
    capture = LiveTranscript(tmp_path)
    tail = LiveRollout(path, base, "own", capture)
    with path.open("a") as stream:
        stream.write('NOT_JSON\n{"partial":')
    tail.close()
    capture.close()
    events = [json.loads(line) for line in (tmp_path / "transcript.jsonl").read_text().splitlines()]
    assert sum(e["kind"] == "capture_gap" for e in events) == 2
    assert not any(e["kind"] == "native_tool_io" for e in events)
