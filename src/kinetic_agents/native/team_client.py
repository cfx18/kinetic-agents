"""Reuse the installed Codex loop; add scoped team-tool and native-child routing.

This is an experimental adapter, not an authorization to start a paid run. Its
environment, all provider traffic and scientific tools still need a qualified
host launcher and shared cost accounts. No original run profile is changed.
"""

from pathlib import Path
import hashlib

from kinetic_agents.native.client import NativeClient
from kinetic_agents.team.router import TeamRouter
from kinetic_agents.team.tools import schemas
from kinetic_agents.team.tools import PRINCIPAL_INSTRUCTIONS
from kinetic_agents.team.tools import RESEARCHER_INSTRUCTIONS
from kinetic_agents.team.tools import READ_ONLY
from kinetic_agents.native.profiles import NativeTeamConfig
from kinetic_agents.native.profiles import NativeSoloConfig
from kinetic_agents.native.scoped_tools import ScopedScienceTools


class NativeTeamProtocolFault(PermissionError):
    """Safe structured host diagnostic, never arbitrary provider/credential text."""

    def __init__(self, exc):
        self.diagnostic_code = {
            "registered team capacity exceeded": "native_team_capacity_mismatch",
            "native child has a foreign parent": "native_child_foreign_parent",
            "native spawn has a foreign sender": "native_spawn_foreign_sender",
            "native child requested a different model or reasoning effort": "native_child_model_mismatch",
        }.get(str(exc), "native_team_protocol_violation")
        super().__init__(self.diagnostic_code)


class NativeTeamClient(NativeClient):
    allowed_team_sizes = {1, 4}

    def __init__(
        self,
        work,
        task,
        state,
        model,
        effort,
        gateway_url,
        token,
        service,
        *,
        extra_tools=(),
        actor_tools=None,
        actor_read_only=()
    ):
        identity = service.store.identity
        if (
            identity.model != model
            or identity.effort != effort
            or identity.max_members not in self.allowed_team_sizes
        ):
            raise PermissionError("team/native identity mismatch")
        work = Path(work).resolve()
        if service.store.root.resolve().is_relative_to(work) or Path(
            state
        ).resolve().is_relative_to(work):
            raise PermissionError("team and native state must be outside agent-writable work")
        self.team = TeamRouter(service)
        if any(t.get("name", "").startswith("team_") for t in extra_tools):
            raise ValueError("extra tools cannot replace the team protocol")
        if actor_tools is not None and not set(actor_tools) <= {t.get("name") for t in extra_tools}:
            raise ValueError("actor-bound callbacks need an explicit matching tool schema")
        self.science_tools = ScopedScienceTools(
            self.team, actor_tools or {}, read_only=actor_read_only
        )
        self.team_dispatch_guard = None
        self._transport_init(
            work,
            task,
            state,
            model,
            effort,
            gateway_url,
            token,
            schemas() + list(extra_tools),
            baseline_config=NativeSoloConfig() if identity.max_members == 1 else NativeTeamConfig(),
        )

    def _transport_init(self, *args, **kwargs):
        NativeClient.__init__(self, *args, **kwargs)

    def start_or_resume(self, owned_thread=None):
        principals = [a["id"] for a in self.team.members.values() if a["role"] == "principal"]
        if principals != ([owned_thread] if owned_thread else []):
            raise PermissionError(
                "resume must use the registered principal; no fresh thread over old state"
            )
        result = super().start_or_resume(owned_thread)
        self.team.bind_principal(result["thread_id"])
        return result

    def begin_research(self, task_text):
        if (
            hashlib.sha256(task_text.encode()).hexdigest()
            != self.team.service.store.identity.task_sha256
        ):
            raise PermissionError("scientific task text differs from the registered contract")
        from kinetic_agents.research.policy import runtime_facts
        from kinetic_agents.team.tools import principal_instructions

        identity = self.team.service.store.identity
        return self.begin(
            runtime_facts(self.team.service.store.identity.max_members, str(self.task))
            + principal_instructions(identity.researcher_model, identity.researcher_effort)
            + "\nResearcher protocol to include when delegating:\n"
            + RESEARCHER_INSTRUCTIONS
            + "\nScientific task (unchanged):\n"
            + task_text
        )

    def handle_message(self, message):
        try:
            if self.team.observe(message):
                return
        except PermissionError as exc:
            # Unknown/foreign workers must still fail closed. Normal capacity
            # rejection happens inside Codex BEFORE spawning (qualified test).
            raise NativeTeamProtocolFault(exc) from exc
        params = message.get("params") or {}
        if (
            "id" in message
            and message.get("method") == "item/tool/call"
            and params.get("tool") in self.science_tools.handlers
        ):
            try:
                result = self.science_tools.dispatch(params, guard=self.team_dispatch_guard)
            except Exception as exc:
                result = self.team.error(exc)
            self.send({"id": message["id"], "result": result})
            return
        if (
            "id" in message
            and message.get("method") == "item/tool/call"
            and str(params.get("tool", "")).startswith("team_")
        ):
            try:
                if self.team_dispatch_guard and params["tool"][5:] not in READ_ONLY:
                    self.team_dispatch_guard()
                result = self.team.dispatch(params)
            except Exception as exc:
                result = self.team.error(exc)
            self.send({"id": message["id"], "result": result})
            return
        return super().handle_message(message)
