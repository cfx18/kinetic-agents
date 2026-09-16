import copy
import json
from pathlib import Path
import tempfile
import unittest

from kinetic_agents.core.store import Store
import kinetic_agents.observability.expert as expert
import kinetic_agents.observability.review as report
import kinetic_agents.observability.notebook_tools as review_tools
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.contracts import Conflict
from kinetic_agents.team.store import TeamStore
from kinetic_agents.team.service import TeamService


class ExpertReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cfx-expert-test-")
        self.root = Path(self.temp.name)
        runtime = Store(self.root / "runtime.sqlite")
        runtime.initialize(dict(api_usd=1, wall_seconds=60, model="fixture", effort="max"))
        self.service = TeamService(
            TeamStore(self.root / "team", TeamIdentity("fixture", "a" * 64, "fixture", "max", 2))
        )
        self.service.store.register("principal")
        self.service.store.register("child", "principal")
        base = self.root / "native/codex/sessions"
        base.mkdir(parents=True)
        events = [
            dict(type="session_meta", payload=dict(id="principal")),
            dict(
                type="response_item",
                payload=dict(
                    type="function_call",
                    name="exec_command",
                    call_id="q",
                    arguments=json.dumps(dict(cmd="rg 'accepted' trace.json")),
                ),
            ),
            dict(
                type="response_item",
                payload=dict(type="function_call_output", call_id="q", output="fixture returned"),
            ),
            dict(
                type="response_item",
                payload=dict(
                    type="message",
                    role="assistant",
                    phase="commentary",
                    content=[dict(text="合成测试：据此选择下一步。")],
                ),
            ),
        ]
        (base / "fixture.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        self.search = report.search_report(self.root)
        prefix = "native/codex/sessions/fixture.jsonl:"
        card = dict(
            id="S01",
            title="合成决策卡",
            stage="验证",
            actor="主Agent",
            when="合成时间",
            known_then="合成已知信息",
            reason_summary="合成公开判断",
            reason_basis="public_statement",
            decision="合成选择",
            action="合成实际动作",
            outcome="合成观察结果",
            assessment="合成待核意见",
            uncertainty="合成未知",
            question="合成问题？",
            priority="high",
            sources=[prefix + "2", prefix + "4"],
        )
        query = dict(
            id="SQ01",
            cards=["S01"],
            purpose="合成问题",
            method="本地检索",
            request="accepted",
            returned="合成返回",
            agent_takeaway="合成重点",
            used_in="合成影响",
            takeaway_basis="public_statement",
            sources=[prefix + "2", prefix + "3", prefix + "4"],
        )
        self.bundle = dict(
            schema=expert.SCHEMA,
            contract_hash=self.search["state"]["contract_hash"],
            title="合成中文复盘",
            overview="仅用于单元测试",
            cards=[card],
            queries=[query],
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_chinese_front_page_and_search_isolation(self):
        first = report.publish(self.root)
        second = report.publish(self.root, expert_cards=self.bundle)
        self.assertEqual(first["search_view_sha256"], second["search_view_sha256"])
        self.assertTrue(second["expert_review_ready"])
        folder = self.root / "review" / second["directory"]
        page = (folder / "EXPERIMENT_REPORT.md").read_text()
        for word in (
            "当时已知",
            "Agent当时的判断",
            "作出的决策",
            "实际动作",
            "后来结果",
            "请你判断",
            "实际关键词／筛选条件",
            "Agent明确看重什么",
            "如何影响下一步",
        ):
            self.assertIn(word, page)
        self.assertNotIn("```json", page)
        self.assertNotIn("合成待核意见", (folder / "SEARCH_REVIEW.json").read_text())
        self.assertIn("合成待核意见", (folder / "report.json").read_text())
        self.assertIn("合成中文复盘", (self.root / "review/EXPERIMENT_REPORT.md").read_text())
        self.assertIn("versions/", (self.root / "review/EXPERIMENT_REPORT.md").read_text())

    def test_unreviewed_is_neither_negative_nor_positive(self):
        template = expert.feedback_template(self.bundle)
        self.assertFalse(template["training_eligible"])
        row = template["items"][0]
        self.assertEqual(row["review_status"], "unreviewed")
        self.assertIsNone(row["verdict"])
        self.assertIsNone(row["expert_raw_text"])
        self.assertEqual(row["card_sha256"], expert.sha(self.bundle["cards"][0]))

    def test_sources_and_rationale_must_be_resolvable(self):
        variants = []
        for field, value in [("contract_hash", "b" * 64), ("title", ""), ("queries", "invalid")]:
            bundle = copy.deepcopy(self.bundle)
            bundle[field] = value
            variants.append(bundle)
        for key, value in [
            ("sources", ["native/not-real:1"]),
            ("sources", ["../escape"]),
            ("sources", ["native/codex/sessions/fixture.jsonl:2"]),
            ("id", None),
        ]:
            bundle = copy.deepcopy(self.bundle)
            bundle["cards"][0][key] = value
            variants.append(bundle)
        for key, value in [
            ("sources", ["native/codex/sessions/fixture.jsonl:2"]),
            ("cards", ["S99"]),
            ("takeaway_basis", "guessed"),
        ]:
            bundle = copy.deepcopy(self.bundle)
            bundle["queries"][0][key] = value
            variants.append(bundle)
        for bundle in variants:
            with self.subTest(bundle=bundle), self.assertRaises((ValueError, PermissionError)):
                expert.validate(bundle, {"search": self.search})

    def test_unknown_rationale_may_not_have_public_statement(self):
        self.bundle["cards"][0].update(
            reason_basis="unknown",
            reason_summary="未说明",
            sources=["native/codex/sessions/fixture.jsonl:2"],
        )
        self.bundle["queries"][0].update(
            takeaway_basis="unknown",
            agent_takeaway="未说明",
            sources=[
                "native/codex/sessions/fixture.jsonl:2",
                "native/codex/sessions/fixture.jsonl:3",
            ],
        )
        expert.validate(self.bundle, {"search": self.search})

    def feedback(self, **changes):
        return dict(
            feedback_id="fixture_1",
            card_id="S01",
            verdict="mixed",
            error_layers=["validation"],
            expert_raw_text="合成专家意见，不是真实用户反馈。",
            preferred_action="合成动作",
            applicability="合成范围",
            basis="retrospective_with_results",
            source_message="synthetic-test",
            **changes
        )

    def test_feedback_idempotent_version_bound_not_training_label(self):
        a = expert.record_feedback(self.root / "feedback", self.bundle, **self.feedback())
        b = expert.record_feedback(self.root / "feedback", self.bundle, **self.feedback())
        self.assertEqual(a, b)
        self.assertFalse(a["training_eligible"])
        self.assertEqual(a["review_status"], "recorded_not_validated")
        self.assertEqual(a["packet_sha256"], expert.sha(self.bundle))
        self.assertEqual(a["contract_hash"], self.bundle["contract_hash"])
        self.assertEqual(a["expert_raw_text"], self.feedback()["expert_raw_text"])
        with self.assertRaises(PermissionError):
            expert.record_feedback(
                self.root / "feedback",
                self.bundle,
                **{**self.feedback(), "expert_raw_text": "合成修订需新编号"}
            )
        new = expert.record_feedback(
            self.root / "feedback",
            self.bundle,
            **{**self.feedback(), "feedback_id": "fixture_2", "expert_raw_text": "合成新版本"}
        )
        self.assertEqual(new["expert_raw_text"], "合成新版本")

    def test_feedback_rejects_missing_expert_statement_and_bad_scope(self):
        for key, value in [
            ("card_id", "S99"),
            ("basis", "guessed"),
            ("expert_raw_text", ""),
            ("source_message", ""),
            ("error_layers", [{}]),
            ("feedback_id", "../escape"),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                expert.record_feedback(
                    self.root / "feedback", self.bundle, **{**self.feedback(), key: value}
                )

    def test_partial_expert_reply_preserved_without_invented_labels(self):
        value = expert.record_feedback(
            self.root / "feedback",
            self.bundle,
            **{
                **self.feedback(),
                "verdict": None,
                "error_layers": [],
                "preferred_action": None,
                "applicability": None,
            }
        )
        self.assertIsNone(value["verdict"])
        self.assertIsNone(value["preferred_action"])
        self.assertIsNone(value["applicability"])
        self.assertFalse(value["training_eligible"])

    def query_args(self):
        return dict(
            query_id="query_fixture",
            expected_version=0,
            purpose="合成检索目的",
            method="本地文件",
            request="status=accepted",
            returned_summary="合成返回",
            important_findings="合成重点与理由",
            importance_status="identified",
            decision_effect="合成后续动作",
            source_addresses=["trace.json:2"],
            references=[],
        )

    def test_query_receipt_versions_readable_by_team_and_exported(self):
        args = self.query_args()
        a = review_tools.record_query(self.service, "principal", args, "q1")
        self.assertEqual(a, review_tools.record_query(self.service, "principal", args, "q1"))
        updated = {**args, "expected_version": 1, "decision_effect": "合成修订"}
        review_tools.record_query(self.service, "principal", updated, "q2")
        with self.assertRaises(Conflict):
            review_tools.record_query(self.service, "principal", updated, "q3")
        old = review_tools.read(
            self.service, "child", dict(section="queries", record_id="query_fixture", version=1)
        )
        self.assertEqual(
            json.loads(old["record"]["content"])["decision_effect"], args["decision_effect"]
        )
        search = report.search_report(self.root)
        self.assertEqual(search["coverage"]["explicit_query_versions"], 2)
        self.assertFalse(search["queries"][0]["source_addresses_verified"])
        self.assertEqual(search["queries"][0]["epistemic_status"], "agent_statement_not_verified")

    def test_query_rejects_foreign_actor_false_reference_and_missing_fields(self):
        with self.assertRaises(PermissionError):
            review_tools.record_query(self.service, "foreign", self.query_args(), "q1")
        for key, value in [
            ("request", ""),
            ("source_addresses", []),
            ("importance_status", "trusted"),
            ("query_id", "decision_bad"),
        ]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                review_tools.record_query(
                    self.service, "principal", {**self.query_args(), key: value}, "bad"
                )
        with self.assertRaises(KeyError):
            review_tools.record_query(
                self.service,
                "principal",
                {
                    **self.query_args(),
                    "references": [dict(kind="evidence", id="not_real", version=1)],
                },
                "bad_ref",
            )

    def test_query_tool_registered_and_prompt_does_not_modify_old_task(self):
        from kinetic_agents.research.environment import PairEnvironment
        from kinetic_agents.research.environment import SPECS
        from kinetic_agents.research.policy import TeamPolicy
        from kinetic_agents.research.policy import runtime_facts

        tool = next(s for s in SPECS if s["name"] == "research_record_query")
        self.assertEqual(set(tool["parameters"]["required"]), set(self.query_args()))
        self.assertTrue(callable(PairEnvironment.record_query))
        self.assertEqual(TeamPolicy.version, "6")
        self.assertIn("each substantive", runtime_facts(1, "fixture"))


if __name__ == "__main__":
    unittest.main()
