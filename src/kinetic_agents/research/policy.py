"""Team protocol adapter for the EXISTING durable native lifecycle.

No new inference loop, retry allowance, budget, monitor, or science algorithm.
The supplied environment remains authoritative for jobs and resource limits.
"""

from kinetic_agents.core.contracts import fingerprint
from kinetic_agents.core.runtime import run_native
from kinetic_agents.core.runtime import TERMINAL
from kinetic_agents.core.state import EnvironmentState
from kinetic_agents.team.tools import schemas
from kinetic_agents.team.tools import PRINCIPAL_INSTRUCTIONS
from kinetic_agents.team.tools import RESEARCHER_INSTRUCTIONS
from kinetic_agents.team.tools import principal_instructions


CONTINUATION = (
    "The research submission has not been recorded. Read team_messages and the current "
    "task metadata. Answer pending researcher questions, review delivered work, and use "
    "the native agent messaging/wait tools to continue your registered researchers. "
    "An assistant reply is not a team submission. Continue your own research if useful, "
    "or explicitly review/cancel remaining tasks and call the scientific task's finish_research "
    "when available (otherwise team_finish) for an honest "
    "completed or incomplete result. Do not duplicate pending executions. This notice "
    "adds no scientific hypothesis, preferred candidate, or new resource allowance."
)


class TeamPolicy:
    policy_id = "native-team-lifecycle"
    version = "6"

    def __init__(self, max_members=None, researcher_model=None, researcher_effort=None):
        self.max_members = max_members
        self.researcher_model, self.researcher_effort = researcher_model, researcher_effort

    def start(self, context):
        facts = runtime_facts(self.max_members, context.task_directory)
        if self.researcher_model is not None:
            facts = facts.replace(
                "native delegation = enabled", "delegation = host-scoped native workers"
            )
            facts += (
                f"Researcher model = {self.researcher_model}; researcher reasoning effort = {self.researcher_effort}. "
                "Use native subagents for bounded independent work when useful, not for work you must repeat yourself. "
                "Use research_spawn/research_followup/research_workers, with fixed models and explicitly installed shared scientific tools. "
                "All researchers share this run's TOTAL resources, not one allowance per agent. "
                "Use separate work/agents/<actor-id>/ areas for concurrent writes; hand over artifact paths and evidence. "
                "The principal reviews results and alone submits final files. This is a mixed-model system, "
                "not a model-controlled architecture-only comparison.\n"
            )
        protocol = (
            facts
            + principal_instructions(self.researcher_model, self.researcher_effort)
            + "\nResearcher protocol:\n"
            + RESEARCHER_INSTRUCTIONS
        )
        if context.resumed:
            return (
                protocol + "\nYour SAME registered principal thread has resumed. "
                "Original task, raw evidence, memory versions and deadlines remain unchanged. "
                "Read current records, not the whole chat.\n" + self.continuation()
            )
        return protocol + "\nScientific task (unchanged):\n" + context.task_text

    def continuation(self):
        return (
            CONTINUATION
            if self.researcher_model is None
            else CONTINUATION.replace(
                "the native agent messaging/wait tools", "research_workers and research_followup"
            )
        )

    def identity(self):
        result = {
            "id": self.policy_id,
            "version": self.version,
            "max_members_including_principal": self.max_members,
            "protocol_sha256": fingerprint(
                [PRINCIPAL_INSTRUCTIONS, RESEARCHER_INSTRUCTIONS, CONTINUATION]
            ),
        }
        if self.researcher_model is not None:
            result.update(
                version="7-mixed",
                researcher_model=self.researcher_model,
                researcher_effort=self.researcher_effort,
            )
            result["protocol_sha256"] = fingerprint(
                [
                    principal_instructions(self.researcher_model, self.researcher_effort),
                    RESEARCHER_INSTRUCTIONS,
                    self.continuation(),
                ]
            )
        return result


def runtime_facts(max_members, task_directory):
    delegation = (
        "disabled"
        if max_members == 1
        else "enabled" if max_members is not None else "see the native tool surface"
    )
    return (
        f"Runtime facts (not scientific advice): task directory = {task_directory}; "
        f"maximum total team members including you = {max_members}; native delegation = {delegation}. "
        "When disabled, no child creation interface exists; do the task yourself. "
        "Do not search for hidden delegation interfaces or try to launch another model client from shell. "
        "If research_budget is available, it gives your actor ID and current resources. "
        "In a forked child, inherited principal instructions describe the parent, not you: "
        "your latest delegation and the authoritative actor_role govern your role. "
        "Only the principal spawns researchers. Researchers reuse their own thread for follow-up work. "
        "If the scientific task requires finish_research, use it to lock actual files and close the team; "
        "it replaces the generic team_finish terminal step. "
        "When research_record_decision is available, record brief decision justifications at material changes "
        "in method, case selection, candidate acceptance/rejection, delegation, validation, memory/skills or stopping. "
        "Cite available evidence, distinguish expected from observed outcomes, and revise the same decision version "
        "when contradicted. Do not reconstruct hidden reasoning or invent missing reasons. "
        "Prefer Chinese brief public review records. After each substantive literature, code, data or memory "
        "query, use research_record_query when available: exact keywords/filters/file sections, returned "
        "facts, what you consider important and why, plus its effect on your next action (or no change). "
        "Distinguish no useful findings from unknown importance. Group pages of the same query, not unrelated "
        "queries; ordinary heartbeat polling needs no interpretive note. Do not claim a source was read "
        "or a finding verified just because search returned it. "
        "Use research_review_read to retrieve this run's decision/notebook metadata and selected versions "
        "after compaction or when planning a revision; do not replay the entire history.\n"
    )


class TeamEnvironment:
    def __init__(self, service, environment):
        self.team, self.environment, self.trajectory = service, environment, environment.trajectory

    def tool_specs(self):
        team = [
            {"name": s["name"], "description": s["description"], "parameters": s["inputSchema"]}
            for s in schemas()
        ]
        science = self.environment.tool_specs()
        if any(s["name"].startswith("team_") or s["name"] == "stop_search" for s in science):
            raise PermissionError(
                "team submission is the only stop authority; tool names must not collide"
            )
        return team + science

    def snapshot(self):
        raw, state = self.environment.snapshot()
        # Check authoritative completion BEFORE treating an exhausted search
        # account as a reason to discard an already-registered submission.
        with self.team.store.connection() as db:
            row = db.execute("SELECT 1 FROM heads WHERE kind='submission'").fetchone()
            final = self.team.store.record(db, "submission", "final") if row else None
        if raw.get("stop") and final is None:
            raise PermissionError("environment stop cannot masquerade as a team submission")
        if final is None:
            return raw, state
        raw = {**raw, "stop": final}
        return raw, EnvironmentState.from_snapshot(raw)

    def activate(self):
        return self.environment.activate()

    def execute(self, name, args):
        if name.startswith("team_"):
            raise PermissionError(
                "team actor must come from the native transport, not an environment argument"
            )
        return self.environment.execute(name, args)


def run_team(
    root,
    store,
    contract,
    *,
    service,
    environment,
    harness_factory,
    gateway_factory,
    finalize,
    save,
    task_manifest,
    adapter_identity,
    **runtime_options,
):
    """Host integration only. Does not create or enlarge an account/CPU owner."""
    identity = service.store.identity
    if (
        contract["model"] != identity.model
        or contract["effort"] != identity.effort
        or task_manifest.get("TASK.md") != identity.task_sha256
    ):
        raise PermissionError("team model or original task differs from the runtime contract")
    if "policy" in runtime_options:
        raise PermissionError("team lifecycle policy is versioned, not silently replaceable")

    def make_client(url, token, dynamic):
        client = harness_factory(url, token, dynamic)

        # This hook executes only at dynamic tool requests, never at token deltas.
        def guard():
            if store.advance("tick") in TERMINAL | {"FINALIZING"}:
                raise PermissionError("original run ended; no new team mutation")

        client.team_dispatch_guard = guard
        return client

    return run_native(
        root,
        store,
        contract,
        environment=TeamEnvironment(service, environment),
        harness_factory=make_client,
        gateway_factory=gateway_factory,
        finalize=finalize,
        save=save,
        task_manifest=task_manifest,
        adapter_identity={
            **adapter_identity,
            "team_identity_sha256": fingerprint(identity.as_dict()),
        },
        policy=TeamPolicy(
            identity.max_members, identity.researcher_model, identity.researcher_effort
        ),
        **runtime_options,
    )
