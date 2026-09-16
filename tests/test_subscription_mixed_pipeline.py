"""Installed native Codex workers -> synthetic Responses -> real shared tools."""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from kinetic_agents.core.store import Store
from kinetic_agents.core.storage import atomic
import kinetic_agents.native.subscription as native
from kinetic_agents.runner import SubscriptionEnvironment
from kinetic_agents.runner import finalize
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore
from kinetic_agents.research.environment import ScientificTeamService
from kinetic_agents.execution.jobs import RemoteJobs
from kinetic_agents.research.policy import run_team
from native_fixture import ScriptedResponses
from native_fixture import function
from native_fixture import final


@unittest.skipUnless(
    os.environ.get("RUN_NATIVE_TEAM_QUALIFICATION") == "1",
    "localhost synthetic qualification only",
)
@patch.dict(
    os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
)
class MixedPipeline(unittest.TestCase):
    def test_parent_workers_tools_submission_and_resume(self):
        with tempfile.TemporaryDirectory(prefix="cfx-mixed-") as d:
            root = Path(d)
            (root / "work").mkdir()
            (root / "task").mkdir()
            task = "Synthetic mixed subscription pipeline. No chemistry, model inference or remote service."
            (root / "task/TASK.md").write_text(task)
            sha = hashlib.sha256(task.encode()).hexdigest()
            contract = dict(
                model=native.MODEL,
                effort=native.EFFORT,
                arm="team-max",
                api_usd=0,
                cpu_seconds=512 * 3600,
                wall_seconds=90,
                researcher_model="gpt-5.5",
                researcher_effort="high",
            )
            store = Store(root / "runtime.sqlite")
            store.initialize(contract)
            store.update("FIXTURE_FAIL_FAST", failures=5)
            identity = TeamIdentity(
                "mixed-fixture",
                sha,
                native.MODEL,
                native.EFFORT,
                3,
                researcher_model="gpt-5.5",
                researcher_effort="high",
            )
            service = ScientificTeamService(TeamStore(root / "team", identity))
            env = SubscriptionEnvironment(
                root, store, service, remote=RemoteJobs(root, store, transport=False)
            )
            counts = Counter()
            clients = []
            diagnostics = []
            worker_outputs = {}
            gate = threading.Event()

            def execute(code, serial):
                return [
                    dict(
                        type="custom_tool_call",
                        id=f"ct_{serial}",
                        call_id=f"call_{serial}",
                        name="exec",
                        namespace="functions",
                        input=code,
                    )
                ]

            def script(body, headers, serial):
                actor = next(v for k, v in headers.items() if k.lower() == "thread-id")
                counts[actor] += 1
                n = counts[actor]
                outputs = [
                    i
                    for i in body.get("input", [])
                    if i.get("type")
                    in ("function_call_output", "custom_tool_call_output")
                ]
                diagnostics.append(
                    dict(model=body["model"], n=n, outputs=str(outputs[-1:])[-1500:])
                )
                if body["model"] == "gpt-5.5":
                    self.assertEqual(body["reasoning"]["effort"], "high")
                    names = {i.get("name") for i in body.get("tools", [])}
                    self.assertIn("research_budget", names)
                    self.assertIn("team_claim", names)
                    self.assertNotIn("collaboration", names)
                    if n == 1:
                        return [function("research_budget", {}, serial)]
                    if n == 2:
                        self.assertIn("researcher", str(outputs[-1]))
                        self.assertIn("ChatGPT subscription", str(outputs[-1]))
                        gate.wait(8)
                        code = (
                            'from pathlib import Path; p=Path("agents")/'
                            + repr(actor)
                            + '; p.mkdir(parents=True,exist_ok=True); (p/"note.txt").write_text("synthetic evidence"); print("worker-output-captured")'
                        )
                        return [
                            function(
                                "exec_command",
                                dict(cmd="python3 -c " + shlex.quote(code)),
                                serial,
                            )
                        ]
                    if n == 3:
                        self.assertIn("worker-output-captured", str(outputs[-1]))
                        worker_outputs[actor] = True
                        row = next(
                            r
                            for r in service.list(actor, kind="task")["rows"]
                            if r["assignee"] == actor
                        )
                        return [
                            function(
                                "team_claim",
                                dict(
                                    task_id=row["id"], expected_version=row["version"]
                                ),
                                serial,
                            )
                        ]
                    if n == 4:
                        row = next(
                            r
                            for r in service.list(actor, kind="task")["rows"]
                            if r["assignee"] == actor
                        )
                        return [
                            function(
                                "team_submit",
                                dict(
                                    task_id=row["id"],
                                    expected_version=row["version"],
                                    summary="合成子 Agent 文件已生成，仅接口验收。",
                                    references=[],
                                ),
                                serial,
                            )
                        ]
                    if n == 5:
                        return [final(serial)]
                    if n == 6:
                        return [function("research_budget", {}, serial)]
                    if n == 7:
                        return [final(serial)]
                    raise AssertionError("unexpected worker continuation")
                self.assertEqual(body["model"], native.MODEL)
                self.assertEqual(body["reasoning"]["effort"], "xhigh")
                if n <= 3:
                    return execute(
                        "text(await tools.research_spawn("
                        + json.dumps(
                            dict(
                                name="worker_" + str(n),
                                message="Read shared budget, write synthetic evidence, claim and submit your assigned task.",
                            )
                        )
                        + "));",
                        serial,
                    )
                if n == 4:
                    self.assertIn("two workers are active", str(outputs[-1]))
                    gate.set()
                    deadline = time.monotonic() + 15
                    while (
                        clients[0].worker_pool.running and time.monotonic() < deadline
                    ):
                        time.sleep(0.02)
                    self.assertFalse(clients[0].worker_pool.running)
                    return execute(
                        'text(await tools.research_followup({name:"worker_1",message:"Check your existing shared budget and finish this turn."}));',
                        serial,
                    )
                if n == 5:
                    deadline = time.monotonic() + 15
                    while (
                        clients[0].worker_pool.running and time.monotonic() < deadline
                    ):
                        time.sleep(0.02)
                    self.assertFalse(clients[0].worker_pool.running)
                    code = ""
                    for row in service.list(actor, kind="task")["rows"]:
                        code += (
                            "text(await tools.team_review("
                            + json.dumps(
                                dict(
                                    task_id=row["id"],
                                    expected_version=row["version"],
                                    decision="accept",
                                    feedback="合成工具验收通过，不是科学证据",
                                )
                            )
                            + "));"
                        )
                    cmd = "python3 -c " + shlex.quote(
                        'from pathlib import Path; Path("candidate.yaml").write_text("synthetic"); Path("REPORT.md").write_text("synthetic test only")'
                    )
                    return execute(
                        code
                        + "text(await tools.exec_command("
                        + json.dumps({"cmd": cmd})
                        + "));",
                        serial,
                    )
                if n == 6:
                    return execute(
                        'text(await tools.finish_research({mechanisms:["candidate.yaml"],report:"REPORT.md",outcome:"submitted"}));',
                        serial,
                    )
                if n == 7:
                    return [final(serial)]
                raise AssertionError("unexpected principal continuation")

            provider = ScriptedResponses(script, model=None)
            original = native.flags

            def flags(settings):
                return original(
                    {
                        **settings,
                        "model_provider": "fixture",
                        "model_providers.fixture": dict(
                            name="Synthetic local only",
                            base_url=provider.url,
                            wire_api="responses",
                            requires_openai_auth=False,
                            request_max_retries=0,
                            stream_max_retries=0,
                            supports_websockets=False,
                        ),
                        "features.responses_websockets": False,
                        "features.responses_websockets_v2": False,
                    }
                )

            class Client(native.SubscriptionTeamClient):
                provider = "fixture"

                def prepare_account(self):
                    pass

                def prepare_worker_account(self, client):
                    pass

            class Gateway:
                token = None

                def start(self):
                    return None

                def close(self):
                    pass

            def factory(account, token, specs):
                client = Client(
                    root / "work",
                    root / "task",
                    root / "native",
                    native.MODEL,
                    native.EFFORT,
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
                clients.append(client)
                return client

            try:
                with patch.object(native, "flags", side_effect=flags):
                    result = run_team(
                        root,
                        store,
                        contract,
                        service=service,
                        environment=env,
                        harness_factory=factory,
                        gateway_factory=Gateway,
                        finalize=lambda r, s: finalize(r, s, service),
                        save=lambda n, v: atomic(root / n, v),
                        task_manifest={"TASK.md": sha},
                        adapter_identity={"harness": "mixed-native-fake-fixture"},
                    )
                self.assertEqual(
                    result["status"],
                    "COMPLETED",
                    str(
                        {
                            "result": result,
                            "diagnostics": diagnostics,
                            "faults": provider.faults,
                        }
                    )[-13000:],
                )
                self.assertFalse(provider.faults)
                self.assertEqual(len(worker_outputs), 2)
                self.assertEqual(len(service.store.status()["agents"]), 3)
                self.assertEqual(len(result["submission"]["mechanisms"]), 1)
                self.assertTrue((root / "final_artifacts").exists())
                workers = json.loads((root / "native/workers.json").read_text())
                self.assertTrue(
                    all(
                        r["status"] == "IDLE"
                        and "Synthetic routing complete" in r["reply"]
                        for r in workers.values()
                    )
                )
                usage = json.loads(
                    (root / "native/subscription_usage.json").read_text()
                )
                self.assertEqual(
                    set(usage["usage_by_model"]), {"gpt-6-astra", "gpt-5.5"}
                )
                for actor in worker_outputs:
                    rendered = (root / "agents" / actor / "transcript.md").read_text()
                    self.assertIn("worker-output-captured", rendered)
                    self.assertIn("research_budget", rendered)
                self.assertEqual(store.snapshot()["api"]["requests"], 0)
            finally:
                gate.set()
                provider.close()


if __name__ == "__main__":
    unittest.main()
