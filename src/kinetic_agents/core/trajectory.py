"""Host-written event envelopes; raw science stays in its existing evidence store.

No prompts, credentials, result bodies or arbitrary exception text enter the
monitoring stream. Hashes bind tool inputs/results without duplicating large data.
This is observability, not a substitute for authoritative billing or raw logs.
"""

import time
from uuid import uuid4

from kinetic_agents.core.contracts import RuntimeIdentity
from kinetic_agents.core.contracts import fingerprint


class Trajectory:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock
        self.session = {}

    def bind_turn(self, thread_id, turn_id, policy_id, policy_version):
        self.session = {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "policy_id": policy_id,
            "policy_version": policy_version,
        }

    def record(self, kind, **payload):
        if not kind.startswith("RR_"):
            raise ValueError("research event namespace required")
        # Kept out of mutable key/value state: many simultaneous tool calls must
        # not overwrite a single latest-action record.
        with self.store.tx() as db:
            self.store._event(db, kind, payload)

    def bind(self, identity: RuntimeIdentity):
        value = identity.as_dict()
        with self.store.tx() as db:
            old = self.store._get(db, "research_runtime_identity")
            if old is not None:
                if old != value:
                    raise PermissionError(
                        "runtime identity changed; create an explicit new experiment version"
                    )
                return
            legacy = self.store._get(db, "thread_id") is not None
            self.store._put(db, "research_runtime_identity", value)
            self.store._event(
                db,
                "RR_IDENTITY_BOUND",
                {
                    **value,
                    "historical_coverage": "from_this_binding_only" if legacy else "from_run_start",
                },
            )

    def start_tool(self, tool_name, args):
        action_id = uuid4().hex
        payload = {
            **self.session,
            "action_id": action_id,
            "tool_name": tool_name,
            "arguments_sha256": fingerprint(args),
            "status": "started",
        }
        # Request IDs link reconnects; input content is never a monitoring label.
        if isinstance(args.get("request_id"), str):
            payload["request_id_sha256"] = fingerprint(args["request_id"])
        self.record("RR_TOOL_STARTED", **payload)
        return action_id

    def finish_tool(self, action_id, tool_name, result, elapsed):
        self.record(
            "RR_TOOL_FINISHED",
            action_id=action_id,
            tool_name=tool_name,
            status="returned",
            result_sha256=fingerprint(result),
            duration_seconds=max(0.0, elapsed),
        )

    def fail_tool(self, action_id, tool_name, exc, elapsed):
        self.record(
            "RR_TOOL_FAILED",
            action_id=action_id,
            tool_name=tool_name,
            status="failed",
            error_type=type(exc).__name__,
            duration_seconds=max(0.0, elapsed),
        )
