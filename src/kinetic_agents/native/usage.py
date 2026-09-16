"""Host-only per-actor model/usage observations, not an API dollar invoice."""

import json
from pathlib import Path
import time
import threading
from functools import wraps

from kinetic_agents.core.storage import atomic
from kinetic_agents.team.contracts import identifier


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)

    return call


class SubscriptionTeamState:
    def __init__(self, state, identity, billing="ChatGPT_subscription"):
        self.state, self.identity = Path(state), identity
        self.billing = billing
        self.lock = threading.RLock()
        self.path = self.state / "subscription_team.json"
        self.data = (
            json.loads(self.path.read_text()) if self.path.exists() else {"agents": {}, "usage": {}}
        )
        if self.data.get("identity", identity.as_dict()) != identity.as_dict():
            raise PermissionError("subscription team observation belongs to a different contract")
        self.data["identity"] = identity.as_dict()

    @locked
    def bind(self, actor, parent, model, effort, *, observed):
        identifier(actor)
        previous = self.data["agents"].get(actor)
        value = dict(
            id=actor,
            parent=parent,
            role="principal" if parent is None else "researcher",
            model=model,
            effort=effort,
            model_observed=observed,
        )
        if previous and any(previous[k] != value[k] for k in ("parent", "model", "effort")):
            raise PermissionError("subscription actor model identity changed")
        if previous and previous["model_observed"]:
            value["model_observed"] = True
        self.data["agents"][actor] = value
        self.save()

    @locked
    def usage(self, actor, usage):
        if actor not in self.data["agents"]:
            return False
        safe = {
            kind: {k: v for k, v in row.items() if type(v) is int and v >= 0}
            for kind, row in usage.items()
            if kind in ("total", "last") and isinstance(row, dict)
        }
        # Native totals replace prior snapshots. Do NOT sum repeated/forked
        # histories or silently convert included allowance into dollars.
        self.data["usage"][actor] = safe
        self.save()
        return True

    def save(self):
        self.data["updated_at"] = time.time()
        atomic(self.path, self.data)
        principal = next((a for a in self.data["agents"].values() if a["parent"] is None), None)
        by_model = {}
        for actor, usage in self.data["usage"].items():
            model = self.data["agents"][actor]["model"]
            by_model.setdefault(model, []).append({"actor_id": actor, "token_usage": usage})
        atomic(
            self.state / "subscription_usage.json",
            dict(
                thread_id=principal["id"] if principal else None,
                token_usage=(self.data["usage"].get(principal["id"], {}) if principal else {}),
                actors=self.data["agents"],
                usage_by_model=by_model,
                billing=self.billing,
                api_usd=None,
                api_request_count=None,
                interpretation="per-thread native cumulative snapshots, NOT a dollar/credit invoice; server caching/usage semantics remain authoritative",
                auxiliary_calls_included="all_observed_owned_threads; missing usage remains unknown",
                at=time.time(),
            ),
        )
