import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import kinetic_agents.evaluation.submission as adapter


class PairEndpointTests(unittest.TestCase):
    def test_only_locked_final_files_and_separate_account_reach_evaluator(self):
        import cantera as ct

        with tempfile.TemporaryDirectory(prefix="cfx-pair-endpoint-") as d:
            pair = Path(d)
            root = pair / "solo-max"
            for p in (root / "task", root / "final_artifacts", pair / "qualification"):
                p.mkdir(parents=True)
            parent = b"synthetic parent"
            candidate = b"synthetic candidate"
            psha = hashlib.sha256(parent).hexdigest()
            csha = hashlib.sha256(candidate).hexdigest()
            (root / "task/parent.yaml").write_bytes(parent)
            (root / "final_artifacts" / csha).write_bytes(candidate)
            (root / "local_cpu.json").write_text(json.dumps(dict(status="SETTLED")))
            submission = dict(
                mechanisms=[dict(sha256=csha)], report=dict(sha256="0" * 64), outcome="submitted"
            )
            (root / "submission.json").write_text(json.dumps(submission))
            (root / "result.json").write_text(json.dumps(dict(submission=submission)))
            base = dict(
                development=[],
                recheck=[],
                numerical_policy={},
                solver_version=ct.__version__,
                source_pins={"fixture": "fixed"},
                per_case_cpu={},
                source_manifest_sha256="b" * 64,
            )
            (pair / "evaluation_base.json").write_text(json.dumps(base))
            (pair / "qualification/endpoint.json").write_text(
                json.dumps(dict(base_sha256=adapter.file_sha(pair / "evaluation_base.json")))
            )
            (pair / "preflight.json").write_text(
                json.dumps(dict(contracts={"solo-max": dict(parent_sha256=psha)}))
            )

            class Gas:
                n_species = 3
                n_reactions = 2
                species_names = ["H2", "O2", "H2O"]

            with patch.object(
                adapter, "source_pins", return_value={"fixture": "fixed"}
            ), patch.object(ct, "Solution", return_value=Gas()), patch(
                "kinetic_agents.evaluation.artifacts.inline_yaml"
            ), patch(
                "kinetic_agents.release.verify",
                return_value={"files": {"kinetic_agents/evaluation/scoring.py": "a" * 64}},
            ), patch.object(
                adapter.evaluator, "register"
            ) as register:
                target = adapter.prepare(root, pair / "release")
                register.assert_called_once_with(target, pair / "release")
            manifest = json.loads((target / "manifest.json").read_text())
            self.assertEqual(len(manifest["selected"]), 2)
            self.assertEqual(manifest["endpoint_pairs"], 1220)
            self.assertFalse(manifest["controller_feedback_allowed"])
            self.assertFalse(manifest["scientist_restart_allowed"])
            self.assertEqual((target / "candidate_1.yaml").read_bytes(), candidate)
            self.assertEqual(manifest["selected"]["candidate_1"]["sha256"], csha)

    def test_evaluator_identifiers_and_cpu_total_are_separate(self):
        for arm in ("solo-max", "team-max"):
            name = adapter.identify(arm)
            self.assertIn("cfx", name)
            self.assertIn(arm.replace("-", "_"), name)
            self.assertLessEqual(adapter.evaluator.MINUTES * 60 * 64 + 3600, 64 * 3600)
        with self.assertRaises(PermissionError):
            adapter.identify("old-other-run")


if __name__ == "__main__":
    unittest.main()
