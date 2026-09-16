"""No-model/no-network acceptance for the portable collaboration store."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest

from kinetic_agents.team.contracts import Conflict
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore


class TeamStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cfx-team-store-")
        self.root = Path(self.temp.name) / "state"
        self.identity = TeamIdentity("synthetic", "a" * 64, "synthetic-no-model", "high")
        self.store = TeamStore(self.root, self.identity)
        self.store.register("pi")
        self.store.register("worker", "pi")

    def tearDown(self):
        self.temp.cleanup()

    def change(self, revision, value, request="request", actor="worker"):
        return self.store.apply(
            actor,
            "note",
            {"value": value, "expected": revision},
            request,
            lambda db: self.store.put(db, "document", "note", revision, {"value": value}),
        )

    def test_identity_survives_restart_and_rejects_different_run(self):
        self.assertEqual(TeamStore(self.root).identity, self.identity)
        self.assertEqual(TeamStore(self.root, self.identity).identity, self.identity)
        with self.assertRaises(PermissionError):
            TeamStore(self.root, TeamIdentity("foreign", "b" * 64, "synthetic-no-model", "high"))
        self.assertEqual(TeamStore(self.root).identity, self.identity)

    def test_registration_cannot_rebind_or_import_foreign_parent(self):
        self.store.register("worker", "pi")
        for actor, parent in [("worker", None), ("other-pi", None), ("foreign", "unknown")]:
            with self.assertRaises(PermissionError):
                self.store.register(actor, parent)
        self.store.register("worker2", "pi")
        self.store.register("worker3", "worker2")
        with self.assertRaises(PermissionError):
            self.store.register("over-cap", "pi")

    def test_versions_are_append_only_and_cas_rejects_stale_revision(self):
        first = self.change(0, "original", "r1")
        second = self.change(1, "revised", "r2")
        with self.store.connection() as db:
            self.assertEqual(self.store.record(db, "document", "note", 1), first)
            self.assertEqual(self.store.record(db, "document", "note"), second)
        with self.assertRaises(Conflict):
            self.change(1, "stale", "r3")

    def test_closed_researcher_releases_slot_without_erasing_identity(self):
        self.store.register("worker2", "pi")
        self.store.register("worker3", "pi")
        self.store.native_lifecycle("worker", closed=True)
        self.store.register("worker4", "pi")
        self.assertEqual(self.store.status()["active_members"], 4)
        self.assertEqual(len(self.store.status()["agents"]), 5)
        with self.assertRaises(PermissionError):
            self.store.native_lifecycle("worker", closed=False)
        self.store.native_lifecycle("worker4", closed=True)
        self.store.native_lifecycle("worker", closed=False)
        self.assertEqual(self.store.status()["active_members"], 4)

    def test_concurrent_revision_has_exactly_one_winner(self):
        self.change(0, "original", "initial")
        ready = threading.Barrier(2)

        def update(index):
            ready.wait(timeout=3)
            try:
                self.change(1, str(index), f"race-{index}")
                return "committed"
            except Conflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(update, [1, 2]))
        self.assertCountEqual(outcomes, ["committed", "conflict"])

    def test_request_replay_is_persistent_and_conflicting_retry_rejected(self):
        first = self.change(0, "same")
        before = self.store.events()
        self.store = TeamStore(self.root)
        self.assertEqual(self.change(0, "same"), first)
        self.assertEqual(self.store.events(), before)
        with self.assertRaises(Conflict):
            self.change(0, "different")

    def test_read_only_observer_does_not_wait_for_writer_or_add_events(self):
        before = self.store.events()
        with self.store.connection(write=True) as writer:
            self.store.event(writer, "pi", "uncommitted", None, None)
            with ThreadPoolExecutor(max_workers=1) as workers:
                status = workers.submit(self.store.status).result(timeout=2)
            self.assertEqual(status["last_event"], before[-1]["seq"])
            self.assertEqual(self.store.events(), before)
        committed = self.store.events()
        self.store.status()
        self.assertEqual(self.store.events(), committed)

    def test_failed_change_rolls_back_record_event_and_retry_key(self):
        before = self.store.events()

        def crash(db):
            self.store.put(db, "document", "note", 0, {"value": "partial"})
            raise RuntimeError("synthetic interrupted transaction")

        with self.assertRaises(RuntimeError):
            self.store.apply("worker", "note", {}, "interrupted", crash)
        with self.store.connection() as db:
            with self.assertRaises(KeyError):
                self.store.record(db, "document", "note")
        self.assertEqual(self.store.events(), before)
        self.change(0, "retry", "interrupted")

    def test_oversized_record_and_boolean_version_rejected(self):
        for revision, value in [(False, "bad-version"), (0, "x" * 64001)]:
            with self.assertRaises(ValueError):
                self.change(revision, value)
        self.assertEqual(self.change(0, "small")["version"], 1)

    def test_unknown_actor_and_path_identifiers_rejected(self):
        with self.assertRaises(PermissionError):
            self.change(0, "foreign", actor="unknown")
        with self.assertRaises(ValueError):
            self.store.register("../foreign", "pi")
        with self.assertRaises(PermissionError):
            TeamStore(self.root / ".." / "elsewhere", self.identity)

    def test_symlinked_state_is_rejected(self):
        link = Path(self.temp.name) / "linked-state"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(PermissionError):
            TeamStore(link)

    def test_closed_submission_allows_replay_but_no_new_writes(self):
        original = self.change(0, "done", "original")
        self.store.apply(
            "pi",
            "finish",
            {},
            "finish",
            lambda db: self.store.put(db, "submission", "final", 0, {"status": "submitted"}),
        )
        self.assertEqual(self.change(0, "done", "original"), original)
        with self.assertRaises(Conflict):
            self.change(1, "late", "new")

    def test_ended_run_rejects_new_members(self):
        self.store.apply(
            "pi",
            "finish",
            {},
            "finish",
            lambda db: self.store.put(db, "submission", "final", 0, {"status": "submitted"}),
        )
        self.store.register("worker", "pi")  # A transport restart is idempotent.
        with self.assertRaises(Conflict):
            self.store.register("late", "pi")


if __name__ == "__main__":
    unittest.main()
