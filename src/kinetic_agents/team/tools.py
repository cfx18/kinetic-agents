"""Explicit native tool schemas; transport, not the model, supplies actor/retry ID."""

from copy import deepcopy
import inspect

from kinetic_agents.team.service import TeamService


OPERATIONS = (
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
READ_ONLY = frozenset({"read", "list", "messages", "artifact_read"})
PIN = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "id": {"type": "string"},
        "version": {"type": "integer", "minimum": 1},
    },
    "required": ["kind", "id", "version"],
    "additionalProperties": False,
}
REFS = {"type": "array", "items": PIN, "maxItems": 32}
DESCRIPTIONS = {
    "delegate": "Principal assigns a versioned task to itself or a native-registered researcher. Does not spawn an agent.",
    "claim": "Assignee (principal or researcher) claims the current version of an assigned ready task.",
    "ask": "Ask the principal (not the human) to resolve a task ambiguity; wait for its answer before continuing.",
    "answer": "Principal resolves a researcher's question; researcher must read and reclaim the revised task.",
    "submit": "Deliver assigned research for principal review. This is not independent scientific verification.",
    "review": "Principal accepts or requests revisions to a delivered task; not a verifier certificate.",
    "cancel": "Principal cancels an outstanding task. Does not kill native processes or erase execution costs.",
    "note": "Version a scratchpad/report/strategy document; use memory/skill for evolution objects.",
    "read": "Read one exact or latest record. Historical records remain readable, including revoked versions.",
    "list": "Search bounded metadata by title/goal and exact status/scope. Read only relevant record versions next.",
    "messages": "Read your own durable inbox. Acknowledge after processing; raw histories are not injected.",
    "ack": "Advance your durable inbox cursor after reading messages.",
    "finish": "Principal locks the ended team submission after tasks and executions settle; evaluator is separate.",
    "memory": "Propose/revise a scoped scientific or procedural claim with evidence and counterexamples. Assessment is not proof.",
    "skill": "Version a method, schemas and immutable code artifact. Principal can authorize trial or revoke; trial is not efficacy.",
    "context": "Materialize selected pinned objects for a task and record retrieval. Does not prove reliance or improvement.",
    "prepare_use": "Record intended use of an authorized skill version. Does NOT execute; use the trusted budgeted executor next.",
    "artifact_read": "Retrieve a bounded byte page of registered raw evidence, base64 encoded; no host paths or foreign evidence.",
}


def schemas():
    result = []
    for operation in OPERATIONS:
        parameters = inspect.signature(getattr(TeamService, operation)).parameters
        properties, required = {}, []
        for name, parameter in parameters.items():
            if name in {"self", "actor", "request_id"}:
                continue
            nullable = parameter.default is None
            if name in {
                "references",
                "supports",
                "counters",
                "derived_from",
                "memory_refs",
                "test_refs",
            }:
                value = deepcopy(REFS)
            elif name in {"artifact_ref", "skill_ref"}:
                value = deepcopy(PIN)
            elif name in {"expected_version", "record_version", "sequence", "limit", "offset"} or (
                name == "after" and operation == "messages"
            ):
                value = {"type": "integer", "minimum": 0}
            elif name in {"input_schema", "output_schema", "applicability"}:
                value = {"type": "object"}
            else:
                value = {"type": "string"}
            if nullable:
                value = {"anyOf": [value, {"type": "null"}]}
            properties[name] = value
            if parameter.default is inspect.Parameter.empty:
                required.append(name)
        result.append(
            {
                "type": "function",
                "name": "team_" + operation,
                "description": DESCRIPTIONS[operation],
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }
        )
    return result


PRINCIPAL_INSTRUCTIONS = """You are the scientific principal: the researchers' requester, not a human-message relay.
You may research, design methods and review evidence yourself. For your own work, team_delegate to your own
principal thread ID, then team_claim; you have the same Memory/Skill/Context/Use APIs as researchers. Self-review
is bookkeeping, not independent validation. If native delegation is disabled, do all work yourself; you may still
parallelize scripts and solvers within the shared allowance. Delegate only useful bounded work using available native
collaboration tools; all members use the same configured model and shared resource account. After native spawn,
use fork_context=true for members that need team APIs: the qualified installed client only inherits dynamic
tools in forked children. This also inherits conversation context; do not claim fresh-context isolation or a
context-cost saving. Prefer spawning before bulky evidence retrieval and reuse workers through native messaging.
assign a versioned task with team_delegate. Researchers ask YOU via team_ask. Resolve ordinary ambiguity with
team_answer and native messaging/follow-up; escalate to the human only for a true scope/authority/budget gate.
Use metadata-first queries: team_list -> select relevant versions -> team_context/read. Preserve raw evidence.
For original bytes use team_artifact_read with an evidence ID and byte cursor; its content is base64 encoded.
Memory and Skill are separate evolving research objects. Cite evidence, scope and counterexamples when revising.
Authorize skill trials explicitly; a hypothesis/trial is not a proven improvement. Retrieval, intended use and
trusted execution receipts are distinct. Revocation prevents future use, not historical inspection.
After context compaction or restart, read your inbox and current tasks instead of replaying all conversations.
Review every deliverable, resolve or cancel open tasks, reconcile unknown executions, then team_finish.
Native turn completion alone is not submission. No human scientific steering is implied by runtime recovery.
The team database and scorer are host-owned. Team APIs neither execute science nor grant additional resources.
"""

RESEARCHER_INSTRUCTIONS = """Work on the principal's assigned task. Use team_messages and team_read to find its
current version, then team_claim. If the acceptance conditions are ambiguous, ask the principal with team_ask;
do not silently invent a different task or repeatedly ask the human. After the answer, read/reclaim the new version.
Select relevant Memory/Skill versions via metadata first. Propose revisions with scope, supporting evidence and
counterexamples. You cannot authorize a trial, certify evidence, or finish the whole research run.
Use native messaging to notify the principal; team_submit records your deliverable for its review.
"""


def principal_instructions(researcher_model=None, researcher_effort=None):
    if researcher_model is None:
        return PRINCIPAL_INSTRUCTIONS
    return PRINCIPAL_INSTRUCTIONS.replace(
        "all members use the same configured model and shared resource account. After native spawn,\n"
        "use fork_context=true for members that need team APIs: the qualified installed client only inherits dynamic\n"
        "tools in forked children. This also inherits conversation context; do not claim fresh-context isolation or a\n"
        "context-cost saving.",
        f"researchers use {researcher_model}/{researcher_effort} and ONE shared resource account. "
        "Use research_spawn(name, message) for a bounded researcher; research_workers reads its status and public reply; "
        "research_followup(name, message) continues the SAME idle worker after you answer its questions or review it. "
        "The transport pins worker models; do not pass model overrides or invoke another model from shell. "
        "Workers are independent native Codex sessions with the same science APIs, not history forks. "
        "Include the bounded assignment and relevant artifact references in message. "
        "research_spawn registers a versioned task automatically; do not duplicate that assignment. ",
    ).replace(
        "Prefer spawning before bulky evidence retrieval and reuse workers through native messaging.\n"
        "assign a versioned task with team_delegate.",
        "Pass only relevant evidence and reuse workers with research_followup. "
        "For your own work, team_delegate can still assign a task to yourself.",
    )
