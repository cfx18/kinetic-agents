import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from kinetic_agents.core.store import Store
from science_environment import ScienceEnvironment
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.service import TeamService
from kinetic_agents.team.store import TeamStore
from kinetic_agents.research.policy import run_team
from kinetic_agents.research.policy import CONTINUATION
from kinetic_agents.core.trajectory import Trajectory


class EmptyEnvironmentBackend:
    def rpc(self, request):
        return {
            "jobs": [],
            "resources": {"cpu_remaining_seconds": 1000},
            "stop": None,
            "resource_stopped": False,
        }


class TeamRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cfx-team-lifecycle-")
        self.root = Path(self.tmp.name)
        (self.root / "task").mkdir()
        self.task_text = "Synthetic task preserved literally; no scientific steering."
        (self.root / "task/TASK.md").write_text(self.task_text)
        self.sha = hashlib.sha256(self.task_text.encode()).hexdigest()
        self.api = TeamService(
            TeamStore(self.root / "team", TeamIdentity("fixture", self.sha, "fake", "max"))
        )
        self.contract = {
            "model": "fake",
            "effort": "max",
            "api_usd": 0,
            "cpu_seconds": 1000,
            "wall_seconds": 60,
        }
        self.account = Store(self.root / "runtime.sqlite")
        self.account.initialize(self.contract)
        self.prompts, self.resumes, self.closed = [], [], []

    def tearDown(self):
        self.tmp.cleanup()

    def drive(self, mode="normal"):
        test = self

        class Gateway:
            token = "synthetic-no-credentials"

            def start(self):
                return "synthetic://local"

            def close(self):
                test.closed.append("gateway")

        class Harness:
            tools = {}
            turn_id = None

            def __init__(self, *args):
                self.turn = 0

            def start_or_resume(self, owned):
                self.thread_id = owned or "pi"
                test.api.store.register(self.thread_id)
                test.resumes.append(owned)
                return {"thread_id": self.thread_id, "resumed": bool(owned)}

            def begin(self, prompt):
                self.turn += 1
                self.turn_id = f"turn{self.turn}"
                test.prompts.append(prompt)

            def poll(self):
                if mode == "recover" and len(test.resumes) == 1:
                    raise ConnectionError("fixture interrupted transport")
                self.team_dispatch_guard()
                if len(test.prompts) == 1:
                    test.api.store.register("worker", "pi")
                    task = test.api.delegate(
                        "pi",
                        assignee="worker",
                        goal="Fixture",
                        acceptance="Ask PI",
                        references=[],
                        request_id="delegate",
                    )
                    test.question = test.api.ask(
                        "worker",
                        task_id=task["id"],
                        expected_version=1,
                        question="Clarify acceptance?",
                        impact="Cannot complete",
                        request_id="ask",
                    )
                    test.task = task
                    return {"status": "completed"}  # PI prematurely ends: keep going.
                if mode == "cancel":
                    test.account.advance("cancel")
                    try:
                        self.team_dispatch_guard()
                    except PermissionError:
                        test.denied = True
                    return {"status": "completed"}
                if hasattr(test, "question"):
                    inbox = test.api.messages("pi")
                    test.assertIn(test.question["id"], [r["id"] for r in inbox["messages"]])
                    test.api.answer(
                        "pi",
                        question_id=test.question["id"],
                        expected_version=1,
                        answer="Synthetic fixture only",
                        request_id="answer",
                    )
                    task = test.api.read("pi", kind="task", record_id=test.task["id"])
                    test.api.cancel(
                        "pi",
                        task_id=task["id"],
                        expected_version=task["version"],
                        reason="Fixture complete",
                        request_id="cancel-task",
                    )
                test.api.finish(
                    "pi", summary="Synthetic protocol ended", references=[], request_id="finish"
                )
                return {"status": "completed"}

            def close(self):
                test.closed.append("harness")

        def save(name, value):
            (test.root / name).write_text(json.dumps(value))

        def finalize(root, account):
            if account.get("status") == "FINALIZING":
                account.advance("artifacts_verified")
            return account.get("status")

        return run_team(
            self.root,
            self.account,
            self.contract,
            service=self.api,
            environment=ScienceEnvironment(
                EmptyEnvironmentBackend(),
                [],
                Trajectory(self.account),
                deadline=self.account.get("deadline"),
            ),
            harness_factory=Harness,
            gateway_factory=Gateway,
            finalize=finalize,
            save=save,
            task_manifest={"TASK.md": self.sha},
            adapter_identity={"harness": "synthetic-native"},
        )

    def test_pending_question_restarts_principal_and_submit_does_not_start_extra_turn(self):
        self.assertEqual(self.drive(), "COMPLETED")
        self.assertEqual(len(self.prompts), 2)
        self.assertIn(self.task_text, self.prompts[0])
        self.assertEqual(self.prompts[1], CONTINUATION)
        self.assertTrue(self.api.store.status()["submitted"])
        self.assertEqual(self.account.snapshot()["api"]["requests"], 0)

    def test_same_thread_recovery_preserves_budget_and_failure_count(self):
        before = self.account.snapshot()
        self.assertEqual(self.drive("recover"), "COMPLETED")
        self.assertEqual(self.resumes, [None, "pi"])
        self.assertEqual(self.account.get("failures"), 1)
        self.assertEqual(self.account.get("deadline"), before["deadline"])
        self.assertIn("SAME registered principal", self.prompts[-1])

    def test_cancel_stops_mutation_not_just_the_next_heartbeat(self):
        self.assertEqual(self.drive("cancel"), "CANCELLED")
        self.assertTrue(self.denied)
        self.assertFalse(self.api.store.status()["submitted"])


if __name__ == "__main__":
    unittest.main()
