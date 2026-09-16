"""Actor-bound host tool dispatch for native principals AND researchers.

Callbacks receive (actor, arguments, request_id); they must enforce scientific
permissions and durable idempotency at their own effect boundary. Request IDs
survive router restart. This adapter neither creates CPU allowances nor launches
science. Slow jobs must be enqueued by callbacks, not awaited in the native event
loop. Ordinary notifications do not touch this adapter or the budget database.
"""

from collections import OrderedDict
from copy import deepcopy

from kinetic_agents.team.contracts import Conflict
from kinetic_agents.team.contracts import digest
from kinetic_agents.team.contracts import encode
from kinetic_agents.team.contracts import identifier


class ScopedScienceTools:
    def __init__(self, team_router, handlers, *, read_only=()):
        if not isinstance(handlers, dict) or any(not callable(fn) for fn in handlers.values()):
            raise ValueError("explicit host callbacks required")
        for name in handlers:
            identifier(name)
            if name.startswith("team_"):
                raise ValueError("science tools cannot replace team APIs")
        if not set(read_only) <= set(handlers):
            raise ValueError("read-only tool must be explicitly registered")
        self.team, self.handlers = team_router, dict(handlers)
        self.read_only, self.cache = frozenset(read_only), OrderedDict()

    def dispatch(self, params, *, guard=None):
        actor, name = params.get("threadId"), params.get("tool")
        if (
            actor not in self.team.members
            or name not in self.handlers
            or params.get("namespace") not in (None, "")
        ):
            raise PermissionError("foreign actor or unregistered science tool")
        with self.team.service.store.connection() as db:
            self.team.service.store.actor(
                db, actor
            )  # Closed native workers cannot dispatch or replay cached effects.
        turn, call = identifier(params.get("turnId")), identifier(params.get("callId"))
        args = params.get("arguments")
        if not isinstance(args, dict) or len(encode(args).encode()) > 48000:
            raise ValueError("bounded science tool arguments required")
        if "actor" in args or "request_id" in args:
            raise PermissionError("actor and request identity are transport-owned")
        # Team/run identity matters even if a backend accidentally shares a DB.
        key = digest([self.team.service.store.identity.as_dict(), actor, turn, call])
        wanted = digest([name, args])
        if key in self.cache:
            prior, result = self.cache[key]
            if prior != wanted:
                raise Conflict("native call ID reused with different arguments")
            return deepcopy(result)
        if guard is not None and name not in self.read_only:
            guard()  # Only effect requests, never token/progress notifications.
        value = self.handlers[name](actor, deepcopy(args), key)
        output = encode(value)
        if len(output.encode()) > 64000:
            # No silent truncation; backend must retain raw bytes and paginate.
            raise ValueError("host tool result exceeds bound; expose an artifact or page")
        result = {"success": True, "contentItems": [{"type": "inputText", "text": output}]}
        self.cache[key] = (wanted, result)
        if len(self.cache) > 128:
            self.cache.popitem(last=False)
        return deepcopy(result)
