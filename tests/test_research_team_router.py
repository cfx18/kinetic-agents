import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore
from kinetic_agents.team.service import TeamService
from kinetic_agents.team.router import TeamRouter
from kinetic_agents.team.tools import schemas
from kinetic_agents.team.tools import OPERATIONS


class TeamRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cfx-team-router-")
        self.service = TeamService(
            TeamStore(Path(self.tmp.name) / "host", TeamIdentity("test", "a" * 64, "fake", "max"))
        )
        self.router = TeamRouter(self.service)
        self.router.bind_principal("pi")

    def tearDown(self):
        self.tmp.cleanup()

    def child(self, child="worker", parent="pi"):
        return {
            "method": "thread/started",
            "params": {
                "thread": {
                    "id": child,
                    "source": {
                        "subAgent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1}}
                    },
                }
            },
        }

    def request(self, actor="worker", **overrides):
        return {
            "threadId": actor,
            "turnId": "turn",
            "callId": "call",
            "tool": "team_messages",
            "arguments": {},
            **overrides,
        }

    def test_native_registration_ownership_and_foreign_denial(self):
        with self.assertRaises(PermissionError):
            self.router.dispatch(self.request())
        self.assertTrue(self.router.observe(self.child()))
        self.assertTrue(self.router.dispatch(self.request())["success"])
        with self.assertRaises(PermissionError):
            self.router.observe(self.child("foreign", "elsewhere"))

    def test_progress_notifications_are_zero_database_work(self):
        with patch.object(
            self.service.store, "connection", side_effect=AssertionError("must not open database")
        ):
            for _ in range(1000):
                self.assertFalse(
                    self.router.observe(
                        {"method": "item/reasoning/textDelta", "params": {"threadId": "pi"}}
                    )
                )

    def test_transport_actor_and_request_id_cannot_be_spoofed(self):
        self.router.observe(self.child())
        for extra in ({"actor": "pi"}, {"request_id": "forged"}):
            with self.assertRaises(PermissionError):
                self.router.dispatch(self.request(arguments=extra))
        with self.assertRaises(PermissionError):
            self.router.dispatch(
                self.request(tool="team_finish", arguments={"summary": "fake", "references": []})
            )

    def test_mutation_replay_survives_router_restart(self):
        self.router.observe(self.child())
        call = self.request(
            "pi",
            tool="team_delegate",
            arguments={
                "assignee": "worker",
                "goal": "fixture",
                "acceptance": "fixture only",
                "references": [],
            },
        )
        first = self.router.dispatch(call)
        self.router = TeamRouter(TeamService(TeamStore(Path(self.tmp.name) / "host")))
        self.assertEqual(self.router.dispatch(call), first)
        self.assertEqual(len(self.service.store.status()["tasks"]), 1)
        with self.assertRaises(ValueError):
            self.router.dispatch({**call, "arguments": {**call["arguments"], "goal": "changed"}})

    def test_schemas_match_callable_arguments_without_actor_or_retry_id(self):
        for spec, operation in zip(schemas(), OPERATIONS):
            expected = set(inspect.signature(getattr(TeamService, operation)).parameters) - {
                "self",
                "actor",
                "request_id",
            }
            self.assertEqual(set(spec["inputSchema"]["properties"]), expected)
            self.assertFalse(spec["inputSchema"]["additionalProperties"])
        self.assertEqual(len(schemas()), len(OPERATIONS))


if __name__ == "__main__":
    unittest.main()
