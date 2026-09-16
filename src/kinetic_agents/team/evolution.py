"""Memory, skill and actual-use records: separate objects, one provenance graph.

Researchers can propose, revise and test knowledge; evidence itself is append-only.
The principal's epistemic assessment is NOT a verifier certificate. A trial can
start before effectiveness is known. Only a trusted execution adapter may attest
execution, and no generic shell or unbudgeted science execution is offered here.
"""

import json
import uuid

from kinetic_agents.team.contracts import Conflict
from kinetic_agents.team.contracts import TaskStatus as S
from kinetic_agents.team.contracts import digest
from kinetic_agents.team.contracts import encode
from kinetic_agents.team.contracts import identifier
from kinetic_agents.team.contracts import text
from kinetic_agents.team.contracts import version


KINDS = frozenset({"document", "memory", "skill", "evidence", "use", "context"})


def scope(value):
    if (
        not isinstance(value, dict)
        or not value
        or len(value) > 24
        or any(
            not isinstance(k, str) or not isinstance(v, str) or not v.strip()
            for k, v in value.items()
        )
        or len(encode(value).encode()) > 4000
    ):
        raise ValueError("scope must be a nonempty, bounded string-to-string map")
    return value


class EvolutionService:
    """Mixin for TeamService; uses the same transactions, identities and journals."""

    def _editable(self, db, actor, kind, key, expected):
        identifier(key)
        version(expected)
        if expected == 0:
            return {"author": actor}
        old = self.store.record(db, kind, key)
        if old["version"] != expected:
            raise Conflict("evolution object changed; re-read before revising")
        if old["author"] != actor:
            self._principal(db, actor)
        return old

    def memory(
        self,
        actor,
        *,
        memory_id,
        expected_version,
        claim,
        applicability,
        supports,
        counters,
        derived_from,
        status,
        rationale,
        request_id
    ):
        text(claim)
        text(rationale)
        scope(applicability)
        if status not in {"hypothesis", "supported", "contested", "archived"}:
            raise ValueError("invalid memory assessment")
        args = dict(
            memory_id=memory_id,
            expected_version=expected_version,
            claim=claim,
            applicability=applicability,
            supports=supports,
            counters=counters,
            derived_from=derived_from,
            status=status,
            rationale=rationale,
        )

        def change(db):
            old = self._editable(db, actor, "memory", memory_id, expected_version)
            if old.get("status") == "archived":
                raise Conflict("archived memory requires a new ID with explicit provenance")
            support_refs, counter_refs = self._refs(db, supports), self._refs(db, counters)
            if any(r["kind"] != "evidence" for r in support_refs + counter_refs):
                raise ValueError("support and counterexamples must cite registered raw evidence")
            if status == "supported" and not support_refs:
                raise ValueError("supported assessment requires evidence")
            if status == "contested" and not counter_refs:
                raise ValueError("contested assessment requires a counterexample")
            if status != "hypothesis":
                self._principal(db, actor)
            value = {
                "author": old["author"],
                "editor": actor,
                "claim": claim,
                "title": claim[:160],
                "applicability": applicability,
                "supports": support_refs,
                "counters": counter_refs,
                "derived_from": self._refs(db, derived_from, allow_inactive=True),
                "status": status,
                "rationale": rationale,
                "assessment_source": "research_team",
                "scientifically_verified": False,
            }
            return self.store.put(db, "memory", memory_id, expected_version, value)

        return self.store.apply(actor, "memory", args, request_id, change)

    def skill(
        self,
        actor,
        *,
        skill_id,
        expected_version,
        title,
        instructions,
        applicability,
        input_schema,
        output_schema,
        artifact_ref,
        memory_refs,
        test_refs,
        status,
        request_id
    ):
        text(title)
        text(instructions)
        scope(applicability)
        for schema in (input_schema, output_schema):
            if not isinstance(schema, dict) or len(encode(schema).encode()) > 8000:
                raise ValueError("bounded input/output schema objects required")
        if status not in {"draft", "trial", "revoked"}:
            raise ValueError(
                "skills are draft, trial or revoked; effectiveness is evaluated separately"
            )
        args = dict(
            skill_id=skill_id,
            expected_version=expected_version,
            title=title,
            instructions=instructions,
            applicability=applicability,
            input_schema=input_schema,
            output_schema=output_schema,
            artifact_ref=artifact_ref,
            memory_refs=memory_refs,
            test_refs=test_refs,
            status=status,
        )

        def change(db):
            old = self._editable(db, actor, "skill", skill_id, expected_version)
            if old.get("status") == "revoked":
                raise Conflict("revoked skill requires a new ID with explicit provenance")
            artifacts = self._refs(db, [artifact_ref])
            memories = self._refs(db, memory_refs, allow_inactive=status != "trial")
            tests = self._refs(db, test_refs)
            if artifacts[0]["kind"] != "evidence":
                raise ValueError("skill artifact must cite an immutable imported artifact")
            artifact = self.store.record(
                db, "evidence", artifacts[0]["id"], artifacts[0]["version"]
            )
            if artifact["category"] != "artifact":
                raise ValueError("observations are not executable artifacts")
            if any(ref["kind"] != "memory" for ref in memories):
                raise ValueError("memory_refs must reference Memory objects")
            if any(ref["kind"] != "evidence" for ref in tests):
                raise ValueError("test_refs must reference evidence")
            if status in {"trial", "revoked"}:
                self._principal(db, actor)
                if expected_version == 0:
                    raise Conflict("create a draft before authorizing or revoking a skill")
            return self.store.put(
                db,
                "skill",
                skill_id,
                expected_version,
                {
                    "author": old["author"],
                    "editor": actor,
                    "title": title,
                    "instructions": instructions,
                    "applicability": applicability,
                    "input_schema": input_schema,
                    "output_schema": output_schema,
                    "artifact_ref": artifacts[0],
                    "artifact_sha256": artifact["artifact_sha256"],
                    "memory_refs": memories,
                    "test_refs": tests,
                    "status": status,
                    "effectiveness": "unproven",
                    "scientifically_verified": False,
                },
            )

        return self.store.apply(actor, "skill", args, request_id, change)

    def context(self, actor, *, task_id, expected_version, references, purpose, request_id):
        """Materialize a bounded, pinned context and record what was returned.

        This attests retrieval, not that the model relied on it or gained benefit.
        """
        text(purpose)
        args = dict(
            task_id=task_id,
            expected_version=expected_version,
            references=references,
            purpose=purpose,
        )

        def change(db):
            task = self._task(db, actor, task_id, expected_version, {S.READY, S.RUNNING})
            if actor not in {task["assignee"], task["principal"]}:
                raise PermissionError("context must belong to your task")
            refs = self._refs(db, references)
            values = [self.store.record(db, r["kind"], r["id"], r["version"]) for r in refs]
            if len(encode(values).encode()) > 24000:
                raise ValueError("context exceeds 24 KiB; request fewer relevant objects")
            row = self.store.put(
                db,
                "context",
                "context_" + uuid.uuid4().hex,
                0,
                {
                    "actor": actor,
                    "task_id": task_id,
                    "task_version": expected_version,
                    "references": refs,
                    "purpose": purpose,
                    "returned_content_sha256": digest(values),
                    "meaning": "objects returned, not proof of reliance or benefit",
                },
            )
            return {**row, "objects": values}

        return self.store.apply(actor, "context", args, request_id, change)

    def prepare_use(self, actor, *, task_id, expected_version, skill_ref, context_id, request_id):
        """Reserve a pinned execution intent; NEVER claim a tool has already run."""
        args = dict(
            task_id=task_id,
            expected_version=expected_version,
            skill_ref=skill_ref,
            context_id=context_id,
        )

        def change(db):
            self._task(db, actor, task_id, expected_version, {S.RUNNING}, assignee=True)
            refs = self._refs(db, [skill_ref])
            if refs[0]["kind"] != "skill":
                raise ValueError("skill_ref must identify a skill")
            skill = self.store.record(db, "skill", refs[0]["id"], refs[0]["version"])
            if skill["status"] != "trial":
                raise PermissionError("principal must authorize this exact skill version for trial")
            ctx = self.store.record(db, "context", context_id)
            if (
                ctx["actor"] != actor
                or ctx["task_id"] != task_id
                or ctx["task_version"] != expected_version
                or refs[0] not in ctx["references"]
            ):
                raise Conflict(
                    "execution must bind an own, current-task context containing the skill"
                )
            self._refs(db, skill["memory_refs"])
            return self.store.put(
                db,
                "use",
                "use_" + uuid.uuid4().hex,
                0,
                {
                    "actor": actor,
                    "task_id": task_id,
                    "task_version": expected_version,
                    "skill_ref": refs[0],
                    "context_id": context_id,
                    "artifact_sha256": skill["artifact_sha256"],
                    "status": "intended",
                    "execution_receipt": None,
                    "meaning": "intent only; execution requires a trusted receipt",
                },
            )

        return self.store.apply(actor, "prepare_use", args, request_id, change)

    def import_evidence(
        self, *, evidence_id, title, category, artifact_sha256, provenance, summary
    ):
        """Trusted environment API, intentionally NOT an agent-facing tool.

        The environment is responsible for hashing the actual artifact and
        retaining it. This record does not certify the scientific interpretation.
        Final-evaluation feedback is not admitted into this research memory.
        """
        identifier(evidence_id)
        text(title)
        text(summary)
        if category not in {"artifact", "observation", "test", "diagnostic"}:
            raise ValueError("invalid evidence category")
        if (
            not isinstance(artifact_sha256, str)
            or len(artifact_sha256) != 64
            or any(c not in "0123456789abcdef" for c in artifact_sha256)
        ):
            raise ValueError("actual artifact SHA-256 required")
        if (
            not isinstance(provenance, dict)
            or set(provenance) != {"source_id", "role"}
            or provenance["role"] not in {"development", "public_source", "synthetic"}
        ):
            raise PermissionError(
                "only scoped research evidence may enter memory; not final evaluation"
            )
        identifier(provenance["source_id"])
        value = dict(
            title=title,
            category=category,
            artifact_sha256=artifact_sha256,
            provenance=provenance,
            summary=summary,
            status="recorded",
            scientifically_verified=False,
        )
        with self.store.connection(write=True) as db:
            prior = db.execute(
                "SELECT value FROM heads WHERE kind='evidence' AND id=?", (evidence_id,)
            ).fetchone()
            if prior:
                if json.loads(prior[0]) != {**value, "id": evidence_id, "version": 1}:
                    raise Conflict(
                        "raw evidence cannot be overwritten; append a correction with a new ID"
                    )
                return json.loads(prior[0])
            if db.execute("SELECT 1 FROM heads WHERE kind='submission'").fetchone():
                raise Conflict("ended run is immutable")
            result = self.store.put(db, "evidence", evidence_id, 0, value)
            self.store.event(db, "environment", "evidence_imported", evidence_id, 1)
            return result

    def attest_execution(self, *, use_id, receipt_id, artifact_sha256, status, output_refs):
        """Trusted executor callback. Unknown is not failure/success or a free retry."""
        identifier(receipt_id)
        if status not in {"succeeded", "failed", "unknown"}:
            raise ValueError("invalid execution status")
        with self.store.connection(write=True) as db:
            use = self.store.record(db, "use", use_id)
            if use["artifact_sha256"] != artifact_sha256:
                raise PermissionError("executor ran a different artifact than the pinned skill")
            refs = self._refs(db, output_refs)
            if any(r["kind"] != "evidence" for r in refs):
                raise ValueError("execution outputs must be evidence")
            result = {
                **use,
                "status": status,
                "execution_receipt": receipt_id,
                "outputs": refs,
                "meaning": "execution observed; effectiveness not inferred",
            }
            if use["status"] != "intended":
                if all(use.get(k) == v for k, v in result.items()):
                    return use
                raise Conflict("execution already attested; reconcile unknown outcomes explicitly")
            prior = db.execute(
                "SELECT use_id FROM execution_receipts WHERE receipt=?", (receipt_id,)
            ).fetchone()
            if prior and prior[0] != use_id:
                raise Conflict("execution receipt already belongs to another use")
            db.execute("INSERT INTO execution_receipts VALUES (?,?)", (receipt_id, use_id))
            result = self.store.put(db, "use", use_id, use["version"], result)
            self.store.event(
                db, "environment", "skill_execution_" + status, use_id, result["version"]
            )
            return result
