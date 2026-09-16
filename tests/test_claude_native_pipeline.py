"""Installed Claude Code, synthetic Anthropic stream, no real account/model."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from test_harness_adapters import context
from kinetic_agents.harnesses.stream import StreamSession
from kinetic_agents.harnesses.gateway import APIGateway
from kinetic_agents.harnesses.sandbox import qualify
from kinetic_agents.connections import bind_backend
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore
from kinetic_agents.research.environment import ScientificTeamService

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NATIVE_TEAM_QUALIFICATION") != "1",
    reason="explicit loopback qualification only",
)


@pytest.mark.parametrize("members", [1, 3])
def test_installed_claude_native_mcp_resume_and_workers(members):
    calls, errors, requests = [], [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert self.headers["x-api-key"] == "synthetic-upstream-only"
                if self.path.endswith("count_tokens"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"input_tokens":100}')
                    return
                requests.append(body)
                assert self.path == "/v1/messages"
                assert body["model"] in {"synthetic-model", "synthetic-worker"}
                names = [t["name"] for t in body.get("tools", [])]
                assert not any(n in names for n in ("Agent", "Task"))
                name = next(n for n in names if n.endswith("research_probe"))
                called = any(
                    p.get("type") == "tool_result"
                    for m in body["messages"]
                    for p in m.get("content", [])
                    if isinstance(p, dict)
                )
                block = (
                    {"type": "text", "text": ""}
                    if called
                    else {"type": "tool_use", "id": "tool_fixture", "name": name, "input": {}}
                )
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_fixture",
                            "type": "message",
                            "role": "assistant",
                            "model": body["model"],
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 100, "output_tokens": 0},
                        },
                    },
                    {"type": "content_block_start", "index": 0, "content_block": block},
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": (
                            {"type": "text_delta", "text": "Native Claude fixture complete."}
                            if called
                            else {"type": "input_json_delta", "partial_json": '{"value":3.25}'}
                        ),
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": "end_turn" if called else "tool_use",
                            "stop_sequence": None,
                        },
                        "usage": {"output_tokens": 20},
                    },
                    {"type": "message_stop"},
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in events:
                    self.wfile.write(
                        (
                            "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                        ).encode()
                    )
                    self.wfile.flush()
            except Exception as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
                self.send_error(500)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(
            prefix="native-claude-fixture-", dir=Path(__file__).resolve().parents[1]
        ) as directory:
            root = Path(directory)
            ctx = context(root, "claude_code", members=members)
            ctx["contract"].update(
                effort="high",
                harness={
                    "name": "claude_code",
                    "executable": str(Path(shutil.which("claude")).resolve()),
                },
                backend={"auth": "api", "protocol": "anthropic"},
            )
            if members > 1:
                ctx["contract"]["researcher_effort"] = "medium"
            identity = TeamIdentity(
                "claude-fixture",
                hashlib.sha256((root / "task/TASK.md").read_bytes()).hexdigest(),
                "synthetic-model",
                "high",
                members,
                **(
                    {"researcher_model": "synthetic-worker", "researcher_effort": "medium"}
                    if members > 1
                    else {}
                ),
            )
            ctx["service"] = ScientificTeamService(TeamStore(root / "claude-team", identity))
            ctx["environment"] = SimpleNamespace(
                handlers={
                    "research_probe": lambda actor, args, request_id: calls.append((actor, args))
                    or {"ok": True, "raw": args["value"]}
                }
            )
            env = root / "host.env"
            env.write_text(
                f"BASE=http://127.0.0.1:{upstream.server_port}\nKEY=synthetic-upstream-only\n"
            )
            env.chmod(0o600)
            gateway = APIGateway(
                bind_backend(
                    dict(
                        auth="api",
                        protocol="anthropic",
                        env_file=str(env),
                        base_url_env="BASE",
                        api_key_env="KEY",
                    )
                ),
                ["synthetic-model", "synthetic-worker"],
                root / "native",
            )
            specs = [
                {
                    "name": "research_probe",
                    "description": "Synthetic probe, run once.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "number"}},
                        "required": ["value"],
                    },
                }
            ]
            url = gateway.start()
            client = StreamSession(ctx, url, gateway.token, specs)
            try:
                qualify(root, ctx["task"], ctx["contract"]["harness"])
                started = client.start_or_resume()
                client.begin("Call research_probe with 3.25 then reply. Synthetic acceptance only.")
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline and not client.poll():
                    pass
                if not client.completed or client.completed["status"] != "completed":
                    print(
                        "".join(
                            json.loads(line).get("payload", {}).get("event", {}).get("stderr", "")
                            for line in (root / "transcript.jsonl").read_text().splitlines()
                        )
                    )
                assert client.completed and client.completed["status"] == "completed", (
                    errors,
                    "".join(
                        json.loads(line).get("payload", {}).get("event", {}).get("stderr", "")
                        for line in (root / "transcript.jsonl").read_text().splitlines()
                    ),
                )
                assert calls == [(started["thread_id"], {"value": 3.25})], (calls, errors)
                assert "Native Claude fixture complete" in json.dumps(client.completed)
                client.close()
                client = StreamSession(ctx, url, gateway.token, specs)
                client.start_or_resume(started["thread_id"])
                client.begin("Continue your own session; do not rerun the probe. Reply briefly.")
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline and not client.poll():
                    pass
                assert client.completed and client.completed["status"] == "completed"
                assert len(calls) == 1
                if members > 1:
                    client.worker_pool.spawn(
                        started["thread_id"],
                        {"name": "worker", "message": "Run the probe once then reply."},
                        "claude-worker-probe",
                    )
                    deadline = time.monotonic() + 35
                    while (
                        time.monotonic() < deadline
                        and client.worker_pool.status(None, {}, None)["active"]
                    ):
                        time.sleep(0.05)
                    workers = client.worker_pool.status(None, {}, None)["workers"]
                    assert workers[0]["status"] == "IDLE", (workers, errors)
                    assert len(calls) == 2 and calls[1][0] != calls[0][0]
                transcript = (root / "transcript.jsonl").read_text()
                assert "synthetic-upstream-only" not in transcript and not errors
            finally:
                client.close()
                gateway.close()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(2)
