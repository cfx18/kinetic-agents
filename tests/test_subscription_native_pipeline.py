"""Installed Codex Astra/xhigh -> fake model -> real tools -> locked submission.

This deliberately replaces the endpoint and auth ONLY IN THE TEST. It verifies
the installed native harness/tool surface, not OpenAI inference quality/access.
"""

import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile
import time
import unittest
from unittest.mock import patch

from kinetic_agents.core.store import Store
from kinetic_agents.core.storage import atomic
from kinetic_agents.native.subscription import SubscriptionTeamClient
from kinetic_agents.native.subscription import MODEL
from kinetic_agents.native.subscription import EFFORT
from kinetic_agents.runner import SubscriptionEnvironment
from kinetic_agents.runner import finalize
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore
from kinetic_agents.research.environment import ScientificTeamService
from kinetic_agents.execution.jobs import RemoteJobs
from kinetic_agents.research.policy import run_team
import kinetic_agents.native.subscription as subscription_native
from native_fixture import ScriptedResponses
from native_fixture import function
from native_fixture import final


@unittest.skipUnless(
    os.environ.get("RUN_NATIVE_TEAM_QUALIFICATION") == "1",
    "explicit local fake endpoint only",
)
@patch.dict(
    os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
)
class NativeSubscriptionPipeline(unittest.TestCase):
    def test_actual_cli_xhigh_no_children_files_notes_and_locked_final(self):
        # Native Codex substitutes /tmp inside its shell sandbox. Keep public
        # inputs in a distinct real read-only mount, as production does; the
        # host session/workspace can remain fast local scratch.
        with tempfile.TemporaryDirectory(
            prefix="cfx-public-input-", dir=Path(__file__).resolve().parents[1]
        ) as public, tempfile.TemporaryDirectory(prefix="cfx-astra-native-") as d:
            root = Path(d)
            public_task = Path(public)
            (root / "work").mkdir()
            task = (
                "Synthetic subscription-adapter acceptance. No chemistry or public web."
            )
            (public_task / "TASK.md").write_text(task)
            (root / "input-reference.json").write_text(
                json.dumps(
                    {"schema": "shared-task.v1", "task_directory": str(public_task)}
                )
            )
            sha = hashlib.sha256(task.encode()).hexdigest()
            (root / "private-canary").write_text("synthetic private host only")
            (root / "work/escape").symlink_to(root / "private-canary")
            contract = dict(
                api_usd=0,
                cpu_seconds=512 * 3600,
                wall_seconds=150,
                arm="solo-max",
                model=MODEL,
                effort=EFFORT,
            )
            store = Store(root / "runtime.sqlite")
            store.initialize(contract)
            store.update("SYNTHETIC_FAIL_FAST", failures=5)
            service = ScientificTeamService(
                TeamStore(
                    root / "team",
                    TeamIdentity("cfx-astra-fixture", sha, MODEL, EFFORT, 1),
                )
            )
            env = SubscriptionEnvironment(
                root, store, service, remote=RemoteJobs(root, store, transport=False)
            )
            tool_diagnostics = []

            def scripted(body, headers, serial):
                self.assertEqual(body["model"], MODEL)
                self.assertEqual(body["reasoning"]["effort"], EFFORT)
                # Astra's native wire format declares tools as input items,
                # whereas the DeepSeek fixture uses the top-level tools field.
                declared = list(body.get("tools", []))
                for item in body.get("input", []):
                    if item.get("type") == "additional_tools":
                        declared.append(item)
                        tool_diagnostics.append(
                            {
                                k: (
                                    str(v)[:1200]
                                    if isinstance(v, str)
                                    else type(v).__name__
                                )
                                for k, v in item.items()
                            }
                        )

                def names_in(value):
                    if isinstance(value, dict):
                        return (
                            {value["name"]}
                            if isinstance(value.get("name"), str)
                            else set()
                        ) | set().union(*(names_in(v) for v in value.values()))
                    if isinstance(value, list):
                        return set().union(*(names_in(v) for v in value))
                    return set()

                names = names_in(declared)
                tool_diagnostics.append({"names": sorted(names)})
                self.assertIn("exec", names)
                self.assertNotIn("spawn_agent", names)
                self.assertNotIn("multi_agent_v1", names)
                self.assertNotIn("collaboration", names)

                def execute(code):
                    return [
                        {
                            "type": "custom_tool_call",
                            "id": f"ct_{serial}",
                            "call_id": f"call_{serial}",
                            "name": "exec",
                            "namespace": "functions",
                            "input": code,
                        }
                    ]

                if serial == 1:
                    return execute("text(await tools.research_budget({}));")
                if serial == 2:
                    # Evidence is already on disk DURING the model/tool loop.
                    live = (root / "transcript.jsonl").read_text()
                    self.assertIn("model_input", live)
                    self.assertIn("research_budget", live)
                    self.assertIn("tool_return", live)
                    outputs = [
                        i
                        for i in body["input"]
                        if i.get("type")
                        in ("function_call_output", "custom_tool_call_output")
                    ]
                    tool_diagnostics.append({"outputs": str(outputs)[-3000:]})
                    self.assertIn("ChatGPT subscription", json.dumps(outputs))
                    code = f"""from pathlib import Path
for path in (Path({str(root/'private-canary')!r}),Path("escape")):
    try: path.read_bytes()
    except (PermissionError,FileNotFoundError): pass
    else: raise AssertionError("host read or symlink escape allowed")
assert Path({str(public_task/'TASK.md')!r}).read_text() == {task!r}
try: Path({str(public_task/'forbidden')!r}).write_text("forbidden")
except OSError as exc:
    assert exc.errno in (1,13,30), exc.errno
else: raise AssertionError("task capsule writable")
Path("candidate.yaml").write_text("synthetic")
Path("REPORT.md").write_text("not a science result")
print("native-output-captured")
"""
                    return execute(
                        "text(await tools.exec_command("
                        + json.dumps(dict(cmd="python3 -c " + shlex.quote(code)))
                        + "));"
                    )
                if serial == 3:
                    outputs = [
                        i
                        for i in body["input"]
                        if i.get("type")
                        in ("function_call_output", "custom_tool_call_output")
                    ]
                    rendered = json.dumps(outputs[-1])
                    tool_diagnostics.append({"native_command_result": rendered})
                    self.assertNotIn("Traceback", rendered, rendered)
                    live_events = [
                        json.loads(line)
                        for line in (root / "transcript.jsonl").read_text().splitlines()
                    ]
                    native_outputs = [
                        e
                        for e in live_events
                        if e["kind"] == "native_tool_io"
                        and e["payload"]["params"]["item"].get("type")
                        in ("custom_tool_call_output", "function_call_output")
                    ]
                    self.assertTrue(
                        any(
                            "native-output-captured" in json.dumps(e)
                            for e in native_outputs
                        ),
                        "native stdout must already be captured before the next tool decision",
                    )
                    return execute(
                        "text(await tools.research_record_decision("
                        + json.dumps(
                            dict(
                                decision_id="decision_fixture",
                                expected_version=0,
                                stage="validation",
                                decision="合成文件验收",
                                reason_summary="仅验证接口",
                                alternatives=[],
                                references=[],
                                outcome="文件已写入",
                                revisit_when="文件校验失败时",
                            )
                        )
                        + "));"
                    )
                if serial == 4:
                    return execute(
                        "text(await tools.finish_research("
                        + json.dumps(
                            dict(
                                mechanisms=["candidate.yaml"],
                                report="REPORT.md",
                                outcome="submitted",
                            )
                        )
                        + "));"
                    )
                if serial == 5:
                    return [final(serial)]
                if serial == 6:
                    return execute('text("resume-output-captured");')
                if serial == 7:
                    return [final(serial)]
                raise AssertionError(
                    "synthetic submission did not finish; no unbounded fixture continuations"
                )

            provider = ScriptedResponses(scripted, model=MODEL)
            original_flags = subscription_native.flags
            protocol_errors = []
            legacy_shapes = []

            def fixture_flags(settings):
                settings = {
                    **settings,
                    "model_provider": "fixture",
                    "model_providers.fixture": {
                        "name": "Synthetic local only",
                        "base_url": provider.url,
                        "wire_api": "responses",
                        "requires_openai_auth": False,
                        "request_max_retries": 0,
                        "stream_max_retries": 0,
                        "supports_websockets": False,
                    },
                    "features.responses_websockets": False,
                    "features.responses_websockets_v2": False,
                }
                return original_flags(settings)

            class FixtureClient(SubscriptionTeamClient):
                provider = "fixture"

                def prepare_account(self):
                    pass  # No real credential or auth network.

                def handle_message(self, message):
                    if message.get("method", "").startswith("codex/event/"):
                        p = message.get("params") or {}
                        legacy_shapes.append(
                            {
                                "method": message["method"],
                                "keys": list(p),
                                "msg_keys": (
                                    list(p.get("msg", {}))
                                    if isinstance(p.get("msg"), dict)
                                    else None
                                ),
                            }
                        )
                    if message.get("method") == "turn/completed":
                        protocol_errors.append(
                            (message.get("params") or {}).get("turn", {}).get("error")
                        )
                    return super().handle_message(message)

            class NoGateway:
                token = None

                def start(self):
                    return None

                def close(self):
                    pass

            def harness(account, token, specs):
                return FixtureClient(
                    root / "work",
                    public_task,
                    root / "native",
                    MODEL,
                    EFFORT,
                    account,
                    token,
                    service,
                    extra_tools=[s for s in specs if not s["name"].startswith("team_")],
                    actor_tools=env.handlers,
                    actor_read_only=(
                        "research_budget",
                        "research_compute_status",
                        "research_review_read",
                        "research_compute_read",
                    ),
                )

            try:
                with patch.object(
                    subscription_native, "flags", side_effect=fixture_flags
                ):
                    result = run_team(
                        root,
                        store,
                        contract,
                        service=service,
                        environment=env,
                        harness_factory=harness,
                        gateway_factory=NoGateway,
                        finalize=lambda r, s: finalize(r, s, service),
                        save=lambda n, v: atomic(root / n, v),
                        task_manifest={"TASK.md": sha},
                        adapter_identity={
                            "harness": "astra-solo-fake-provider-fixture"
                        },
                    )
                diagnostics = dict(
                    state=store.read_state(("status", "error_type", "diagnostic_code")),
                    protocol_errors=protocol_errors[:6],
                    tool_diagnostics=tool_diagnostics[:12],
                    provider_faults=provider.faults,
                    requests=[
                        {
                            "model": r.get("model"),
                            "reasoning": r.get("reasoning"),
                            "keys": list(r),
                            "input_summary": [
                                {
                                    "type": i.get("type"),
                                    "role": i.get("role"),
                                    "content_types": [
                                        (
                                            c.get("type")
                                            if isinstance(c, dict)
                                            else type(c).__name__
                                        )
                                        for c in i.get("content", [])
                                    ],
                                }
                                for i in r.get("input", [])
                                if isinstance(i, dict)
                            ],
                            "tools": [t.get("name") for t in r.get("tools", [])],
                        }
                        for r in provider.requests[:6]
                    ],
                )
                self.assertEqual(result["status"], "COMPLETED", diagnostics)
                self.assertEqual(len(result["submission"]["mechanisms"]), 1)
                self.assertEqual(
                    (root / "work/candidate.yaml").read_text(), "synthetic"
                )
                self.assertFalse((root / "native/codex/auth.json").exists())
                self.assertFalse(provider.faults)
                self.assertEqual(store.snapshot()["api"]["requests"], 0)
                usage = json.loads(
                    (root / "native/subscription_usage.json").read_text()
                )
                self.assertIsNone(usage["api_usd"])
                events = [
                    json.loads(line)
                    for line in (root / "transcript.jsonl").read_text().splitlines()
                ]
                self.assertTrue(
                    any(
                        e["kind"] == "public_item_completed"
                        and e["payload"]["params"]["item"]["type"] == "agentMessage"
                        for e in events
                    )
                )
                rendered = (root / "transcript.md").read_text()
                self.assertIn("candidate.yaml", rendered)
                self.assertTrue(
                    "exec_command" in rendered,
                    str(
                        {
                            "error": "native code-mode command missing from live transcript",
                            "unknown": [
                                e["payload"]
                                for e in events
                                if e["kind"] == "unsupported_notification"
                            ],
                        }
                    ),
                )
                self.assertIn("合成文件验收", rendered)
                self.assertNotIn("chatgptAuthTokens", rendered)
                # Process restart must keep recording original native calls,
                # not merely public completion summaries.
                with patch.object(
                    subscription_native, "flags", side_effect=fixture_flags
                ):
                    resumed = FixtureClient(
                        root / "work",
                        public_task,
                        root / "native",
                        MODEL,
                        EFFORT,
                        None,
                        None,
                        service,
                    )
                    try:
                        resumed.start_or_resume(store.get("thread_id"))
                        resumed.begin(
                            "Synthetic local capture restart check only; no scientific tools."
                        )
                        deadline = time.monotonic() + 15
                        while not resumed.poll() and time.monotonic() < deadline:
                            pass
                        self.assertTrue(
                            resumed.completed, "resumed fake turn did not complete"
                        )
                    finally:
                        resumed.close()
                resumed_events = [
                    json.loads(line)
                    for line in (root / "transcript.jsonl").read_text().splitlines()
                ]
                resumed_io = [
                    e
                    for e in resumed_events
                    if e["stream"] == resumed.transcript.stream
                    and e["kind"] == "native_tool_io"
                ]
                self.assertTrue(
                    any("resume-output-captured" in json.dumps(e) for e in resumed_io),
                    str(
                        {
                            "error": "resumed process must preserve raw public code-mode capture",
                            "legacy": legacy_shapes[-50:],
                            "unknown": [
                                e["payload"]
                                for e in resumed_events
                                if e["stream"] == resumed.transcript.stream
                                and e["kind"] == "unsupported_notification"
                            ],
                        }
                    ),
                )
                with service.store.connection() as db:
                    self.assertEqual(
                        db.execute("SELECT COUNT(*) FROM agents").fetchone()[0], 1
                    )
            finally:
                provider.close()


if __name__ == "__main__":
    unittest.main()
