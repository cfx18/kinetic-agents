"""Synthetic acceptance of collaboration, memory and skill state transitions."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kinetic_agents.team.contracts import Conflict
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.contracts import encode
from kinetic_agents.team.store import TeamStore
from kinetic_agents.team.service import TeamService


def ref(kind, row):
    return {"kind": kind, "id": row["id"], "version": row["version"]}


class TeamServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cfx-team-protocol-")
        self.root = Path(self.tmp.name) / "host"
        self.store = TeamStore(self.root, TeamIdentity("synthetic", "a" * 64, "fake", "max"))
        for actor, parent in [("pi", None), ("worker", "pi"), ("peer", "pi")]:
            self.store.register(actor, parent)
        self.api = TeamService(self.store)
        self.serial = 0

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, who, op, **args):
        self.serial += 1
        return self.api.call(who, op, {"request_id": f"r{self.serial}", **args})

    def task(self):
        return self.call(
            "pi",
            "delegate",
            assignee="worker",
            goal="Compare a synthetic method",
            acceptance="Report failures and evidence; no scientific claim",
            references=[],
        )

    def claim(self, task):
        return self.call("worker", "claim", task_id=task["id"], expected_version=task["version"])

    def read(self, kind, row):
        return self.api.call("worker", "read", {"kind": kind, "record_id": row["id"]})

    def evidence(self, name="obs", category="observation", **extra):
        return self.api.import_evidence(
            evidence_id=name,
            title=name,
            category=category,
            artifact_sha256="b" * 64,
            provenance={"source_id": "fixture", "role": "synthetic"},
            summary="Synthetic fixture, not science",
            **extra,
        )

    def memory(self, actor="worker", **overrides):
        return self.call(
            actor,
            "memory",
            **{
                "memory_id": "m",
                "expected_version": 0,
                "claim": "Synthetic hypothesis",
                "applicability": {"fuel": "synthetic", "observable": "IDT"},
                "supports": [],
                "counters": [],
                "derived_from": [],
                "status": "hypothesis",
                "rationale": "Needs prospective testing",
                **overrides,
            },
        )

    def skill(self, actor="worker", **overrides):
        artifact = self.evidence("script", "artifact")
        return self.call(
            actor,
            "skill",
            **{
                "skill_id": "s",
                "expected_version": 0,
                "title": "Synthetic skill",
                "instructions": "A harmless fixture, never execute through this service",
                "applicability": {"fuel": "synthetic"},
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "artifact_ref": ref("evidence", artifact),
                "memory_refs": [],
                "test_refs": [],
                "status": "draft",
                **overrides,
            },
        )

    def trial(self):
        self.skill()
        return self.skill("pi", expected_version=1, status="trial")

    def intent(self, task, skill):
        context = self.call(
            "worker",
            "context",
            task_id=task["id"],
            expected_version=task["version"],
            references=[ref("skill", skill)],
            purpose="Prepare synthetic execution",
        )
        use = self.call(
            "worker",
            "prepare_use",
            task_id=task["id"],
            expected_version=task["version"],
            skill_ref=ref("skill", skill),
            context_id=context["id"],
        )
        return context, use

    def test_clarification_restart_review_and_submission(self):
        task = self.claim(self.task())
        q = self.call(
            "worker",
            "ask",
            task_id=task["id"],
            expected_version=task["version"],
            question="Which observable?",
            impact="Changes the metric",
        )
        self.assertEqual(self.read("task", task)["status"], "waiting_for_principal")
        self.api = TeamService(TeamStore(self.root))
        inbox = self.api.call("pi", "messages", {})
        self.assertIn(q["id"], [m["id"] for m in inbox["messages"]])
        self.call(
            "pi",
            "answer",
            question_id=q["id"],
            expected_version=1,
            answer="Use the synthetic scalar",
        )
        task = self.claim(self.read("task", task))
        self.assertEqual(task["clarifications"], [{"question_id": q["id"], "answer_version": 2}])
        delivered = self.call(
            "worker",
            "submit",
            task_id=task["id"],
            expected_version=task["version"],
            summary="Fixture completed",
            references=[],
        )
        with self.assertRaises(Conflict):
            self.call("pi", "finish", summary="Too early", references=[])
        self.call(
            "pi",
            "review",
            task_id=task["id"],
            expected_version=delivered["version"],
            decision="accept",
            feedback="Accepted as fixture, not a scientific result",
        )
        final = self.call("pi", "finish", summary="Synthetic submission", references=[])
        self.assertFalse(final["scientifically_verified"])
        self.assertEqual(final["evaluation_account"], "separate")

    def test_principal_only_actions_and_foreign_task_denied(self):
        with self.assertRaises(PermissionError):
            self.call(
                "worker", "delegate", assignee="peer", goal="x", acceptance="y", references=[]
            )
        task = self.task()
        with self.assertRaises(PermissionError):
            self.call("peer", "claim", task_id=task["id"], expected_version=1)
        with self.assertRaises(PermissionError):
            self.call("worker", "cancel", task_id=task["id"], expected_version=1, reason="x")

    def test_solo_principal_can_use_same_memory_skill_and_execution_protocol(self):
        # An actually single-member account, not a hidden/fake researcher.
        solo_root = Path(self.tmp.name) / "solo"
        self.store = TeamStore(
            solo_root, TeamIdentity("solo", "a" * 64, "fake", "max", max_members=1)
        )
        self.store.register("pi")
        self.api = TeamService(self.store)
        with self.assertRaises(PermissionError):
            self.store.register("worker", "pi")
        task = self.call(
            "pi",
            "delegate",
            assignee="pi",
            goal="Do my own synthetic study",
            acceptance="Same evidence requirements; no independent validation claim",
            references=[],
        )
        self.assertEqual(task["assignment_kind"], "self")
        task = self.call("pi", "claim", task_id=task["id"], expected_version=task["version"])
        m = self.memory(actor="pi")
        skill = self.skill(actor="pi", memory_refs=[ref("memory", m)])
        skill = self.skill(
            actor="pi",
            expected_version=skill["version"],
            status="trial",
            memory_refs=[ref("memory", m)],
        )
        context = self.call(
            "pi",
            "context",
            task_id=task["id"],
            expected_version=task["version"],
            references=[ref("skill", skill), ref("memory", m)],
            purpose="Own planned execution",
        )
        use = self.call(
            "pi",
            "prepare_use",
            task_id=task["id"],
            expected_version=task["version"],
            skill_ref=ref("skill", skill),
            context_id=context["id"],
        )
        result = self.api.attest_execution(
            use_id=use["id"],
            receipt_id="synthetic-receipt",
            artifact_sha256="b" * 64,
            status="succeeded",
            output_refs=[],
        )
        self.assertEqual(result["status"], "succeeded")
        delivered = self.call(
            "pi",
            "submit",
            task_id=task["id"],
            expected_version=task["version"],
            summary="Self-task completed; synthetic only",
            references=[ref("use", result)],
        )
        accepted = self.call(
            "pi",
            "review",
            task_id=task["id"],
            expected_version=delivered["version"],
            decision="accept",
            feedback="Self-review is not an independent verification",
        )
        self.assertIn("not independent", accepted["acceptance_means"])
        self.api = TeamService(TeamStore(solo_root))
        self.assertEqual(len(self.api.store.status()["agents"]), 1)
        final = self.call(
            "pi", "finish", summary="Solo synthetic result", references=[ref("memory", m)]
        )
        self.assertFalse(final["scientifically_verified"])
        with self.assertRaises(PermissionError):
            TeamStore(solo_root, TeamIdentity("solo", "a" * 64, "fake", "max", max_members=4))

    def test_task_cancellation_resolves_question_without_faking_answer(self):
        task = self.task()
        q = self.call(
            "worker", "ask", task_id=task["id"], expected_version=1, question="x?", impact="y"
        )
        self.call(
            "pi", "cancel", task_id=task["id"], expected_version=2, reason="Cancelled fixture"
        )
        self.assertEqual(self.read("question", q)["status"], "cancelled")
        with self.assertRaises(Conflict):
            self.call("pi", "answer", question_id=q["id"], expected_version=1, answer="late")

    def test_tools_cannot_forge_actor_or_call_trusted_environment(self):
        for op in ("register", "import_evidence", "attest_execution", "connection"):
            with self.assertRaises(ValueError):
                self.api.call("worker", op, {})
        with self.assertRaises(ValueError):
            self.api.call("worker", "messages", {"actor": "pi"})
        with self.assertRaises(PermissionError):
            self.api.call("unknown", "messages", {})
        with patch.object(
            self.api, "messages", side_effect=TypeError("internal programming error")
        ):
            with self.assertRaisesRegex(TypeError, "internal"):
                self.api.call("worker", "messages", {})

    def test_evidence_is_immutable_and_final_feedback_is_rejected(self):
        first = self.evidence()
        self.assertEqual(self.evidence(), first)
        with self.assertRaises(Conflict):
            self.api.import_evidence(
                evidence_id="obs",
                title="changed",
                category="observation",
                artifact_sha256="c" * 64,
                provenance={"source_id": "fixture", "role": "synthetic"},
                summary="rewritten",
            )
        with self.assertRaises(PermissionError):
            self.api.import_evidence(
                evidence_id="eval",
                title="eval",
                category="observation",
                artifact_sha256="d" * 64,
                provenance={"source_id": "fixture", "role": "final_evaluation"},
                summary="forbidden",
            )

    def test_memory_needs_evidence_and_principal_assessment(self):
        m = self.memory()
        with self.assertRaises(ValueError):
            self.memory("pi", expected_version=1, status="supported")
        observation = self.evidence()
        with self.assertRaises(PermissionError):
            self.memory(
                expected_version=1, status="supported", supports=[ref("evidence", observation)]
            )
        supported = self.memory(
            "pi", expected_version=1, status="supported", supports=[ref("evidence", observation)]
        )
        self.assertFalse(supported["scientifically_verified"])
        self.assertEqual(self.api.read("worker", kind="memory", record_id="m", record_version=1), m)
        with self.assertRaises(ValueError):
            self.memory("pi", expected_version=2, status="contested")
        disputed = self.memory(
            "pi", expected_version=2, status="contested", counters=[ref("evidence", observation)]
        )
        self.assertEqual(disputed["status"], "contested")

    def test_archived_memory_is_historical_provenance_not_active_context(self):
        old = self.memory()
        self.memory("pi", expected_version=1, status="archived")
        with self.assertRaises(Conflict):
            self.memory(expected_version=2)
        task = self.task()
        with self.assertRaises(Conflict):
            self.call(
                "worker",
                "context",
                task_id=task["id"],
                expected_version=1,
                references=[ref("memory", old)],
                purpose="Attempt to use archived memory",
            )
        revised = self.memory(memory_id="new", derived_from=[ref("memory", old)])
        self.assertEqual(revised["derived_from"], [ref("memory", old)])

    def test_draft_skill_cannot_execute_and_only_principal_authorizes(self):
        draft = self.skill()
        task = self.claim(self.task())
        with self.assertRaises(PermissionError):
            self.intent(task, draft)
        with self.assertRaises(PermissionError):
            self.skill(expected_version=1, status="trial")
        trial = self.skill("pi", expected_version=1, status="trial")
        _, use = self.intent(task, trial)
        self.assertEqual(use["status"], "intended")
        self.assertEqual(trial["effectiveness"], "unproven")

    def test_retrieval_intent_and_execution_are_distinct(self):
        task = self.claim(self.task())
        ctx, use = self.intent(task, self.trial())
        self.assertIn("not proof", ctx["meaning"])
        self.assertIsNone(use["execution_receipt"])
        with self.assertRaises(PermissionError):
            self.api.attest_execution(
                use_id=use["id"],
                receipt_id="run1",
                artifact_sha256="c" * 64,
                status="succeeded",
                output_refs=[],
            )
        args = dict(
            use_id=use["id"],
            receipt_id="run1",
            artifact_sha256="b" * 64,
            status="unknown",
            output_refs=[],
        )
        observed = self.api.attest_execution(**args)
        self.assertEqual(observed["status"], "unknown")
        self.assertEqual(self.api.attest_execution(**args), observed)
        with self.assertRaises(Conflict):
            self.api.attest_execution(**{**args, "status": "succeeded"})

    def test_one_execution_receipt_cannot_credit_two_uses(self):
        task, skill = self.claim(self.task()), self.trial()
        _, first = self.intent(task, skill)
        _, second = self.intent(task, skill)
        args = dict(
            receipt_id="unique", artifact_sha256="b" * 64, status="succeeded", output_refs=[]
        )
        self.api.attest_execution(use_id=first["id"], **args)
        with self.assertRaises(Conflict):
            self.api.attest_execution(use_id=second["id"], **args)
        self.assertEqual(self.read("use", second)["status"], "intended")

    def test_revoked_skill_and_stale_task_context_cannot_start_execution(self):
        task, skill = self.claim(self.task()), self.trial()
        ctx, _ = self.intent(task, skill)
        self.skill("pi", expected_version=2, status="revoked")
        with self.assertRaises(Conflict):
            self.call(
                "worker",
                "prepare_use",
                task_id=task["id"],
                expected_version=task["version"],
                skill_ref=ref("skill", skill),
                context_id=ctx["id"],
            )
        with self.assertRaises(Conflict):
            self.skill("pi", expected_version=3, status="trial")

    def test_unsettled_execution_prevents_finish_even_if_task_cancelled(self):
        task = self.claim(self.task())
        self.intent(task, self.trial())
        self.call(
            "pi", "cancel", task_id=task["id"], expected_version=task["version"], reason="stop"
        )
        with self.assertRaises(Conflict):
            self.call("pi", "finish", summary="cannot hide unresolved execution", references=[])

    def test_archived_dependency_blocks_use_but_does_not_block_skill_revocation(self):
        m = self.memory()
        self.skill(memory_refs=[ref("memory", m)])
        skill = self.skill("pi", expected_version=1, status="trial", memory_refs=[ref("memory", m)])
        self.memory("pi", expected_version=1, status="archived")
        with self.assertRaises(Conflict):
            self.intent(self.claim(self.task()), skill)
        revoked = self.skill(
            "pi", expected_version=2, status="revoked", memory_refs=[ref("memory", m)]
        )
        self.assertEqual(revoked["status"], "revoked")

    def test_metadata_query_has_exact_filters_and_bounded_whole_record_pages(self):
        for i in range(8):
            self.memory(memory_id=f"m{i}", applicability={"fuel": "synthetic", "large": "x" * 3500})
        page = self.api.list(
            "worker",
            kind="memory",
            limit=40,
            applicability={"fuel": "synthetic"},
            status="hypothesis",
        )
        self.assertLess(len(encode(page).encode()), 16000)
        self.assertTrue(page["next_after"])
        self.assertLess(len(page["rows"]), 8)
        rest = self.api.list("worker", kind="memory", after=page["next_after"], limit=40)
        self.assertFalse(set(v["id"] for v in page["rows"]) & set(v["id"] for v in rest["rows"]))
        self.assertEqual(
            self.api.list("worker", kind="memory", applicability={"fuel": "absent"})["rows"], []
        )
        self.assertNotIn("claim", page["rows"][0])

    def test_inbox_ack_is_own_monotonic_and_durable(self):
        self.task()
        messages = self.api.messages("worker")
        seq = messages["next_after"]
        with self.assertRaises(PermissionError):
            self.call("peer", "ack", sequence=seq)
        self.call("worker", "ack", sequence=seq)
        self.api = TeamService(TeamStore(self.root))
        self.assertEqual(self.api.messages("worker")["messages"], [])
        self.assertEqual(self.api.messages("worker", after=0)["messages"], messages["messages"])


if __name__ == "__main__":
    unittest.main()
