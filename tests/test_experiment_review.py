import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kinetic_agents.core.store import Store
import kinetic_agents.observability.review as review
import kinetic_agents.observability.notebook_tools as review_tools
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.contracts import Conflict
from kinetic_agents.team.store import TeamStore
from kinetic_agents.team.service import TeamService


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cfx-review-test-")
        self.root = Path(self.temp.name)
        self.runtime = Store(self.root / "runtime.sqlite")
        self.runtime.initialize(dict(api_usd=50, wall_seconds=1000, model="fixture", effort="max"))
        self.team = TeamService(
            TeamStore(
                self.root / "team", TeamIdentity("review-fixture", "a" * 64, "fixture", "max", 4)
            )
        )
        self.team.store.register("principal")
        self.team.store.register("child", "principal")
        self.write("result.json", dict(status="COMPLETED", scientifically_verified=False))
        self.write("local_cpu.json", dict(status="SETTLED", cpu_seconds=1))

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def decision(self, **changes):
        return dict(
            decision_id="decision_grid",
            expected_version=0,
            stage="case_selection",
            decision="Test additional flames",
            reason_summary="Ignition alone does not check flame speed",
            alternatives=["Keep current grid"],
            references=[],
            outcome="pending",
            revisit_when="Flame results arrive",
            **changes
        )

    def native(self):
        folder = self.root / "native/codex/sessions/2026/09/14"
        folder.mkdir(parents=True)
        events = [
            {"type": "session_meta", "payload": {"id": "principal"}},
            {
                "type": "response_item",
                "payload": {"type": "reasoning", "summary": "HIDDEN_SENTINEL"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "channel": "analysis",
                    "content": [{"text": "HIDDEN_SENTINEL"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "a",
                    "arguments": json.dumps(
                        {"cmd": "python check.py", "api_key": "sk-VERYSECRETVALUE123456"}
                    ),
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "a", "output": "exit 1"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "channel": "commentary",
                    "content": [{"text": "The check failed; inspect its output."}],
                },
            },
        ]
        path = folder / "fixture.jsonl"
        path.write_text(
            "".join(json.dumps({"timestamp": "2026-09-14T00:00:00Z", **e}) + "\n" for e in events)
        )

    def endpoint(self):
        score = {"parent": {"all610": {"coverage": 1, "full_pool_mean_abs_sigma": 2}}}
        board = {
            "rows": [dict(label="parent", species=111, reactions=784, coverage=1, mean_abs_sigma=2)]
        }
        result = dict(
            rows=[
                dict(
                    label="candidate",
                    case_id="SEALED_SENTINEL",
                    status="input_incompatible",
                    missing_species=["X"],
                )
            ],
            scores=score,
            leaderboard=board,
        )
        path = self.write("endpoint/evaluation_result.json", result)
        return self.write(
            "endpoint/scorecard.json",
            dict(
                status="COMPLETE",
                scores=score,
                leaderboard=board,
                result_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                evaluation_allocated_core_seconds=100,
            ),
        )

    def test_native_is_observable_actions_not_hidden_reasoning(self):
        self.native()
        report = review.search_report(self.root)
        text = review.encoded(report)
        self.assertNotIn("HIDDEN_SENTINEL", text)
        self.assertNotIn("sk-VERYSECRETVALUE123456", text)
        self.assertEqual(len(report["native"]["actions"]), 1)
        action = report["native"]["actions"][0]
        self.assertIsNone(action["rationale"])
        self.assertEqual(action["outcome"], "output_recorded_not_scientific_success")
        self.assertEqual(action["output_source"]["line"], 5)
        self.assertEqual(len(action["outputs"]), 1)
        self.assertEqual(action["outputs"][0]["line"], 5)
        self.assertEqual(action["request_observation"]["label"], "运行Python程序")
        self.assertEqual(report["native"]["ignored_reasoning_records"], 2)

    def test_native_phase_commentary_and_final_answer_are_public(self):
        self.native()
        path = self.root / "native/codex/sessions/2026/09/14/fixture.jsonl"
        with path.open("a") as stream:
            for phase in ("commentary", "final_answer", "analysis"):
                stream.write(
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "phase": phase,
                                "content": [{"text": "visible-" + phase}],
                            },
                        }
                    )
                    + "\n"
                )
        report = review.search_report(self.root)
        statements = review.encoded(report["native"]["statements"])
        self.assertIn("visible-commentary", statements)
        self.assertIn("visible-final_answer", statements)
        self.assertNotIn("visible-analysis", statements)

    def test_step_exports_every_action_and_statement_without_evaluation_leak(self):
        self.native()
        self.endpoint()
        first = review.publish(self.root)
        second = review.publish(self.root, endpoint=True)
        self.assertEqual(first["search_view_sha256"], second["search_view_sha256"])
        folder = self.root / "review" / second["directory"]
        steps = json.loads((folder / "STEP_REVIEW.json").read_text())
        self.assertEqual(steps["counts"]["actions"], 1)
        self.assertEqual(steps["counts"]["public_statements"], 1)
        self.assertNotIn("HIDDEN_SENTINEL", (folder / "STEP_PAGE_001.md").read_text())
        self.assertNotIn("sk-VERYSECRETVALUE123456", (folder / "STEP_REVIEW.json").read_text())
        self.assertNotIn("SEALED_SENTINEL", (folder / "STEP_REVIEW.json").read_text())
        self.assertTrue((self.root / "review/STEP_BY_STEP.md").exists())

    def test_step_outputs_scrub_json_credentials_and_preserve_all_returns(self):
        self.native()
        path = self.root / "native/codex/sessions/2026/09/14/fixture.jsonl"
        with path.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": dict(
                            type="function_call_output",
                            call_id="a",
                            output=json.dumps({"api_key": "DO_NOT_EXPORT_FIXTURE", "status": "ok"}),
                        ),
                    }
                )
                + "\n"
            )
        snapshot = review.search_report(self.root)
        self.assertNotIn("DO_NOT_EXPORT_FIXTURE", review.encoded(snapshot))
        self.assertEqual(len(snapshot["native"]["actions"][0]["outputs"]), 2)

    def test_inherited_parent_metadata_does_not_relabel_child(self):
        self.native()
        path = self.root / "native/codex/sessions/2026/09/14/fixture.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[0]["payload"] = {"id": "child", "parent_thread_id": "principal"}
        events.insert(1, {"type": "session_meta", "payload": {"id": "principal"}})
        path.write_text("".join(json.dumps(e) + "\n" for e in events))
        native = review.search_report(self.root)["native"]
        self.assertEqual([s["id"] for s in native["sessions"]], ["child"])
        self.assertEqual(native["actions"][0]["actor"], "child")
        self.assertEqual(native["statements"][0]["actor"], "child")

    def test_unsupported_actions_are_exposed_as_coverage_gap(self):
        self.native()
        path = self.root / "native/codex/sessions/2026/09/14/fixture.jsonl"
        with path.open("a") as stream:
            stream.write(
                json.dumps({"type": "response_item", "payload": {"type": "future_tool_call"}})
                + "\n"
            )
        report = review.search_report(self.root)
        self.assertEqual(report["coverage"]["unhandled_native_call_types"], {"future_tool_call": 1})

    def test_decision_versions_and_both_actors_read_exact_records(self):
        first = review_tools.record(self.team, "principal", self.decision(), "req1")
        second_args = {**self.decision(), "expected_version": 1, "outcome": "observed failure"}
        second = review_tools.record(self.team, "principal", second_args, "req2")
        self.assertEqual((first["version"], second["version"]), (1, 2))
        old = review_tools.read(self.team, "child", dict(record_id="decision_grid", version=1))
        self.assertEqual(json.loads(old["record"]["content"])["outcome"], "pending")
        report = review.search_report(self.root, include_native=False)
        self.assertEqual(len(report["decisions"]), 2)
        self.assertEqual(report["decisions"][1]["epistemic_status"], "agent_statement_not_verified")
        with self.assertRaises(Conflict):
            review_tools.record(self.team, "principal", second_args, "req3")

    def test_same_request_id_idempotent_no_new_decision(self):
        first = review_tools.record(self.team, "principal", self.decision(), "req1")
        self.assertEqual(
            first, review_tools.record(self.team, "principal", self.decision(), "req1")
        )

    def test_no_foreign_identity_or_false_evidence(self):
        with self.assertRaises(PermissionError):
            review_tools.read(self.team, "foreign", {})
        with self.assertRaises(KeyError):
            review_tools.record(
                self.team,
                "principal",
                {
                    **self.decision(),
                    "references": [{"kind": "evidence", "id": "fake", "version": 1}],
                },
                "req1",
            )

    def test_read_no_endpoint_path_run_or_invalid_cursor(self):
        for args in (
            {"section": "endpoint"},
            {"path": "../endpoint"},
            {"run_id": "other"},
            {"limit": 0},
            {"version": 2},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                review_tools.read(self.team, "principal", args)

    def test_agent_view_identical_with_or_without_endpoint_and_no_db_writes(self):
        self.native()
        self.endpoint()
        before = (self.root / "runtime.sqlite").read_bytes()
        first = review.publish(self.root)
        second = review.publish(self.root, endpoint=True)
        self.assertEqual(first["search_view_sha256"], second["search_view_sha256"])
        folder = self.root / "review" / second["directory"]
        self.assertNotIn("SEALED_SENTINEL", (folder / "SEARCH_REVIEW.json").read_text())
        self.assertIn("SEALED_SENTINEL", (folder / "report.json").read_text())
        self.assertEqual(before, (self.root / "runtime.sqlite").read_bytes())
        self.assertNotEqual(first["revision"], second["revision"])

    def test_idempotent_publish_and_no_overwrite_of_revisions(self):
        a = review.publish(self.root, include_native=False)
        b = review.publish(self.root, include_native=False)
        self.assertEqual(a["revision"], b["revision"])
        path = self.root / "review" / a["directory"] / "EXPERIMENT_REPORT.md"
        path.write_text("tampered")
        with self.assertRaises(PermissionError):
            review.publish(self.root, include_native=False)

    def test_missing_records_are_explicit_not_success(self):
        report = review.search_report(self.root)
        self.assertEqual(report["decisions"], [])
        self.assertIn(
            {"source": "native/codex/sessions", "reason": "missing"},
            report["coverage"]["sources_missing_or_limited"],
        )
        self.assertEqual(report["coverage"]["explanation_completeness"], "not_guaranteed")

    def test_hash_and_scorecard_tamper_rejected(self):
        path = self.endpoint()
        card = json.loads(path.read_text())
        card["scores"] = {}
        path.write_text(json.dumps(card))
        with self.assertRaises(ValueError):
            review.publish(self.root, endpoint=True, include_native=False)

    def test_analysis_not_automatically_mixed_into_agent_notebook(self):
        findings = [
            dict(
                title="Review",
                analysis="EXPERT_ONLY_SENTINEL",
                epistemic_status="hypothesis",
                sources=["runtime.sqlite#events/1"],
                next_check="Need a controlled check",
            )
        ]
        out = review.publish(self.root, include_native=False, expert_findings=findings)
        folder = self.root / "review" / out["directory"]
        self.assertNotIn("EXPERT_ONLY_SENTINEL", (folder / "SEARCH_REVIEW.json").read_text())
        self.assertIn("EXPERT_ONLY_SENTINEL", (folder / "TECHNICAL_AUDIT.md").read_text())
        self.assertIn("待中文整理", (folder / "EXPERIMENT_REPORT.md").read_text())
        self.assertFalse(out["expert_review_ready"])

    def test_symlink_and_scientist_directory_rejected(self):
        (self.root / "linked").symlink_to(self.root / "runtime.sqlite")
        with self.assertRaises(PermissionError):
            review.plain(self.root / "linked")
        with self.assertRaises(PermissionError):
            review.publish(self.root, output=self.root / "work/review", include_native=False)

    def test_record_invalid_fields_rejected(self):
        for changes in (
            {"stage": "unknown"},
            {"decision_id": "not_namespaced"},
            {"reason_summary": ""},
            {"alternatives": "not_list"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                review_tools.record(self.team, "principal", {**self.decision(), **changes}, "bad")

    def test_terminal_owner_reports_without_submission_and_no_evaluation(self):
        with patch.object(review, "attempt") as report, patch(
            "kinetic_agents.evaluation.submission.execute"
        ) as execute:
            review.finish(self.root, None, evaluate=False)
        execute.assert_not_called()
        self.assertEqual(report.call_count, 2)

    def test_endpoint_failure_still_attempts_report_and_preserves_failure(self):
        with patch.object(review, "attempt") as report, patch(
            "kinetic_agents.evaluation.submission.execute", side_effect=RuntimeError("fixture")
        ):
            with self.assertRaises(RuntimeError):
                review.finish(self.root, self.root, evaluate=True)
        self.assertEqual([c.kwargs["endpoint"] for c in report.call_args_list], [False, True])

    def test_timeout_records_retry_not_success(self):
        import subprocess

        with patch.object(
            review.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 60)
        ):
            value = review.attempt(self.root, endpoint=False)
        self.assertEqual(value["status"], "PENDING_RETRY")
        self.assertEqual(json.loads((self.root / "result.json").read_text())["status"], "COMPLETED")

    def test_coordinator_postrun_uses_existing_cap_and_no_unsubmitted_evaluation(self):
        from kinetic_agents.execution.finalize import postrun_review

        with patch(
            "kinetic_agents.execution.finalize.cpu_run",
            return_value={"status": "SETTLED", "cpu_seconds": 1},
        ) as owner:
            postrun_review(self.root, {"status": "SETTLED"}, {})
            self.assertNotIn("--evaluate", owner.call_args.args[0])
            self.assertEqual(owner.call_args.args[2:4], (3600, 2))
            postrun_review(self.root, {"status": "SETTLED"}, {"submission": {"locked": True}})
            self.assertIn("--evaluate", owner.call_args.args[0])

    def test_postrun_owner_failure_preserves_science_and_exposes_pending(self):
        from kinetic_agents.execution.finalize import postrun_review

        with patch(
            "kinetic_agents.execution.finalize.cpu_run", side_effect=RuntimeError("fixture")
        ):
            postrun_review(self.root, {"status": "SETTLED"}, {})
        self.assertEqual(
            json.loads((self.root / "review_build_status.json").read_text())["status"],
            "PENDING_RETRY",
        )
        self.assertEqual(json.loads((self.root / "result.json").read_text())["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
