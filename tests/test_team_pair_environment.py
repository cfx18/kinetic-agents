import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from kinetic_agents.core.store import Store
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore
from kinetic_agents.research.environment import PairEnvironment
from kinetic_agents.research.environment import ScientificTeamService
from kinetic_agents.execution.jobs import RemoteJobs
from kinetic_agents.execution.jobs import read_regular
from kinetic_agents.execution.jobs import unpack_regular
from kinetic_agents.execution.jobs import batch_script


class PairEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cfx-pair-env-")
        self.root = Path(self.temp.name)
        (self.root / "work").mkdir()
        self.store = Store(self.root / "runtime.sqlite")
        self.store.initialize(
            dict(
                api_usd=50,
                cpu_seconds=512 * 3600,
                wall_seconds=86400,
                arm="solo-max",
                model="deepseek-flash",
                effort="max",
            )
        )
        self.store.update("QUALIFIED", qualification_cpu_charged_seconds=100)
        self.service = ScientificTeamService(
            TeamStore(
                self.root / "team",
                TeamIdentity("test", "a" * 64, "deepseek-flash", "max", max_members=4),
            )
        )
        self.service.store.register("pi")
        self.service.store.register("child", "pi")
        self.remote = RemoteJobs(self.root, self.store, transport=False)
        self.env = PairEnvironment(self.root, self.store, self.service, remote=self.remote)

    def tearDown(self):
        self.temp.cleanup()

    def args(self):
        return dict(
            argv=["python", "main.py"], inputs=["main.py"], outputs=["answer.txt"], minutes=5
        )

    def test_queue_is_fast_no_ssh_and_durable_same_id(self):
        with patch("kinetic_agents.execution.jobs.checked") as ssh:
            first = self.remote.submit("pi", self.args(), "id1")
            second = self.remote.submit("pi", self.args(), "id1")
            ssh.assert_not_called()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.remote.rows()), 1)
        self.assertEqual(self.remote.budget()["remote_charged_or_reserved_seconds"], 6 * 60 * 64)
        other = RemoteJobs(self.root, self.store, transport=False)
        self.assertEqual(other.rows()[0]["id"], first["id"])
        changed = {**self.args(), "minutes": 6}
        with self.assertRaises(PermissionError):
            other.submit("pi", changed, "id1")

    def test_no_dispatch_after_expiry_preserve_queued_record(self):
        self.remote.submit("pi", self.args(), "id1")
        self.store.update("FIXTURE", deadline=time.time() - 1)
        with patch("kinetic_agents.execution.jobs.checked") as ssh:
            self.remote._dispatch(self.remote.rows()[0])
            ssh.assert_not_called()
        self.assertEqual(self.remote.rows()[0]["status"], "REJECTED")
        self.assertEqual(self.remote.budget()["remote_charged_or_reserved_seconds"], 0)

    def test_budget_rejects_whole_team_overcommit(self):
        self.store.update("FIXTURE", qualification_cpu_charged_seconds=500 * 3600)
        with self.assertRaises(PermissionError):
            self.remote.submit("child", self.args(), "id1")
        self.assertEqual(self.remote.rows(), [])

    def test_reject_read_escape_symlink_hardlink_fifo(self):
        import os

        private = self.root / "private"
        private.write_text("synthetic private")
        (self.root / "work/linked").symlink_to(private)
        os.link(private, self.root / "work/hardlink")
        os.mkfifo(self.root / "work/fifo")
        for name in ("../private", "/private", "linked", "hardlink", "fifo"):
            with self.assertRaises((ValueError, PermissionError, OSError)):
                read_regular(self.root / "work", name)

    def test_output_archive_rejects_link_and_traversal_before_writing(self):
        for name, kind in [("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)]:
            blob = io.BytesIO()
            with tarfile.open(fileobj=blob, mode="w:") as archive:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.linkname = "/etc/passwd"
                archive.addfile(item)
            target = self.root / "archive"
            with self.assertRaises(PermissionError):
                unpack_regular(blob.getvalue(), target)
            self.assertFalse(target.exists())

    def test_finish_locks_bytes_not_just_report_and_child_cannot_finish(self):
        (self.root / "work/mechanism.yaml").write_text("synthetic mechanism")
        (self.root / "work/REPORT.md").write_text("synthetic report")
        args = dict(mechanisms=["mechanism.yaml"], report="REPORT.md", outcome="submitted")
        with self.assertRaises(PermissionError):
            self.env.finish("child", args, "f" * 64)
        with self.assertRaises(ValueError):
            self.service.finish("pi", summary="done", references=[], request_id="bare")
        answer = self.env.finish("pi", args, "f" * 64)
        self.assertTrue(answer["registered"])
        self.assertFalse(answer["scientifically_verified"])
        digest = answer["mechanism_hashes"][0]
        (self.root / "work/mechanism.yaml").write_text("later changed")
        self.assertEqual(
            (self.root / "final_artifacts" / digest).read_text(), "synthetic mechanism"
        )
        final = self.service.read("pi", kind="submission", record_id="final")
        self.assertEqual(final["summary"], "Locked final file manifest " + "f" * 64)

    def test_pending_job_blocks_final_not_silently_dropped(self):
        self.remote.submit("pi", self.args(), "pending")
        with self.assertRaises(ValueError):
            self.env.finish(
                "pi", dict(mechanisms=[], report="REPORT.md", outcome="incomplete"), "e" * 64
            )
        self.assertFalse((self.root / "submission.json").exists())

    def test_read_only_budget_does_not_advance_job(self):
        self.remote.submit("pi", self.args(), "pending")
        with patch("kinetic_agents.execution.jobs.checked") as ssh:
            self.env.budget("child", {}, "read")
            self.env.job_status("pi", {}, "list")
            ssh.assert_not_called()

    def test_skill_use_is_pinned_and_real_terminal_receipt_settles_it(self):
        def ref(kind, row):
            return dict(kind=kind, id=row["id"], version=row["version"])

        source = b"print(477)\n"
        evidence = self.env.archive.ingest(
            source,
            evidence_id="code",
            title="Synthetic code",
            category="artifact",
            provenance=dict(source_id="fixture", role="synthetic"),
            summary="fixture only",
        )
        spec = dict(
            skill_id="s",
            title="Fixture",
            instructions="Run synthetic code",
            applicability={"fixture": "synthetic"},
            input_schema={},
            output_schema={},
            artifact_ref=ref("evidence", evidence),
            memory_refs=[],
            test_refs=[],
        )
        self.service.skill("child", expected_version=0, status="draft", request_id="draft", **spec)
        trial = self.service.skill(
            "pi", expected_version=1, status="trial", request_id="trial", **spec
        )
        task = self.service.delegate(
            "pi",
            assignee="child",
            goal="Fixture",
            acceptance="477",
            references=[],
            request_id="delegate",
        )
        task = self.service.claim(
            "child", task_id=task["id"], expected_version=task["version"], request_id="claim"
        )
        ctx = self.service.context(
            "child",
            task_id=task["id"],
            expected_version=task["version"],
            references=[ref("skill", trial)],
            purpose="Synthetic use",
            request_id="ctx",
        )
        use = self.service.prepare_use(
            "child",
            task_id=task["id"],
            expected_version=task["version"],
            skill_ref=ref("skill", trial),
            context_id=ctx["id"],
            request_id="use",
        )
        args = dict(
            use_id=use["id"], interpreter="python", arguments=[], inputs=[], outputs=[], minutes=2
        )
        with self.assertRaises(PermissionError):
            self.env.skills.submit("pi", args, "bad")
        job = self.env.skills.submit("child", args, "good")
        row = self.remote.rows()[0]
        binding = row["args"]["_skill"]
        self.assertEqual(binding["artifact_sha256"], hashlib.sha256(source).hexdigest())
        with self.assertRaises(PermissionError):
            self.env.skills.before_dispatch(row, {binding["code_path"]: "0" * 64})
        self.env.skills.before_dispatch(row, {binding["code_path"]: binding["artifact_sha256"]})
        from kinetic_agents.team.contracts import Conflict

        with self.assertRaises(Conflict):
            self.env.skills.before_dispatch(row, {binding["code_path"]: binding["artifact_sha256"]})
        row.update(status="SETTLED", execution_receipt=dict(exit_code=1), charged_seconds=64)
        self.env.skills.after_terminal(row)
        self.env.skills.after_terminal(row)
        actual = self.service.read("child", kind="use", record_id=use["id"])
        self.assertEqual(actual["status"], "failed")
        self.assertTrue(actual["execution_receipt"])

    def test_metadata_exposes_actual_actor_without_requiring_hidden_state(self):
        result = self.env.budget("child", {}, "metadata")
        self.assertEqual(result["actor_id"], "child")
        self.assertEqual(result["actor_role"], "researcher")

    def test_remote_script_requests_owned_64_core_batch(self):
        script = batch_script(
            "/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/test/job", "cfx_test", 5
        )
        self.assertIn("#SBATCH -N 1", script)
        self.assertIn("#SBATCH -n 64", script)
        self.assertIn("runner.py", script)
        with self.assertRaises(PermissionError):
            batch_script("/tmp/foreign", "cfx_test", 5)


if __name__ == "__main__":
    unittest.main()
