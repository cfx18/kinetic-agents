"""Principal ↔ researcher protocol; no LLM calls, solver calls or scoring rules.

Actor identity is supplied by the trusted transport, never by tool arguments.
All accepted transitions and their notifications commit together. Large raw
artifacts belong to the scientific environment; records hold immutable references.
"""

import json
import inspect
import uuid

from kinetic_agents.team.contracts import Conflict
from kinetic_agents.team.contracts import TaskStatus as S
from kinetic_agents.team.contracts import digest
from kinetic_agents.team.contracts import encode
from kinetic_agents.team.contracts import identifier
from kinetic_agents.team.contracts import text
from kinetic_agents.team.contracts import version
from kinetic_agents.team.evolution import EvolutionService
from kinetic_agents.team.evolution import KINDS


class TeamService(EvolutionService):
    def __init__(self, store):
        self.store = store
        self._artifact_reader = None

    def call(self, actor, operation, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        methods = {
            name: getattr(self, name)
            for name in (
                "delegate",
                "claim",
                "ask",
                "answer",
                "submit",
                "review",
                "cancel",
                "note",
                "read",
                "list",
                "messages",
                "ack",
                "finish",
                "memory",
                "skill",
                "context",
                "prepare_use",
                "artifact_read",
            )
        }
        if operation not in methods:
            raise ValueError("unknown collaboration operation")
        with self.store.connection() as db:
            self.store.actor(db, actor)
        try:
            inspect.signature(methods[operation]).bind(actor, **arguments)
        except TypeError as exc:
            raise ValueError("arguments do not match the tool schema") from exc
        return methods[operation](actor, **arguments)

    def _principal(self, db, actor):
        if self.store.actor(db, actor)["role"] != "principal":
            raise PermissionError("only the principal can make this decision")

    def _task(self, db, actor, task_id, expected_version=None, statuses=None, assignee=False):
        row = self.store.record(db, "task", task_id)
        if expected_version is not None and row["version"] != version(expected_version):
            raise Conflict("task changed: read its current version")
        if assignee and actor != row["assignee"]:
            raise PermissionError("task belongs to another researcher")
        if statuses is not None and row["status"] not in statuses:
            raise Conflict("operation is not allowed in the current task state")
        return row

    def _refs(self, db, refs, *, allow_inactive=False):
        if not isinstance(refs, list) or len(refs) > 32:
            raise ValueError("at most 32 pinned document references")
        clean = []
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) not in (
                {"id", "version"},
                {"kind", "id", "version"},
            ):
                raise ValueError("references require kind, id and version")
            kind = ref.get("kind", "document")
            if kind not in KINDS:
                raise ValueError("unknown reference kind")
            doc = self.store.record(db, kind, identifier(ref["id"]), version(ref["version"]))
            latest = self.store.record(db, kind, ref["id"])
            if not allow_inactive and (
                doc.get("status") in {"revoked", "archived"}
                or latest.get("status") in {"revoked", "archived"}
            ):
                raise Conflict("revoked document cannot enter a new task/submission")
            clean.append({"kind": kind, "id": doc["id"], "version": doc["version"]})
        return clean

    def delegate(self, actor, *, assignee, goal, acceptance, references, request_id):
        text(goal)
        text(acceptance)
        identifier(assignee)
        args = dict(assignee=assignee, goal=goal, acceptance=acceptance, references=references)

        def change(db):
            self._principal(db, actor)
            if self.store.actor(db, assignee)["role"] != "researcher" and assignee != actor:
                raise ValueError("assign to yourself or a registered researcher")
            row = self.store.put(
                db,
                "task",
                "task_" + uuid.uuid4().hex,
                0,
                {
                    **args,
                    "references": self._refs(db, references),
                    "status": S.READY,
                    "principal": actor,
                    "clarifications": [],
                    "delivery": None,
                    "assignment_kind": "self" if assignee == actor else "delegated",
                },
            )
            self.store.notify(db, assignee, actor, "task", row["id"], row["version"])
            return row

        return self.store.apply(actor, "delegate", args, request_id, change)

    def _transition(self, actor, operation, task_id, expected_version, request_id, update):
        args = dict(task_id=task_id, expected_version=expected_version)

        def change(db):
            row = self._task(db, actor, task_id, expected_version)
            value, recipient = update(db, row)
            result = self.store.put(db, "task", task_id, expected_version, value)
            self.store.notify(db, recipient, actor, "task", task_id, result["version"])
            return result

        return args, change

    def claim(self, actor, *, task_id, expected_version, request_id):
        def update(db, row):
            self._task(db, actor, task_id, expected_version, {S.READY}, assignee=True)
            return {**row, "status": S.RUNNING}, row["principal"]

        args, change = self._transition(
            actor, "claim", task_id, expected_version, request_id, update
        )
        return self.store.apply(actor, "claim", args, request_id, change)

    def ask(self, actor, *, task_id, expected_version, question, impact, request_id):
        text(question)
        text(impact)
        args = dict(
            task_id=task_id, expected_version=expected_version, question=question, impact=impact
        )

        def change(db):
            task = self._task(
                db, actor, task_id, expected_version, {S.READY, S.RUNNING}, assignee=True
            )
            q = self.store.put(
                db,
                "question",
                "question_" + uuid.uuid4().hex,
                0,
                {
                    "task_id": task_id,
                    "task_version": expected_version,
                    "asker": actor,
                    "principal": task["principal"],
                    "question": question,
                    "impact": impact,
                    "answer": None,
                    "status": "open",
                },
            )
            self.store.put(
                db,
                "task",
                task_id,
                expected_version,
                {**task, "status": S.WAITING, "question_id": q["id"]},
            )
            self.store.notify(db, task["principal"], actor, "question", q["id"], q["version"])
            return q

        return self.store.apply(actor, "ask", args, request_id, change)

    def answer(self, actor, *, question_id, expected_version, answer, request_id):
        text(answer)
        args = dict(question_id=question_id, expected_version=expected_version, answer=answer)

        def change(db):
            self._principal(db, actor)
            question = self.store.record(db, "question", question_id)
            if question["status"] != "open" or question["version"] != version(expected_version):
                raise Conflict("question already resolved or changed")
            task = self._task(db, actor, question["task_id"], statuses={S.WAITING})
            if task["question_id"] != question_id:
                raise Conflict("question is not the task's current blocker")
            q = self.store.put(
                db,
                "question",
                question_id,
                expected_version,
                {**question, "answer": answer, "status": "answered"},
            )
            revised = self.store.put(
                db,
                "task",
                task["id"],
                task["version"],
                {
                    **task,
                    "status": S.READY,
                    "question_id": None,
                    "clarifications": [
                        *task["clarifications"],
                        {"question_id": question_id, "answer_version": q["version"]},
                    ],
                },
            )
            self.store.notify(db, task["assignee"], actor, "task", task["id"], revised["version"])
            return q

        return self.store.apply(actor, "answer", args, request_id, change)

    def submit(self, actor, *, task_id, expected_version, summary, references, request_id):
        text(summary)
        args = dict(
            task_id=task_id,
            expected_version=expected_version,
            summary=summary,
            references=references,
        )

        def change(db):
            task = self._task(db, actor, task_id, expected_version, {S.RUNNING}, assignee=True)
            row = self.store.put(
                db,
                "task",
                task_id,
                expected_version,
                {
                    **task,
                    "status": S.REVIEW,
                    "delivery": {
                        "summary": summary,
                        "references": self._refs(db, references),
                        "scientifically_verified": False,
                        "task_version_used": expected_version,
                    },
                },
            )
            self.store.notify(db, task["principal"], actor, "task", task_id, row["version"])
            return row

        return self.store.apply(actor, "submit", args, request_id, change)

    def review(self, actor, *, task_id, expected_version, decision, feedback, request_id):
        text(feedback)
        if decision not in {"accept", "revise"}:
            raise ValueError("review decision must be accept or revise")
        args = dict(
            task_id=task_id, expected_version=expected_version, decision=decision, feedback=feedback
        )

        def change(db):
            self._principal(db, actor)
            task = self._task(db, actor, task_id, expected_version, {S.REVIEW})
            row = self.store.put(
                db,
                "task",
                task_id,
                expected_version,
                {
                    **task,
                    "status": S.ACCEPTED if decision == "accept" else S.READY,
                    "review_feedback": feedback,
                    "acceptance_means": "principal accepted this deliverable, not independent scientific validation",
                },
            )
            self.store.notify(db, task["assignee"], actor, "task", task_id, row["version"])
            return row

        return self.store.apply(actor, "review", args, request_id, change)

    def cancel(self, actor, *, task_id, expected_version, reason, request_id):
        text(reason)
        args = dict(task_id=task_id, expected_version=expected_version, reason=reason)

        def change(db):
            self._principal(db, actor)
            task = self._task(db, actor, task_id, expected_version)
            if task["status"] in {S.ACCEPTED, S.CANCELLED}:
                raise Conflict("terminal task cannot be cancelled again")
            row = self.store.put(
                db,
                "task",
                task_id,
                expected_version,
                {**task, "status": S.CANCELLED, "cancel_reason": reason},
            )
            if task.get("question_id"):
                q = self.store.record(db, "question", task["question_id"])
                self.store.put(db, "question", q["id"], q["version"], {**q, "status": "cancelled"})
            self.store.notify(db, task["assignee"], actor, "task", task_id, row["version"])
            return row

        return self.store.apply(actor, "cancel", args, request_id, change)

    def note(
        self,
        actor,
        *,
        document_id,
        expected_version,
        kind,
        title,
        content,
        references,
        status,
        request_id
    ):
        identifier(document_id)
        version(expected_version)
        text(title)
        text(content)
        if kind not in {"question", "strategy", "report", "scratchpad"}:
            raise ValueError("unknown research document kind")
        if status not in {"draft", "revoked"}:
            raise ValueError(
                "documents support draft/revoked; use the separate memory and skill tools"
            )
        args = dict(
            document_id=document_id,
            expected_version=expected_version,
            kind=kind,
            title=title,
            content=content,
            references=references,
            status=status,
        )

        def change(db):
            if expected_version:
                old = self.store.record(db, "document", document_id)
                if old["kind"] != kind or old["status"] == "revoked":
                    raise Conflict("document kind is fixed; revoked documents require a new ID")
                if old["author"] != actor:
                    self._principal(db, actor)
                author = old["author"]
            else:
                author = actor
            return self.store.put(
                db,
                "document",
                document_id,
                expected_version,
                {
                    "author": author,
                    "editor": actor,
                    "kind": kind,
                    "title": title,
                    "content": content,
                    "references": self._refs(db, references),
                    "status": status,
                    "content_sha256": digest(content),
                    "scientifically_verified": False,
                },
            )

        return self.store.apply(actor, "note", args, request_id, change)

    def read(self, actor, *, kind, record_id, record_version=None):
        if kind not in KINDS | {"task", "question", "submission"}:
            raise ValueError("unknown record kind")
        if record_version is not None:
            version(record_version)
        with self.store.connection() as db:
            self.store.actor(db, actor)
            return self.store.record(db, kind, record_id, record_version)

    def list(self, actor, *, kind, after="", limit=12, query="", status=None, applicability=None):
        if (
            kind not in KINDS | {"task", "question"}
            or type(limit) is not int
            or not 1 <= limit <= 40
        ):
            raise ValueError("invalid metadata query")
        if (
            not isinstance(after, str)
            or len(after) > 128
            or not isinstance(query, str)
            or len(query) > 128
        ):
            raise ValueError("invalid metadata cursor/query")
        if status is not None and (not isinstance(status, str) or len(status) > 64):
            raise ValueError("invalid status filter")
        if applicability is not None:
            from kinetic_agents.team.evolution import scope

            scope(applicability)
        with self.store.connection() as db:
            self.store.actor(db, actor)
            # JSON content is not searched: index over descriptive titles/goals only.
            field = "goal" if kind == "task" else "question" if kind == "question" else "title"
            rows = db.execute(
                "SELECT value FROM heads WHERE kind=? AND id>? AND instr("
                "COALESCE(json_extract(value,?),''),?)>0 "
                "AND (? IS NULL OR json_extract(value,'$.status')=?) "
                "AND NOT EXISTS (SELECT 1 FROM json_each(?) wanted WHERE NOT EXISTS "
                "(SELECT 1 FROM json_each(json_extract(heads.value,'$.applicability')) actual "
                "WHERE actual.key=wanted.key AND actual.value=wanted.value)) ORDER BY id LIMIT ?",
                (
                    kind,
                    after,
                    "$." + field,
                    query,
                    status,
                    status,
                    encode(applicability or {}),
                    limit + 1,
                ),
            ).fetchall()
            keys = (
                "id",
                "version",
                "kind",
                "title",
                "goal",
                "question",
                "status",
                "author",
                "assignee",
                "task_id",
                "content_sha256",
                "applicability",
            )
            values = []
            for (raw,) in rows[:limit]:
                v = json.loads(raw)
                item = {
                    k: v[k][:240] if k in {"title", "goal", "question"} else v[k]
                    for k in keys
                    if k in v
                }
                if len(encode([*values, item]).encode()) > 15000:
                    break
                values.append(item)
            return {
                "rows": values,
                "next_after": values[-1]["id"] if len(rows) > len(values) else None,
                "meaning": "bounded metadata; exact scope/status filters, not a relevance guarantee",
            }

    def messages(self, actor, *, after=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("invalid inbox limit")
        with self.store.connection() as db:
            self.store.actor(db, actor)
            if after is None:
                row = db.execute("SELECT seq FROM cursors WHERE actor=?", (actor,)).fetchone()
                after = row[0] if row else 0
            version(after)
            rows = db.execute(
                "SELECT seq,sender,kind,object_id,version FROM messages "
                "WHERE recipient=? AND seq>? ORDER BY seq LIMIT ?",
                (actor, after, limit),
            ).fetchall()
            keys = ("seq", "sender", "kind", "id", "version")
            return {
                "messages": [dict(zip(keys, row)) for row in rows],
                "next_after": rows[-1][0] if rows else after,
                "delivery": "at-least-once; acknowledge after reading",
            }

    def artifact_read(self, actor, *, evidence_id, offset=0, limit=4096):
        """Read actual raw bytes for registered evidence; never a supplied host path."""
        from kinetic_agents.team.artifacts import ArtifactArchive

        if self._artifact_reader is None:
            self._artifact_reader = ArtifactArchive(self, create=False)
        return self._artifact_reader.read(
            actor, evidence_id=evidence_id, offset=offset, limit=limit
        )

    def ack(self, actor, *, sequence, request_id):
        version(sequence)

        def change(db):
            if not db.execute(
                "SELECT 1 FROM messages WHERE recipient=? AND seq=?", (actor, sequence)
            ).fetchone():
                raise PermissionError("inbox cursor does not belong to this actor")
            db.execute(
                "INSERT INTO cursors VALUES (?,?) ON CONFLICT(actor) DO UPDATE SET seq=MAX(seq,excluded.seq)",
                (actor, sequence),
            )
            return {"sequence": sequence}

        return self.store.apply(actor, "ack", {"sequence": sequence}, request_id, change)

    def finish(self, actor, *, summary, references, request_id):
        text(summary)

        def change(db):
            self._principal(db, actor)
            pending = [
                json.loads(row[0])
                for row in db.execute("SELECT value FROM heads WHERE kind='task'")
            ]
            if any(t["status"] not in {S.ACCEPTED, S.CANCELLED} for t in pending):
                raise Conflict("review or cancel outstanding tasks before final submission")
            if db.execute(
                "SELECT 1 FROM heads WHERE kind='use' AND json_extract(value,'$.status')='intended'"
            ).fetchone():
                raise Conflict(
                    "unsettled skill executions must receive executor receipts before submission"
                )
            return self.store.put(
                db,
                "submission",
                "final",
                0,
                {
                    "summary": summary,
                    "references": self._refs(db, references),
                    "scientifically_verified": False,
                    "identity": self.store.identity.as_dict(),
                    "evaluation_account": "separate",
                },
            )

        return self.store.apply(
            actor, "finish", dict(summary=summary, references=references), request_id, change
        )
