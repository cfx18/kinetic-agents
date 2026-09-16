import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import kinetic_agents.evaluation.submission as adapter


class ReceiverTests(unittest.TestCase):
    def test_transient_verification_error_rechecks_without_new_job(self):
        with tempfile.TemporaryDirectory(prefix="cfx-receiver-") as folder:
            root = Path(folder)
            good = {"scheduler": {"state": "COMPLETED"}}
            with patch.object(
                adapter.evaluator,
                "observe",
                side_effect=[PermissionError("synthetic mismatch"), good],
            ) as observe, patch.object(adapter.evaluator, "submit") as submit, patch.object(
                adapter.evaluator, "deploy"
            ) as deploy:
                self.assertEqual(adapter.observe_with_retries(root, sleep=lambda _: None), good)
                self.assertEqual(observe.call_count, 2)
                submit.assert_not_called()
                deploy.assert_not_called()
            receipt = json.loads((root / "receiver_status.json").read_text())
            self.assertEqual(receipt["status"], "RECOVERED")
            self.assertEqual(len(receipt["failures"]), 1)

    def test_persistent_identity_mismatch_still_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="cfx-receiver-") as folder:
            root = Path(folder)
            with patch.object(
                adapter.evaluator, "observe", side_effect=PermissionError("synthetic foreign job")
            ) as observe, patch.object(adapter.evaluator, "fetch") as fetch:
                with self.assertRaises(PermissionError):
                    adapter.observe_with_retries(root, sleep=lambda _: None)
                self.assertEqual(observe.call_count, 4)
                fetch.assert_not_called()
            self.assertFalse(json.loads((root / "receiver_status.json").read_text())["verified"])

    def test_success_has_no_extra_query_or_retry_sleep(self):
        with tempfile.TemporaryDirectory(prefix="cfx-receiver-") as folder:
            with patch.object(
                adapter.evaluator, "observe", return_value={"scheduler": {"state": "RUNNING"}}
            ) as observe:
                adapter.observe_with_retries(
                    Path(folder), sleep=lambda _: self.fail("unexpected sleep")
                )
                observe.assert_called_once()
