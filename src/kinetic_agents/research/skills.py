"""Pin optional Skill Uses to the SAME generic remote executor and CPU account."""

import hashlib
import json

from kinetic_agents.team.contracts import digest
from kinetic_agents.team.contracts import Conflict
from kinetic_agents.execution.jobs import write_new_regular
from kinetic_agents.execution.jobs import read_regular


class SkillJobs:
    def __init__(self, environment):
        self.env = environment
        self.service = environment.service
        self.archive = environment.archive

    def submit(self, actor, args, request_id):
        if set(args) != {"use_id", "interpreter", "arguments", "inputs", "outputs", "minutes"}:
            raise ValueError("use_id/interpreter/arguments/inputs/outputs/minutes required")
        if args["interpreter"] not in ("python", "sh"):
            raise ValueError("qualified Python or sh interpreter required")
        with self.service.store.connection() as db:
            use = self.service.store.record(db, "use", args["use_id"])
            if use["actor"] != actor or use["status"] != "intended":
                raise PermissionError("own unexecuted Skill Use required")
        data = self.archive.load(use["artifact_sha256"])
        name = (
            "skill_sources/"
            + use["artifact_sha256"]
            + (".py" if args["interpreter"] == "python" else ".sh")
        )
        try:
            write_new_regular(self.env.root / "work", name, data)
        except FileExistsError:
            if read_regular(self.env.root / "work", name) != data:
                raise PermissionError("skill work copy changed")
        if not isinstance(args["arguments"], list) or not all(
            isinstance(v, str) for v in args["arguments"]
        ):
            raise ValueError("string argv arguments required")
        request = dict(
            argv=[args["interpreter"], "/work/" + name, *args["arguments"]],
            inputs=[name, *args["inputs"]],
            outputs=args["outputs"],
            minutes=args["minutes"],
            _skill=dict(use_id=use["id"], code_path=name, artifact_sha256=use["artifact_sha256"]),
        )
        return self.env.remote.submit(actor, request, request_id)

    def before_dispatch(self, row, manifest):
        bound = row["args"]["_skill"]
        if manifest.get(bound["code_path"]) != bound["artifact_sha256"]:
            raise PermissionError("skill source snapshot differs")
        with self.service.store.connection(write=True) as db:
            use = self.service.store.record(db, "use", bound["use_id"])
            if (
                use["actor"] != row["actor"]
                or use["status"] != "intended"
                or use["artifact_sha256"] != bound["artifact_sha256"]
            ):
                raise PermissionError("Skill Use actor/state/hash differs")
            self.service._task(
                db, use["actor"], use["task_id"], use["task_version"], {"running"}, assignee=True
            )
            self.service._refs(db, [use["skill_ref"]])
            skill = self.service.store.record(
                db, "skill", use["skill_ref"]["id"], use["skill_ref"]["version"]
            )
            self.service._refs(db, skill["memory_refs"])
            if skill["status"] != "trial":
                raise PermissionError("skill not authorized for trial")
            for (raw,) in db.execute("SELECT value FROM heads WHERE kind='execution'"):
                old = json.loads(raw)
                if old.get("use_id") == use["id"]:
                    raise Conflict("Skill Use already dispatched; do not replay")
            saved = self.service.store.put(
                db,
                "execution",
                row["id"],
                0,
                dict(
                    use_id=use["id"],
                    actor=use["actor"],
                    status="dispatching",
                    input_manifest=manifest,
                    argv_sha256=digest(row["args"]["argv"]),
                    allocated_core_seconds_reserved=row["reserved_seconds"],
                    account="existing_search_account",
                ),
            )
            self.service.store.event(
                db, "environment", "skill_batch_bound", row["id"], saved["version"]
            )

    def after_terminal(self, row):
        bound = row["args"].get("_skill")
        if not bound:
            return
        with self.service.store.connection() as db:
            use = self.service.store.record(db, "use", bound["use_id"])
            if use["status"] != "intended":
                return
        receipt = row.get("execution_receipt")
        status = (
            "failed"
            if row["status"] == "REJECTED"
            else (
                "unknown"
                if receipt is None
                else "succeeded" if receipt["exit_code"] == 0 else "failed"
            )
        )
        evidence = self.archive.ingest(
            json.dumps(row, sort_keys=True).encode(),
            evidence_id="batch_" + row["id"],
            title="Remote execution and accounting receipt",
            category="diagnostic",
            provenance=dict(source_id=row["id"], role="development"),
            summary="Host batch record; rejected means no program dispatch, missing receipt means unknown. "
            "Successful execution is not scientific correctness or skill improvement.",
        )
        self.service.attest_execution(
            use_id=use["id"],
            receipt_id="batch_" + row["id"],
            artifact_sha256=use["artifact_sha256"],
            status=status,
            output_refs=[dict(kind="evidence", id=evidence["id"], version=evidence["version"])],
        )
