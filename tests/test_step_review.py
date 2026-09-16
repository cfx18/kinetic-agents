import copy
import json
import re
import unittest

import kinetic_agents.observability.steps as step


class StepReviewTests(unittest.TestCase):
    def search(self, count=3):
        source = "native/codex/sessions/fixture.jsonl"
        actions = []
        for i in range(count):
            actions.append(
                dict(
                    actor="child" if i % 2 else "principal",
                    source=source,
                    line=i * 3 + 1,
                    at=f"2026-09-14T01:{i//60:02d}:{i%60:02d}Z",
                    tool="exec_command",
                    request_excerpt="{}",
                    request_observation=step.request_observation(
                        "exec_command", {"cmd": "rg 'accepted' trace.json"}
                    ),
                    outputs=[
                        dict(
                            source=source,
                            line=i * 3 + 2,
                            at=f"2026-09-14T02:{i//60:02d}:{i%60:02d}Z",
                            observation=step.output_observation(
                                "Process exited with code 1\nOutput:\nfixture error"
                            ),
                        )
                    ],
                )
            )
        return dict(
            run_id="team-fixture",
            run_root="/fixture/run",
            state={"contract_hash": "a" * 64},
            coverage={},
            native=dict(
                actions=actions,
                statements=[
                    dict(
                        actor="principal",
                        source=source,
                        line=999,
                        at="2026-09-14T00:00:00Z",
                        text="公开说明fixture",
                    )
                ],
                sessions=[dict(id="principal"), dict(id="child", parent_thread_id="principal")],
            ),
        )

    def test_every_action_and_statement_once_no_filtering_and_pagination(self):
        search = self.search(57)
        view = step.build(search)
        self.assertEqual(view["counts"], dict(actions=57, public_statements=1, total=58))
        docs = step.documents(view)
        pages = [name for name in docs if name.startswith("STEP_PAGE_")]
        self.assertEqual(len(pages), 3)
        content = "\n".join(docs[p] for p in sorted(pages))
        for row in view["rows"]:
            self.assertEqual(content.count(f'<a id="{row["id"].lower()}"></a>'), 1)
        self.assertEqual(content.count("## T"), 58)
        self.assertEqual(len(re.findall(r"\|\[T\d+\]", docs["STEP_SIGNALS.md"])), 57)

    def test_same_actor_context_only_not_inferred_rationale(self):
        rows = step.build(self.search())["rows"]
        self.assertEqual(rows[1]["prior_public_step"], rows[0]["id"])
        self.assertIsNone(rows[2]["prior_public_step"])
        self.assertEqual(rows[1]["rationale_status"], "not_explicitly_linked")
        self.assertEqual(rows[2]["actor_name"], "研究员1")

    def test_late_returns_stay_timed_and_missing_returns_are_unknown(self):
        search = self.search()
        search["native"]["actions"][1]["outputs"] = []
        docs = step.documents(step.build(search))
        text = docs["STEP_PAGE_001.md"]
        self.assertIn("02:00:00Z（不提前", text)
        self.assertIn("未观察到返回；不推断成功或失败", text)

    def test_signal_is_execution_not_word_matching_or_science(self):
        self.assertFalse(
            step.output_observation(
                "Process exited with code 0\nOutput:\nTraceback: sample in docs"
            )["signal"]
        )
        self.assertFalse(
            step.output_observation({"status": "complete", "scientifically_verified": False})[
                "signal"
            ]
        )
        self.assertTrue(
            step.output_observation({"error_type": "ValueError", "message": "fixture"})["signal"]
        )
        self.assertTrue(step.output_observation("Process exited with code -9\nOutput:\n")["signal"])
        self.assertIn(
            "仍在运行",
            step.output_observation("Process running with session ID 123\nOutput:\n")["status"],
        )

    def test_exact_search_keywords_and_file_mentions_are_not_motives(self):
        obs = step.request_observation(
            "exec_command",
            dict(cmd='curl "https://example.test/search?q=DRGEP+mechanism+reduction&per_page=5"'),
        )
        self.assertIn("URL参数q=DRGEP mechanism reduction", obs["keywords"])
        obs = step.request_observation(
            "exec_command", dict(cmd="rg -n 'status=accepted' trace.json")
        )
        self.assertIn("status=accepted", obs["keywords"][0])
        self.assertIn("不等于全部已访问", " ".join(obs["details"]))
        self.assertEqual(obs["interpretation"], "literal_syntax_hints_not_scientific_rationale")
        self.assertIn("无显式参数", step.request_observation("research_budget", {})["details"])

    def test_exact_card_links_not_range_inferences(self):
        search = self.search()
        bundle = dict(
            cards=[dict(id="T03", sources=["native/codex/sessions/fixture.jsonl:2"])], queries=[]
        )
        view = step.build(search, bundle)
        self.assertEqual(view["rows"][1]["exact_source_card_links"], ["T03"])
        self.assertEqual(view["rows"][2]["exact_source_card_links"], [])
        self.assertNotIn("T03", json.dumps(step.build(search)))
        self.assertEqual(view["rows"][1]["review_status"], "unreviewed")

    def test_html_and_table_text_escaped_not_executable(self):
        search = self.search()
        search["native"]["statements"][0]["text"] = "<script>alert(1)</script>|fixture"
        page = step.documents(step.build(search))["STEP_PAGE_001.md"]
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&#124;fixture", page)

    def test_stable_identity_survives_inserted_other_actor_event(self):
        search = self.search()
        original = step.build(search)["rows"][1]
        search["native"]["statements"].append(
            dict(
                actor="child",
                source="native/other.jsonl",
                line=1,
                at="2026-09-14T00:01:00Z",
                text="another event",
            )
        )
        shifted = next(r for r in step.build(search)["rows"] if r["source"] == original["source"])
        self.assertNotEqual(original["number"], shifted["number"])
        self.assertEqual(original["stable_id"], shifted["stable_id"])

    def test_unknown_tools_and_multiple_outputs_are_preserved(self):
        search = self.search(1)
        row = search["native"]["actions"][0]
        row["tool"] = "future_tool"
        row["request_observation"] = step.request_observation("future_tool", {"x": 123})
        output = copy.deepcopy(row["outputs"][0])
        output["line"] = 99
        output["at"] = "later"
        row["outputs"].append(output)
        view = step.build(search)
        docs = step.documents(view)
        self.assertIn("future_tool", docs["STEP_PAGE_001.md"])
        self.assertEqual(len(view["rows"][1]["outputs"]), 2)
        self.assertEqual(docs["STEP_PAGE_001.md"].count("|返回时间|"), 2)


if __name__ == "__main__":
    unittest.main()
