"""Crash-durable control state. Observers have no role in advancing a run.

Single coordinator owns each arm. SQLite FULL transactions persist deadlines,
retry counts and request identities. A reply is not a terminal experiment.
"""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

TERMINAL = {"COMPLETED", "BUDGET_STOPPED", "FAILED_REVIEW", "CANCELLED"}


class ModelBudgetExhausted(PermissionError):
    """A failed spending reservation, not a generic lifecycle/identity denial."""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, at REAL, kind TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, digest TEXT, status TEXT,
                    reserve REAL NOT NULL, charge REAL, detail TEXT);
            """
            )

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, key, default=None):
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _put(db, key, value):
        db.execute(
            "INSERT OR REPLACE INTO state VALUES(?,?)", (key, json.dumps(value, allow_nan=False))
        )

    @staticmethod
    def _event(db, kind, payload):
        db.execute(
            "INSERT INTO events(at,kind,payload) VALUES(?,?,?)",
            (time.time(), kind, json.dumps(payload, allow_nan=False)),
        )

    def initialize(self, contract, now=None):
        fingerprint = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
        with self.tx() as db:
            old = self._get(db, "contract_hash")
            if old:
                if old != fingerprint:
                    raise PermissionError("immutable run contract changed")
                return
            at = time.time() if now is None else now
            for key, value in dict(
                contract_hash=fingerprint,
                contract=contract,
                status="PREPARED",
                started=at,
                deadline=at + contract["wall_seconds"],
                failures=0,
                next_retry=0,
                thread_id=None,
            ).items():
                self._put(db, key, value)
            self._event(db, "INITIALIZED", {"contract_hash": fingerprint})

    def get(self, key, default=None):
        return self.read_state((key,)).get(key, default)

    def read_state(self, keys, *, include_call_count=False):
        """One consistent read snapshot. Never acquire a RESERVED/write lock."""
        uri = self.path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=20)
        try:
            db.execute("BEGIN")
            rows = dict(db.execute("SELECT key,value FROM state"))
            result = {key: json.loads(rows[key]) for key in keys if key in rows}
            if include_call_count:
                result["_call_count"] = db.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
            return result
        finally:
            db.close()

    def update(self, kind, **values):
        with self.tx() as db:
            for key, value in values.items():
                self._put(db, key, value)
            self._event(db, kind, values)

    def advance(self, event, now=None):
        now = time.time() if now is None else now
        if event == "tick":
            state = self.read_state(("status", "deadline", "next_retry"))
            current = state["status"]
            if current in TERMINAL or (
                now < state["deadline"] and (current != "RETRY_WAIT" or now < state["next_retry"])
            ):
                return current  # No transition, no write transaction/event.
        with self.tx() as db:
            current = self._get(db, "status")
            if current in TERMINAL:
                return current
            if current == "FINALIZING" and event == "model_budget_exhausted":
                return current  # A late model request cannot relabel a locked submission.
            if now >= self._get(db, "deadline") or event in (
                "budget_exhausted",
                "model_budget_exhausted",
            ):
                new = "BUDGET_STOPPED"
            elif event == "cancel":
                new = "CANCELLED"
            elif event == "fatal":
                new = "FAILED_REVIEW"
            elif event == "stop_validated":
                new = "FINALIZING"
            elif event == "artifacts_verified" and current == "FINALIZING":
                new = "COMPLETED"
            elif event == "recoverable_fault":
                count = self._get(db, "failures", 0) + 1
                self._put(db, "failures", count)
                # Persistent cumulative restart cap, not reset by process restarts.
                new = "RETRY_WAIT" if count <= 5 else "FAILED_REVIEW"
                self._put(db, "next_retry", now + min(120, 2**count))
            elif event == "tick" and current == "RETRY_WAIT":
                new = "RECOVERING" if now >= self._get(db, "next_retry") else current
            elif event in ("ready", "reply_completed", "recovered"):
                new = "RUNNING"
            elif event == "tick":
                new = current
            else:
                raise ValueError("invalid lifecycle transition")
            self._put(db, "status", new)
            self._event(db, event, {"from": current, "to": new})
            return new

    def reserve(self, request_id, digest, upper_usd):
        """Reserve BEFORE sending. Lost usage retains the full ceiling forever.

        Retries must be new charged calls; same ID cannot dispatch twice. This
        cannot make the upstream provider exactly-once, but bounds our exposure.
        """
        import math

        if not math.isfinite(upper_usd) or upper_usd <= 0:
            raise ValueError("invalid reservation")
        with self.tx() as db:
            if self._get(db, "status") in TERMINAL | {"FINALIZING"} or time.time() >= self._get(
                db, "deadline"
            ):
                raise PermissionError("run is terminal or original deadline expired")
            if db.execute("SELECT 1 FROM calls WHERE id=?", (request_id,)).fetchone():
                raise PermissionError("request identity already consumed")
            limit = self._get(db, "contract")["api_usd"]
            used = db.execute(
                "SELECT COALESCE(SUM(COALESCE(charge,reserve)),0) FROM calls"
            ).fetchone()[0]
            if used + upper_usd > limit + 1e-9:
                raise ModelBudgetExhausted("model budget exhausted")
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?)",
                (request_id, digest, "DISPATCH_RESERVED", upper_usd, None, "{}"),
            )
            self._event(db, "API_RESERVED", {"id": request_id, "upper_usd": upper_usd})

    def settle(self, request_id, actual, detail):
        import math

        if not math.isfinite(actual) or actual < 0:
            raise ValueError("invalid charge")
        with self.tx() as db:
            row = db.execute(
                "SELECT reserve,charge FROM calls WHERE id=?", (request_id,)
            ).fetchone()
            if row is None or row[1] is not None:
                raise PermissionError("unknown or already settled call")
            if actual > row[0] + 1e-9:
                self._put(db, "status", "FAILED_REVIEW")
                self._event(db, "PRICE_BOUND_BREACH", {"id": request_id})
            db.execute(
                "UPDATE calls SET status=?,charge=?,detail=? WHERE id=?",
                ("SETTLED", actual, json.dumps(detail), request_id),
            )

    def snapshot(self):
        # Truly read-only observer; no heartbeat writes and no model/SSH calls.
        uri = self.path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            keys = ("status", "started", "deadline", "failures", "thread_id", "next_retry")
            result = {key: self._get(db, key) for key in keys}
            result["api"] = dict(
                zip(
                    ("requests", "charged_or_reserved_usd"),
                    db.execute(
                        "SELECT COUNT(*),COALESCE(SUM(COALESCE(charge,reserve)),0) FROM calls"
                    ).fetchone(),
                )
            )
            return result
