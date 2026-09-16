"""Native app-server event adapter, not a replacement reasoning/execution loop.

Feed ONLY authenticated messages from the owned app-server process. This adapter
does not make attacker-supplied JSON trustworthy or implement an OS sandbox.
"""

from collections import OrderedDict
import json

from kinetic_agents.team.contracts import Conflict
from kinetic_agents.team.contracts import digest
from kinetic_agents.team.contracts import encode
from kinetic_agents.team.contracts import identifier
from kinetic_agents.team.tools import OPERATIONS
from kinetic_agents.team.tools import READ_ONLY


class TeamRouter:
    def __init__(self, service):
        self.service = service
        self.members = {a["id"]: a for a in service.store.status()["agents"]}
        self.cache = OrderedDict()

    def bind_principal(self, thread_id):
        self.service.store.register(thread_id)
        self.members[thread_id] = {"id": thread_id, "parent": None, "role": "principal"}

    def observe(self, message):
        """No database access for ordinary deltas/heartbeats. Spawn is a state change."""
        if "id" in message:
            return False
        if message.get("method") == "item/completed":
            params = message.get("params") or {}
            item = params.get("item") or {}
            if (
                item.get("type") != "collabAgentToolCall"
                or item.get("tool") not in {"spawnAgent", "closeAgent", "resumeAgent"}
                or item.get("status") != "completed"
            ):
                return False
            parent = item.get("senderThreadId")
            if parent != params.get("threadId") or parent not in self.members:
                raise PermissionError("native spawn has a foreign sender")
            if item["tool"] in {"closeAgent", "resumeAgent"}:
                for child in item.get("receiverThreadIds", []):
                    if child not in self.members:
                        raise PermissionError("foreign native lifecycle target")
                    self.service.store.native_lifecycle(child, closed=item["tool"] == "closeAgent")
                return True
            identity = self.service.store.identity
            model = identity.researcher_model or identity.model
            effort = identity.researcher_effort or identity.effort
            if (
                item.get("model") not in (None, model)
                or item.get("reasoningEffort") not in (None, effort)
                or identity.researcher_model is not None
                and (item.get("model"), item.get("reasoningEffort")) != (model, effort)
            ):
                raise PermissionError(
                    "native child requested a different model or reasoning effort"
                )
            for child in item.get("receiverThreadIds", []):
                identifier(child)
                self.service.store.register(child, parent)
                self.members[child] = {"id": child, "parent": parent, "role": "researcher"}
            return True
        if message.get("method") != "thread/started":
            return False
        thread = (message.get("params") or {}).get("thread") or {}
        source = thread.get("source")
        sub = source.get("subAgent") if isinstance(source, dict) else None
        spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
        if not isinstance(spawn, dict):
            return False
        parent = spawn.get("parent_thread_id")
        if parent not in self.members:
            raise PermissionError("native child has a foreign parent")
        child = identifier(thread.get("id"))
        self.service.store.register(child, parent)
        self.members[child] = {"id": child, "parent": parent, "role": "researcher"}
        return True

    def dispatch(self, params):
        actor = params.get("threadId")
        name = params.get("tool", "")
        operation = name.removeprefix("team_")
        if (
            actor not in self.members
            or not name.startswith("team_")
            or operation not in OPERATIONS
            or params.get("namespace") not in (None, "")
        ):
            raise PermissionError("foreign thread or unsupported team tool")
        with self.service.store.connection() as db:
            self.service.store.actor(db, actor)
        call, turn = params.get("callId"), params.get("turnId")
        identifier(call)
        identifier(turn)
        arguments = params.get("arguments")
        if not isinstance(arguments, dict) or len(encode(arguments).encode()) > 48000:
            raise ValueError("bounded tool arguments required")
        if "actor" in arguments or "request_id" in arguments:
            raise PermissionError("actor and retry identity are transport-owned")
        key = digest([actor, turn, call])
        request_digest = digest([operation, arguments])
        if key in self.cache:
            prior_digest, result = self.cache[key]
            if prior_digest != request_digest:
                raise Conflict("native call ID reused with different arguments")
            return result
        args = dict(arguments)
        if operation not in READ_ONLY:
            args["request_id"] = key
        value = self.service.call(actor, operation, args)
        result = {"success": True, "contentItems": [{"type": "inputText", "text": encode(value)}]}
        self.cache[key] = (request_digest, result)
        if len(self.cache) > 128:
            self.cache.popitem(last=False)
        return result

    @staticmethod
    def error(exc):
        # No provider bodies, file paths, keys or arbitrary exception messages.
        message = (
            str(exc)[:240]
            if isinstance(exc, (ValueError, PermissionError))
            else "Tool failed; inspect host diagnostic category."
        )
        return {
            "success": False,
            "contentItems": [
                {
                    "type": "inputText",
                    "text": json.dumps({"error_type": type(exc).__name__, "message": message}),
                }
            ],
        }
