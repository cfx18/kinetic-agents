"""Host-owned SQLite state, short writes, read-only observers and replayable events.

Deploy outside all agent-writable roots. These path checks are not an OS sandbox.
No model/network/solver work belongs inside a transaction. Reads do not acquire
write reservations or rewrite heartbeats. SQLite WAL requires a local filesystem.
"""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from kinetic_agents.team.contracts import Conflict
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.contracts import digest
from kinetic_agents.team.contracts import encode
from kinetic_agents.team.contracts import identifier
from kinetic_agents.team.contracts import version


class TeamStore:
    def __init__(self, directory, identity=None):
        self.root = Path(directory).absolute()
        self.path = self.root / "team.sqlite"
        self._check_paths()
        if identity is not None:
            if not isinstance(identity, TeamIdentity):
                raise TypeError("TeamIdentity required")
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self.connection(write=True, create=True) as db:
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS identity(value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,parent TEXT,role TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS retired_agents(id TEXT PRIMARY KEY);
                    CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,version INTEGER,value TEXT,
                        PRIMARY KEY(kind,id,version));
                    CREATE TABLE IF NOT EXISTS heads(kind TEXT,id TEXT,version INTEGER,value TEXT,
                        PRIMARY KEY(kind,id));
                    CREATE TABLE IF NOT EXISTS requests(actor TEXT,id TEXT,digest TEXT,result TEXT,
                        PRIMARY KEY(actor,id));
                    CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,at REAL,actor TEXT,
                        operation TEXT,object_id TEXT,version INTEGER);
                    CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY,recipient TEXT,
                        sender TEXT,kind TEXT,object_id TEXT,version INTEGER);
                    CREATE INDEX IF NOT EXISTS message_recipient ON messages(recipient,seq);
                    CREATE TABLE IF NOT EXISTS cursors(actor TEXT PRIMARY KEY,seq INTEGER);
                    CREATE TABLE IF NOT EXISTS execution_receipts(receipt TEXT PRIMARY KEY,use_id TEXT UNIQUE NOT NULL);
                """
                )
                # executescript commits before executing; begin the identity CAS explicitly.
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute("SELECT value FROM identity").fetchall()
                wanted = encode(identity.as_dict())
                if not rows:
                    db.execute("INSERT INTO identity VALUES (?)", (wanted,))
                elif rows != [(wanted,)]:
                    raise PermissionError(
                        "team identity differs; never reuse another run or settings"
                    )
        with self.connection() as db:
            rows = db.execute("SELECT value FROM identity").fetchall()
            if len(rows) != 1:
                raise ValueError("invalid team identity")
            self.identity = TeamIdentity(**json.loads(rows[0][0]))

    def _check_paths(self):
        if ".." in self.root.parts:
            raise PermissionError("team root traversal")
        for path in (
            self.root,
            *self.root.parents,
            self.path,
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ):
            if path.is_symlink():
                raise PermissionError("team state symlink")
            if (
                path.exists()
                and not path.is_dir()
                and (not path.is_file() or path.stat().st_nlink != 1)
            ):
                raise PermissionError("team state must use regular unlinked files")

    @contextmanager
    def connection(self, *, write=False, create=False):
        self._check_paths()
        mode = "rwc" if create else "rw" if write else "ro"
        db = sqlite3.connect(self.path.as_uri() + "?mode=" + mode, uri=True, timeout=10)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            if create:
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, actor, parent=None):
        """Trusted harness events only. Never expose registration as an LLM tool."""
        identifier(actor)
        if parent is not None:
            identifier(parent)
        role = "principal" if parent is None else "researcher"
        with self.connection(write=True) as db:
            existing = db.execute("SELECT parent,role FROM agents WHERE id=?", (actor,)).fetchone()
            if existing:
                if existing != (parent, role):
                    raise PermissionError("agent identity rebound")
                return
            if db.execute("SELECT 1 FROM heads WHERE kind='submission'").fetchone():
                raise Conflict("run submitted; no new team members")
            if parent is None and db.execute("SELECT 1 FROM agents").fetchone():
                raise PermissionError("principal already registered")
            if (
                parent is not None
                and not db.execute("SELECT 1 FROM agents WHERE id=?", (parent,)).fetchone()
            ):
                raise PermissionError("unregistered parent")
            if (
                len(list(db.execute("SELECT id FROM agents"))) - len(self.retired(db))
                >= self.identity.max_members
            ):
                raise PermissionError("registered team capacity exceeded")
            db.execute("INSERT INTO agents VALUES (?,?,?)", (actor, parent, role))
            self.event(db, actor, "registered", actor, 1)

    @staticmethod
    def retired(db):
        # Read legacy records without silently migrating an old experiment.
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='retired_agents'"
        ).fetchone():
            return set()
        return {r[0] for r in db.execute("SELECT id FROM retired_agents")}

    def native_lifecycle(self, actor, *, closed):
        """Authenticated native close/resume ONLY; retain all historical records."""
        with self.connection(write=True) as db:
            row = db.execute("SELECT role FROM agents WHERE id=?", (actor,)).fetchone()
            if row is None or row[0] != "researcher":
                raise PermissionError("only owned researcher lifecycle allowed")
            db.execute("CREATE TABLE IF NOT EXISTS retired_agents(id TEXT PRIMARY KEY)")
            retired = self.retired(db)
            if not closed and actor in retired:
                if (
                    len(list(db.execute("SELECT id FROM agents"))) - len(retired)
                    >= self.identity.max_members
                ):
                    raise PermissionError("registered team capacity exceeded")
                db.execute("DELETE FROM retired_agents WHERE id=?", (actor,))
            if closed:
                db.execute("INSERT OR IGNORE INTO retired_agents VALUES(?)", (actor,))
            self.event(db, actor, "native_closed" if closed else "native_resumed", actor, 1)

    @staticmethod
    def actor(db, actor):
        identifier(actor)
        row = db.execute("SELECT parent,role FROM agents WHERE id=?", (actor,)).fetchone()
        if row is None:
            raise PermissionError("thread not registered in this run")
        if actor in TeamStore.retired(db):
            raise PermissionError("native researcher is closed")
        return {"id": actor, "parent": row[0], "role": row[1]}

    @staticmethod
    def record(db, kind, key, rev=None):
        identifier(key)
        row = (
            db.execute("SELECT value FROM heads WHERE kind=? AND id=?", (kind, key)).fetchone()
            if rev is None
            else db.execute(
                "SELECT value FROM records WHERE kind=? AND id=? AND version=?", (kind, key, rev)
            ).fetchone()
        )
        if row is None:
            raise KeyError("record not found in this run")
        return json.loads(row[0])

    @staticmethod
    def put(db, kind, key, expected, value):
        identifier(key)
        version(expected)
        row = db.execute("SELECT version FROM heads WHERE kind=? AND id=?", (kind, key)).fetchone()
        if (row[0] if row else 0) != expected:
            raise Conflict("stale version: read the latest record before revising")
        record = {**value, "id": key, "version": expected + 1}
        raw = encode(record)
        if len(raw.encode()) > 64000:
            raise ValueError("record exceeds 64 KiB: keep large raw artifacts outside context")
        db.execute("INSERT INTO records VALUES (?,?,?,?)", (kind, key, expected + 1, raw))
        db.execute("INSERT OR REPLACE INTO heads VALUES (?,?,?,?)", (kind, key, expected + 1, raw))
        return record

    @staticmethod
    def event(db, actor, operation, key, rev):
        db.execute(
            "INSERT INTO events(at,actor,operation,object_id,version) VALUES (?,?,?,?,?)",
            (time.time(), actor, operation, key, rev),
        )

    @staticmethod
    def notify(db, recipient, actor, kind, key, rev):
        db.execute(
            "INSERT INTO messages(recipient,sender,kind,object_id,version) VALUES (?,?,?,?,?)",
            (recipient, actor, kind, key, rev),
        )

    def apply(self, actor, operation, arguments, request_id, change):
        identifier(request_id)
        identity = digest({"operation": operation, "arguments": arguments})
        with self.connection(write=True) as db:
            self.actor(db, actor)
            prior = db.execute(
                "SELECT digest,result FROM requests WHERE actor=? AND id=?", (actor, request_id)
            ).fetchone()
            if prior:
                if prior[0] != identity:
                    raise Conflict("request_id reused with different arguments")
                return json.loads(prior[1])
            if db.execute("SELECT 1 FROM heads WHERE kind='submission'").fetchone():
                raise Conflict("run submitted; collaboration is read-only")
            result = change(db)
            db.execute(
                "INSERT INTO requests VALUES (?,?,?,?)",
                (actor, request_id, identity, encode(result)),
            )
            self.event(db, actor, operation, result.get("id"), result.get("version"))
            return result

    def status(self):
        """Read-only metadata; deliberately excludes task text and artifact bodies."""
        with self.connection() as db:
            agents = [
                {"id": a, "parent": p, "role": r}
                for a, p, r in db.execute("SELECT * FROM agents ORDER BY id")
            ]
            retired = self.retired(db)
            tasks = [
                {k: value[k] for k in ("id", "version", "assignee", "status")}
                for (raw,) in db.execute("SELECT value FROM heads WHERE kind='task' ORDER BY id")
                for value in [json.loads(raw)]
            ]
            return {
                "identity": self.identity.as_dict(),
                "agents": agents,
                "tasks": tasks,
                "closed_agents": sorted(retired),
                "active_members": len(agents) - len(retired),
                "submitted": bool(
                    db.execute("SELECT 1 FROM heads WHERE kind='submission'").fetchone()
                ),
                "last_event": db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0],
                "scientific_score": None,
                "meaning": "coordination state, not scientific verification",
            }

    def events(self, after=0, limit=50):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid event cursor")
        with self.connection() as db:
            columns = ("seq", "at", "actor", "operation", "object_id", "version")
            return [
                dict(zip(columns, row))
                for row in db.execute(
                    "SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (after, limit)
                )
            ]
