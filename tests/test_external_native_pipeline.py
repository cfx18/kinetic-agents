"""Actual installed Kimi CLI -> synthetic local API -> actual scoped MCP, zero inference."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from test_harness_adapters import context
from kinetic_agents.harnesses.stream import StreamSession
from kinetic_agents.harnesses.sandbox import qualify
from kinetic_agents.harnesses.gateway import APIGateway
from kinetic_agents.connections import bind_backend

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NATIVE_TEAM_QUALIFICATION") != "1",
    reason="explicit loopback qualification only",
)


@pytest.mark.parametrize("members", [1, 3])
@pytest.mark.parametrize("kind", ["kimi_code", "kimi_code_node"])
def test_installed_kimi_tools_live_capture_resume_and_gateway(members, kind):
    requests, calls, errors = [], [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                assert self.headers["Authorization"] == "Bearer synthetic-upstream-only"
                assert body["model"] in {"synthetic-model", "synthetic-worker"}
                if kind == "kimi_code_node":
                    assert body.get("reasoning_effort") == "max", repr(body.get("reasoning_effort"))
                assert self.path == "/v1/chat/completions"
                names = [v["function"]["name"] for v in body["tools"]]
                if kind != "kimi_code_node":
                    assert not any(v in names for v in ("Agent", "AgentSwarm", "Task")), names
                name = next(n for n in names if n.endswith("research_probe"))
                called = any(m.get("role") == "tool" and m.get("tool_call_id") == "call_probe" for m in body["messages"])
                denied = [m for m in body["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == "call_forbidden"]
                if kind == "kimi_code_node" and denied:
                    assert "denied" in str(denied[0]["content"]).lower(), denied
                delta = (
                    {"role": "assistant", "content": "Native fixture complete."}
                    if called
                    else {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_probe",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": '{"value":3.25}',
                                },
                            }
                        ],
                    }
                )
                if kind == "kimi_code_node" and not denied:
                    delta = {"role": "assistant", "tool_calls": [{
                        "index": 0, "id": "call_forbidden", "type": "function",
                        "function": {"name": "Agent", "arguments": json.dumps({
                            "prompt": "DO NOT LAUNCH: synthetic forbidden child probe",
                            "description": "Forbidden untracked child",
                        })},
                    }]}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in [
                    dict(
                        id="chat_fixture",
                        object="chat.completion.chunk",
                        created=1,
                        model="synthetic-model",
                        choices=[{"index": 0, "delta": delta, "finish_reason": None}],
                    ),
                    dict(
                        id="chat_fixture",
                        object="chat.completion.chunk",
                        created=1,
                        model="synthetic-model",
                        choices=[
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop" if called else "tool_calls",
                            }
                        ],
                        usage={
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "total_tokens": 12,
                        },
                    ),
                ]:
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
            except Exception as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
                self.send_error(500)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(
            prefix="native-kimi-fixture-", dir=Path(__file__).resolve().parents[1]
        ) as directory:
            root = Path(directory)
            ctx = context(root, kind=kind, members=members)
            from kinetic_agents.harnesses.kimi_node import KimiNodeSession
            session_class = KimiNodeSession if kind == "kimi_code_node" else StreamSession
            (root / "evaluation_base.json").write_text("private fixture")
            # qualify expects a run/arm hierarchy for its host-only canary.
            (root / "../evaluation_base.json").resolve()  # no mutation outside fixture
            env = root / "host.env"
            env.write_text(
                f"BASE=http://127.0.0.1:{upstream.server_port}/v1\nKEY=synthetic-upstream-only\n"
            )
            env.chmod(0o600)
            gateway = APIGateway(
                bind_backend(
                    dict(
                        auth="api",
                        protocol="chat_completions",
                        env_file=str(env),
                        base_url_env="BASE",
                        api_key_env="KEY",
                    )
                ),
                ["synthetic-model", "synthetic-worker"],
                root / "native",
                expected_efforts=({"synthetic-model": "max", "synthetic-worker": "max"}
                                 if kind == "kimi_code_node" else None),
            )
            url = gateway.start()
            ctx["environment"] = SimpleNamespace(
                handlers={
                    "research_probe": lambda actor, args, request_id: calls.append((actor, args))
                    or {"raw": args["value"], "ok": True}
                }
            )
            specs = [
                {
                    "name": "research_probe",
                    "description": "Call this synthetic acceptance probe once.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "number"}},
                        "required": ["value"],
                    },
                }
            ]
            client = session_class(ctx, url, gateway.token, specs)
            try:
                qualify(root, ctx["task"], ctx["contract"]["harness"])
                started = client.start_or_resume()
                client.begin(
                    "Call research_probe with value 3.25, then finish your reply. Synthetic fixture only."
                )
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline and not client.poll():
                    pass
                transcript = (root / "transcript.jsonl").read_text()
                assert client.completed and client.completed["status"] == "completed", (
                    errors,
                    transcript[-10000:],
                )
                assert calls == [(started["thread_id"], {"value": 3.25})], (
                    errors,
                    transcript[-10000:],
                )
                assert (
                    "tool_request" in transcript
                    and "tool_result" in transcript
                    and "Native fixture complete" in transcript
                )
                assert "synthetic-upstream-only" not in transcript
                from kinetic_agents.observability.stream_review import snapshot
                from kinetic_agents.observability.review import Sources

                review = snapshot(Sources(root))
                assert any(
                    a["tool"] == "research_probe" and a["outputs"] for a in review["actions"]
                )
                assert any("Native fixture complete" in s["text"] for s in review["statements"]), [
                    json.loads(row)["payload"].get("event")
                    for row in transcript.splitlines()
                    if json.loads(row)["kind"] == "native_event"
                ]
                client.close()
                client = session_class(ctx, url, gateway.token, specs)
                assert client.start_or_resume(started["thread_id"])["resumed"]
                client.begin("Keep the same evidence and reply briefly. Do not rerun the probe.")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and not client.poll():
                    pass
                assert client.completed and client.completed["status"] == "completed"
                assert len(calls) == 1 and not errors
                if members > 1:
                    client.worker_pool.spawn(
                        started["thread_id"],
                        {
                            "name": "worker",
                            "message": "Call the acceptance probe once, then reply.",
                        },
                        "kimi-probe-worker",
                    )
                    deadline = time.monotonic() + 35
                    while (
                        time.monotonic() < deadline
                        and client.worker_pool.status(None, {}, None)["active"]
                    ):
                        time.sleep(0.05)
                    rows = client.worker_pool.status(None, {}, None)["workers"]
                    assert rows[0]["status"] == "IDLE", rows
                    assert len(calls) == 2 and calls[1][0] != calls[0][0]
                    assert "Native fixture complete" in rows[0]["reply"]
                assert "USAGE_OBSERVATION" in (root / "native/api_requests.jsonl").read_text()
            finally:
                client.close()
                gateway.close()
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(2)


@pytest.mark.parametrize("members", [1, 3])
def test_installed_codex_api_custom_model_and_workers(members):
    from kinetic_agents.native.api import APITeamClient
    from kinetic_agents.team.contracts import TeamIdentity
    from kinetic_agents.team.store import TeamStore
    from kinetic_agents.research.environment import ScientificTeamService
    from native_fixture import ScriptedResponses, final, function
    import hashlib
    import shutil

    with tempfile.TemporaryDirectory(
        prefix="native-api-fixture-", dir=Path(__file__).resolve().parents[1]
    ) as directory:
        root = Path(directory)
        ctx = context(root)
        sha = hashlib.sha256((root / "task/TASK.md").read_bytes()).hexdigest()
        identity = TeamIdentity(
            "codex-api-fixture",
            sha,
            "gpt-6-astra",
            "xhigh",
            members,
            **({"researcher_model": "gpt-5.5", "researcher_effort": "high"} if members > 1 else {}),
        )
        service = ScientificTeamService(TeamStore(root / "api-team", identity))
        calls, seen = [], set()

        def script(body, headers, serial):
            model = body["model"]
            assert model in {"gpt-6-astra", "gpt-5.5"}
            if model in seen:
                return [final(serial)]
            seen.add(model)
            if model == "gpt-6-astra":
                return [
                    {
                        "type": "custom_tool_call",
                        "id": f"ct_{serial}",
                        "call_id": f"call_{serial}",
                        "name": "exec",
                        "namespace": "functions",
                        "input": "text(await tools.research_probe({value:3.25}));",
                    }
                ]
            return [function("research_probe", {"value": 5.5}, serial)]

        api = ScriptedResponses(script, model=None)
        specs = [
            {
                "type": "function",
                "name": "research_probe",
                "description": "Synthetic acceptance probe.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"value": {"type": "number"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            }
        ]
        client = APITeamClient(
            root / "work",
            root / "task",
            root / "native",
            "gpt-6-astra",
            "xhigh",
            api.url,
            "synthetic-gateway-token",
            service,
            extra_tools=specs,
            actor_tools={
                "research_probe": lambda actor, args, request_id: calls.append((actor, args))
                or {"ok": True}
            },
            executable=shutil.which("codex"),
        )
        rpc_errors = []
        observe = client.receiver.observer

        def capture(message, at):
            if "error" in message:
                rpc_errors.append(message["error"])
            observe(message, at)

        client.receiver.observer = capture
        try:
            try:
                actor = client.start_or_resume()["thread_id"]
            except RuntimeError as exc:
                raise AssertionError(rpc_errors) from exc
            client.begin("Run the synthetic probe and end this turn.")
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline and not client.poll():
                pass
            assert client.completed and client.completed["status"] == "completed", api.faults
            assert calls == [(actor, {"value": 3.25})], api.faults
            if members > 1:
                client.worker_pool.spawn(
                    actor,
                    {
                        "name": "cheap",
                        "message": "Run the acceptance probe once and reply.",
                    },
                    "fixture-spawn",
                )
                deadline = time.monotonic() + 30
                while (
                    time.monotonic() < deadline
                    and client.worker_pool.status(None, {}, None)["active"]
                ):
                    time.sleep(0.05)
                rows = client.worker_pool.status(None, {}, None)["workers"]
                assert rows[0]["status"] == "IDLE", (rows, api.faults)
                assert len(calls) == 2 and calls[1][1] == {"value": 5.5}, api.faults
                assert calls[1][0] != actor
            assert not api.faults
            assert "configured_API" in (root / "native/subscription_usage.json").read_text()
        finally:
            client.close()
            api.close()
