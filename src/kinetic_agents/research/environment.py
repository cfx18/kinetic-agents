"""Open-world team tools: generic compute, bounded evidence and locked finals.

No scientific algorithm or case selection is supplied here. Raw artifacts are
archived, not certified; final benchmark scoring stays outside this environment.
"""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from kinetic_agents.core.storage import atomic
from kinetic_agents.research.tool_schemas import SPECS as ORIGINAL_SPECS
from kinetic_agents.core.state import EnvironmentState
from kinetic_agents.team.service import TeamService
from kinetic_agents.team.artifacts import ArtifactArchive
from kinetic_agents.execution.jobs import RemoteJobs
from kinetic_agents.execution.jobs import read_regular
from kinetic_agents.execution.jobs import write_new_regular
from kinetic_agents.core.trajectory import Trajectory
from kinetic_agents.observability.notebook_tools import STAGES as DECISION_STAGES


def schema(name, description, properties, required):
    return dict(
        name=name,
        description=description,
        parameters=dict(
            type="object", properties=properties, required=required, additionalProperties=False
        ),
    )


SPECS = [
    dict(name=s["name"], description=s["description"], parameters=s["inputSchema"])
    for s in ORIGINAL_SPECS
] + [
    schema(
        "research_compute_submit",
        "Enqueue your own program on a 64-core remote node, return immediately with a job ID. "
        "Container provides Python 3.12 and sh, no prescribed scientific software, network disabled. "
        "Find resources on local public internet; transfer explicit input files/offline wheels if needed. "
        "Inputs are relative files from your workspace, copied to /work with the same names. "
        "Your argv executes ONLY inside /work in the container. Outputs are explicit relative files. "
        "Allocated 64-core time, including idle time and failures, counts against your shared arm budget. "
        "Both solo and team may use parallel numerical scripts. Jobs queue; do not resubmit a pending job.",
        dict(
            argv=dict(type="array", items=dict(type="string")),
            inputs=dict(type="array", items=dict(type="string")),
            outputs=dict(type="array", items=dict(type="string")),
            minutes=dict(type="integer", minimum=2, maximum=120),
        ),
        ["argv", "inputs", "outputs", "minutes"],
    ),
    schema(
        "research_compute_status",
        "Read this arm's job metadata, paginated; contains no other arm or final evaluation results.",
        dict(offset=dict(type="integer", minimum=0)),
        [],
    ),
    schema(
        "research_compute_read",
        "Read up to 8192 archived bytes of a settled job output (base64), by job ID and relative output name.",
        dict(
            job_id=dict(type="string"),
            path=dict(type="string"),
            offset=dict(type="integer", minimum=0),
            limit=dict(type="integer", minimum=1, maximum=8192),
        ),
        ["job_id", "path"],
    ),
    schema(
        "research_compute_fetch",
        "Copy a settled raw output directly into a NEW workspace file for your own local postprocessing. "
        "This avoids passing large numeric arrays through model context. Existing destination files are never overwritten.",
        dict(job_id=dict(type="string"), path=dict(type="string"), destination=dict(type="string")),
        ["job_id", "path", "destination"],
    ),
    schema(
        "research_skill_compute_submit",
        "Execute an own intended team_prepare_use through the same shared-budget remote queue. "
        "Copies the exact pinned skill artifact; interpreter is Python or sh, arguments are command-line strings. "
        "Inputs are additional relative dependency/data files. Real terminal receipts settle the Skill Use; they do not prove improvement.",
        dict(
            use_id=dict(type="string"),
            interpreter=dict(type="string", enum=["python", "sh"]),
            arguments=dict(type="array", items=dict(type="string")),
            inputs=dict(type="array", items=dict(type="string")),
            outputs=dict(type="array", items=dict(type="string")),
            minutes=dict(type="integer", minimum=2, maximum=120),
        ),
        ["use_id", "interpreter", "arguments", "inputs", "outputs", "minutes"],
    ),
    schema(
        "research_record_decision",
        "Record a brief explicit decision justification, alternatives, evidence references, "
        "observed outcome and revisit condition. Not hidden reasoning. Reuse decision_id/expected_version for revisions; "
        "unknown means unrecorded, not success. This uses the existing versioned team notebook.",
        dict(
            decision_id=dict(type="string"),
            expected_version=dict(type="integer", minimum=0),
            stage=dict(type="string", enum=list(DECISION_STAGES)),
            decision=dict(type="string"),
            reason_summary=dict(type="string"),
            alternatives=dict(type="array", items=dict(type="string")),
            references=dict(type="array", items=dict(type="object")),
            outcome=dict(type="string"),
            revisit_when=dict(type="string"),
        ),
        [
            "decision_id",
            "expected_version",
            "stage",
            "decision",
            "reason_summary",
            "alternatives",
            "references",
            "outcome",
            "revisit_when",
        ],
    ),
    schema(
        "research_review_read",
        "Read this run's search notebook for experiment review: catalog first, then an exact "
        "record/version. Includes decisions, tasks, questions, memory, skills and evidence. "
        "No other runs, final benchmark feedback or hidden reasoning. No model summary is generated.",
        dict(
            section=dict(
                type="string",
                enum=["decisions", "queries", "tasks", "questions", "memory", "skills", "evidence"],
            ),
            after=dict(type="string"),
            limit=dict(type="integer", minimum=1, maximum=40),
            record_id=dict(type="string"),
            version=dict(type="integer", minimum=1),
        ),
        [],
    ),
    schema(
        "research_record_query",
        "Record each substantive information query after reading its result: exact search "
        "keywords/filters or file sections, returned facts, what you explicitly consider important and why, "
        "and how it changes the next action (including no change). Not hidden reasoning. "
        "Group pagination of the same query, not unrelated queries; do not record heartbeat polling here. "
        "source_addresses are your reported URLs/file sections/call IDs, not host-verified facts. "
        "Revise by query_id/expected_version. Prefer brief Chinese public summaries.",
        dict(
            query_id=dict(type="string"),
            expected_version=dict(type="integer", minimum=0),
            purpose=dict(type="string"),
            method=dict(type="string"),
            request=dict(type="string"),
            returned_summary=dict(type="string"),
            important_findings=dict(type="string"),
            importance_status=dict(type="string", enum=["identified", "none", "unknown"]),
            decision_effect=dict(type="string"),
            source_addresses=dict(type="array", items=dict(type="string")),
            references=dict(type="array", items=dict(type="object")),
        ),
        [
            "query_id",
            "expected_version",
            "purpose",
            "method",
            "request",
            "returned_summary",
            "important_findings",
            "importance_status",
            "decision_effect",
            "source_addresses",
            "references",
        ],
    ),
    schema(
        "research_record_artifact",
        "Archive an existing regular workspace file and register its real hash as raw evidence. "
        "Your summary is an interpretation, NOT host scientific verification. Use returned evidence ID with memory/skill tools.",
        dict(path=dict(type="string"), title=dict(type="string"), summary=dict(type="string")),
        ["path", "title", "summary"],
    ),
]


class ScientificTeamService(TeamService):
    def finish(self, actor, *, summary, references, request_id):
        raise ValueError(
            "This task requires actual final files. Review/cancel tasks then call finish_research "
            "with relative mechanism/report paths; it also closes the team submission."
        )


class PairEnvironment:
    def __init__(self, root, store, service, *, remote=None):
        self.root = Path(root).absolute()
        self.store = store
        self.service = service
        self.trajectory = Trajectory(store)
        self.archive = ArtifactArchive(service)
        self.remote = remote or RemoteJobs(self.root, store)
        self._verified_outputs = {}
        from kinetic_agents.research.skills import SkillJobs

        self.skills = SkillJobs(self)
        self.remote.before_dispatch = self.skills.before_dispatch
        self.remote.after_terminal = self.skills.after_terminal
        self.handlers = {
            "research_budget": self.budget,
            "research_compute_submit": self.compute_submit,
            "research_compute_status": self.job_status,
            "research_compute_read": self.job_read,
            "research_skill_compute_submit": self.skills.submit,
            "research_compute_fetch": self.job_fetch,
            "research_record_artifact": self.record_artifact,
            "finish_research": self.finish,
            "research_record_decision": self.record_decision,
            "research_review_read": self.review_read,
            "research_record_query": self.record_query,
        }

    def record_query(self, actor, args, request_id):
        from kinetic_agents.observability.notebook_tools import record_query

        return record_query(self.service, actor, args, request_id)

    def record_decision(self, actor, args, request_id):
        from kinetic_agents.observability.notebook_tools import record

        return record(self.service, actor, args, request_id)

    def review_read(self, actor, args, request_id):
        from kinetic_agents.observability.notebook_tools import read

        return read(self.service, actor, args)

    def compute_submit(self, actor, args, request_id):
        if set(args) != {"argv", "inputs", "outputs", "minutes"}:
            raise ValueError("generic compute keys only; use skill compute for pinned skills")
        return self.remote.submit(actor, args, request_id)

    def tool_specs(self):
        return SPECS

    def activate(self):
        return None

    def execute(self, name, args):
        raise PermissionError("native actor-bound dispatch required")

    def snapshot(self):
        budget = self.remote.budget()
        cpu = 0
        receipt = self.root / "local_cpu.json"
        if receipt.exists():
            cpu = json.loads(receipt.read_text()).get("cpu_seconds", 0)
        left = (
            budget["search_cpu_limit_seconds"]
            - budget["qualification_cpu_seconds"]
            - budget["prior_attempt_cpu_seconds"]
            - budget["remote_charged_or_reserved_seconds"]
            - cpu
        )
        raw = dict(
            jobs=[
                dict(
                    id=r["id"],
                    status="FAILED_UNCERTAIN" if r["status"] == "SUBMIT_UNCERTAIN" else r["status"],
                )
                for r in self.remote.rows()
            ],
            resources=dict(cpu_remaining_seconds=max(0, left)),
            stop=None,
            resource_stopped=False,
        )
        return raw, EnvironmentState.from_snapshot(raw)

    def budget(self, actor, args, request_id):
        if args:
            raise ValueError("no arguments")
        result = self.remote.budget()
        snapshot = self.store.snapshot()
        with self.service.store.connection() as db:
            identity = self.service.store.actor(db, actor)
        result.update(
            model=snapshot["api"],
            model_limit_usd=self.store.get("contract")["api_usd"],
            wall_remaining_seconds=max(0, self.store.get("deadline") - time.time()),
            local_cores=2,
            actor_id=actor,
            actor_role=identity["role"],
            max_team_members=self.service.store.identity.max_members,
            remote_allocation_cores=64,
            evaluator_account="separate; scores do not feed this search",
        )
        return result

    def job_status(self, actor, args, request_id):
        offset = args.get("offset", 0)
        if set(args) - {"offset"} or type(offset) != int or offset < 0:
            raise ValueError("nonnegative offset")
        rows = self.remote.rows()
        selected = rows[offset : offset + 10]
        # Avoid dumping command/source/full manifests into the next prompt.
        keys = (
            "id",
            "status",
            "job_id",
            "created",
            "reserved_seconds",
            "charged_seconds",
            "output_status",
            "error_type",
        )
        return dict(
            rows=[
                {
                    **{k: r[k] for k in keys if k in r},
                    "output_files": [p["path"] for p in r.get("outputs", [])][:64],
                    "total_output_files": len(r.get("outputs", [])),
                }
                for r in selected
            ],
            next_offset=offset + len(selected),
            total=len(rows),
        )

    def job_read(self, actor, args, request_id):
        if set(args) - {"job_id", "path", "offset", "limit"}:
            raise ValueError("unexpected keys")
        ident = args["job_id"]
        name = args["path"]
        offset = args.get("offset", 0)
        limit = args.get("limit", 4096)
        if not re.fullmatch("job_[a-f0-9]{24}", ident):
            raise PermissionError("owned opaque job ID required")
        row = next((r for r in self.remote.rows() if r["id"] == ident), None)
        if not row or row["status"] != "SETTLED":
            raise PermissionError("owned settled job required")
        item = next((r for r in row.get("outputs", []) if r["path"] == name), None)
        if not item:
            raise PermissionError("file not in archived output manifest")
        if type(offset) != int or offset < 0 or type(limit) != int or not 1 <= limit <= 8192:
            raise ValueError("bounded byte cursor required")
        path = self.remote.archive / ident / "output" / name
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise PermissionError("linked archive")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            info = os.fstat(f.fileno())
            signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size != item["bytes"]
            ):
                raise PermissionError("changed archive")
            key = (ident, name)
            if self._verified_outputs.get(key) != signature:
                hasher = hashlib.sha256()
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    hasher.update(chunk)
                if hasher.hexdigest() != item["sha256"]:
                    raise PermissionError("output changed")
                if len(self._verified_outputs) >= 128:
                    self._verified_outputs.pop(next(iter(self._verified_outputs)))
                self._verified_outputs[key] = signature
            f.seek(offset)
            page = f.read(limit)
        return dict(
            path=name,
            sha256=item["sha256"],
            bytes=item["bytes"],
            offset=offset,
            next_offset=min(item["bytes"], offset + limit),
            encoding="base64",
            data=base64.b64encode(page).decode(),
        )

    def job_fetch(self, actor, args, request_id):
        if set(args) != {"job_id", "path", "destination"}:
            raise ValueError("job/path/new destination required")
        checked = self.job_read(
            actor, dict(job_id=args["job_id"], path=args["path"], limit=1), request_id
        )
        data = read_regular(self.remote.archive / args["job_id"] / "output", args["path"])
        if hashlib.sha256(data).hexdigest() != checked["sha256"]:
            raise PermissionError("archive changed")
        try:
            write_new_regular(self.root / "work", args["destination"], data)
        except FileExistsError:
            if read_regular(self.root / "work", args["destination"]) != data:
                raise PermissionError("destination already exists with other bytes")
        return dict(destination=args["destination"], bytes=len(data), sha256=checked["sha256"])

    def record_artifact(self, actor, args, request_id):
        if set(args) != {"path", "title", "summary"}:
            raise ValueError("path/title/summary required")
        data = read_regular(self.root / "work", args["path"], limit=self.archive.MAX_BYTES)
        return self.archive.ingest(
            data,
            evidence_id="evidence_" + request_id[:32],
            title=args["title"],
            category="artifact",
            provenance=dict(source_id=actor, role="development"),
            summary=args["summary"],
        )

    def finish(self, actor, args, request_id):
        if (
            set(args) != {"mechanisms", "report", "outcome"}
            or args["outcome"] not in ("submitted", "incomplete")
            or not isinstance(args["mechanisms"], list)
            or len(args["mechanisms"]) > 3
        ):
            raise ValueError(
                "up to 3 mechanism file paths, report file path, and explicit outcome required"
            )
        with self.service.store.connection() as db:
            self.service._principal(db, actor)
            if any(
                json.loads(r[0])["status"] not in ("accepted", "cancelled")
                for r in db.execute("SELECT value FROM heads WHERE kind='task'")
            ):
                raise ValueError("review or cancel outstanding tasks before final submission")
        if any(r["status"] not in ("SETTLED", "REJECTED") for r in self.remote.rows()):
            raise ValueError(
                "compute is pending; wait for accounting and archive collection before submission"
            )
        folder = self.root / "final_artifacts"
        folder.mkdir(mode=0o700, exist_ok=True)
        manifest = dict(
            outcome=args["outcome"],
            mechanisms=[],
            scientifically_verified=False,
            request_id=request_id,
        )
        for kind, names in [("mechanisms", args["mechanisms"]), ("report", [args["report"]])]:
            for name in names:
                data = read_regular(self.root / "work", name, limit=16 * 1024 * 1024)
                sha = self.archive.put(data)
                target = folder / sha
                if not target.exists():
                    with target.open("xb") as f:
                        f.write(data)
                    target.chmod(0o400)
                entry = dict(path=name, sha256=sha, bytes=len(data))
                if kind == "mechanisms":
                    manifest[kind].append(entry)
                else:
                    manifest[kind] = entry
        if args["outcome"] == "submitted" and not manifest["mechanisms"]:
            raise ValueError(
                "submitted outcome requires at least one mechanism; use incomplete otherwise"
            )
        # Persist request-specific exact bytes before the team final transaction.
        # Its summary points to this immutable ID, so a crash cannot select a
        # later file proposal as an earlier accepted team submission.
        intent = folder / (request_id + ".json")
        if intent.exists() and json.loads(intent.read_text()) != manifest:
            raise PermissionError("final request changed")
        atomic(intent, manifest)
        final = TeamService.finish(
            self.service,
            actor,
            summary="Locked final file manifest " + request_id,
            references=[],
            request_id=request_id,
        )
        atomic(self.root / "submission.json", manifest)
        return dict(
            registered=True,
            scientifically_verified=False,
            outcome=args["outcome"],
            mechanism_hashes=[r["sha256"] for r in manifest["mechanisms"]],
            team_submission_version=final["version"],
        )
