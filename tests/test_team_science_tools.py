from pathlib import Path
import tempfile
import unittest

from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore
from kinetic_agents.team.service import TeamService
from kinetic_agents.team.router import TeamRouter
from kinetic_agents.native.scoped_tools import ScopedScienceTools


class ScienceRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cfx-scoped-tools-")
        service = TeamService(
            TeamStore(
                Path(self.tmp.name) / "team", TeamIdentity("fixture", "a" * 64, "fake", "max")
            )
        )
        self.team = TeamRouter(service)
        self.team.bind_principal("pi")
        service.store.register("worker", "pi")
        self.team = TeamRouter(service)
        self.calls = []

        def callback(actor, args, request_id):
            self.calls.append((actor, args, request_id))
            return {"actor": actor, "synthetic": True}

        self.callback = callback
        self.tools = ScopedScienceTools(
            self.team,
            {"research_budget": callback, "submit_job": callback},
            read_only={"research_budget"},
        )

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, actor="worker", **changes):
        return dict(
            threadId=actor,
            turnId="turn",
            callId="call",
            tool="research_budget",
            arguments={},
            **changes
        )

    def test_both_actors_routed_with_distinct_host_bound_identity(self):
        self.assertTrue(self.tools.dispatch(self.request())["success"])
        self.assertTrue(self.tools.dispatch(self.request("pi"))["success"])
        self.assertEqual([v[0] for v in self.calls], ["worker", "pi"])
        self.assertNotEqual(self.calls[0][2], self.calls[1][2])

    def test_unknown_or_forged_actor_never_reaches_callback(self):
        for params in (
            self.request("foreign"),
            {**self.request(), "arguments": {"actor": "pi"}},
            {**self.request(), "arguments": {"request_id": "forged"}},
        ):
            with self.assertRaises(PermissionError):
                self.tools.dispatch(params)
        self.assertEqual(self.calls, [])

    def test_replay_key_stable_across_restart_backend_owns_durable_effects(self):
        first = self.tools.dispatch(self.request())
        self.assertEqual(self.tools.dispatch(self.request()), first)
        self.assertEqual(len(self.calls), 1)
        self.tools = ScopedScienceTools(self.team, {"research_budget": self.callback})
        self.tools.dispatch(self.request())
        self.assertEqual(self.calls[0][2], self.calls[1][2])
        with self.assertRaises(ValueError):
            self.tools.dispatch({**self.request(), "arguments": {"different": True}})

    def test_effect_boundary_guard_and_read_only_budget_query(self):
        def guard():
            raise PermissionError("ended account")

        self.assertTrue(self.tools.dispatch(self.request(), guard=guard)["success"])
        with self.assertRaises(PermissionError):
            self.tools.dispatch(
                {**self.request(), "callId": "other", "tool": "submit_job"}, guard=guard
            )
        self.assertEqual(len(self.calls), 1)

    def test_closed_worker_cannot_replay_cached_or_create_new_scientific_calls(self):
        self.tools.dispatch(self.request())
        self.team.service.store.native_lifecycle("worker", closed=True)
        for params in (self.request(), {**self.request(), "callId": "new", "tool": "submit_job"}):
            with self.assertRaises(PermissionError):
                self.tools.dispatch(params)
        self.assertEqual(len(self.calls), 1)
        self.team.service.store.native_lifecycle("worker", closed=False)
        self.assertTrue(self.tools.dispatch(self.request())["success"])


if __name__ == "__main__":
    unittest.main()
