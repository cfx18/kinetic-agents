"""Bounded worker lifecycle; each worker is an unchanged native Codex tool loop.

No prompt-generated shell broker, model proxy, extra account or hidden supervisor
LLM. Native thread IDs bind tools to the shared authoritative research service.
"""

import json
from pathlib import Path
import threading
import time

from kinetic_agents.native.client import NativeClient
from kinetic_agents.core.storage import atomic
from kinetic_agents.team.contracts import identifier
from kinetic_agents.team.contracts import text
from kinetic_agents.team.tools import RESEARCHER_INSTRUCTIONS
from kinetic_agents.native.profiles import NativeSoloConfig
from kinetic_agents.native.subscription import SubscriptionTeamClient


class ResearcherClient(SubscriptionTeamClient):
    is_researcher = True

    def __init__(self, parent, state):
        self.parent = parent
        self.team = parent.team
        self.science_tools = parent.science_tools
        self.team_dispatch_guard = parent.team_dispatch_guard
        self.shared_observations = parent.team_observations
        self.transcript_root = parent.transcript.root
        self.worker_pool = None
        self.provider = parent.provider
        self.executable = getattr(parent, "executable", None)
        identity = self.team.service.store.identity
        self._transport_init(
            parent.work,
            parent.task,
            state,
            identity.researcher_model,
            identity.researcher_effort,
            parent.account,
            None,
            parent.specs,
            baseline_config=NativeSoloConfig(),
        )
        self.subscription_stop = parent.subscription_stop

    def prepare_account(self):
        # The parent catalogs all configured models before any worker exists.
        # A test subclass may replace this hook; production uses official auth.
        return self.parent.prepare_worker_account(self)

    def start_or_resume(self, owned_thread=None):
        if (
            owned_thread
            and self.team.members.get(owned_thread, {}).get("parent") != self.parent.thread_id
        ):
            raise PermissionError("worker resume outside the registered team")
        result = NativeClient.start_or_resume(self, owned_thread)
        actor = result["thread_id"]
        self.team.service.store.register(actor, self.parent.thread_id)
        if owned_thread:
            self.team.service.store.native_lifecycle(actor, closed=False)
        self.team.members[actor] = dict(id=actor, parent=self.parent.thread_id, role="researcher")
        self.team_observations.bind(
            actor, self.parent.thread_id, self.model, self.effort, observed=True
        )
        self.transcript.record(
            "actor_registered",
            dict(
                threadId=actor,
                parent=self.parent.thread_id,
                role="researcher",
                model=self.model,
                effort=self.effort,
                native_metadata_verified=True,
            ),
        )
        return result


class WorkerPool:
    def __init__(self, parent):
        self.parent = parent
        self.lock = threading.RLock()
        self.rows = {}
        self.running = {}
        self.clients = {}
        self.closing = threading.Event()

    @property
    def path(self):
        return self.parent.state / "workers.json"

    def load(self):
        if self.path.exists():
            self.rows = json.loads(self.path.read_text())
        for row in self.rows.values():
            if row["status"] in ("STARTING", "RUNNING"):
                row["status"] = "INTERRUPTED"
                row["reason"] = (
                    "previous native process ended; explicit followup resumes the same worker"
                )
                if row.get("actor_id"):
                    self.parent.team.service.store.native_lifecycle(row["actor_id"], closed=True)
        self.save()

    def save(self):
        atomic(self.path, self.rows)

    def schemas(self):
        definitions = {
            "research_spawn": (
                "Start a researcher using the configured worker model, sharing all science tools and TOTAL resources. Returns immediately. At most two active workers.",
                {"name": {"type": "string"}, "message": {"type": "string"}},
                ["name", "message"],
            ),
            "research_followup": (
                "Continue an existing idle/interrupted researcher using its SAME native thread, evidence and account. Resolve its questions via team_answer first.",
                {"name": {"type": "string"}, "message": {"type": "string"}},
                ["name", "message"],
            ),
            "research_workers": (
                "Read worker statuses, actor IDs and public final replies; no model call or remote polling.",
                {},
                [],
            ),
        }
        return [
            dict(
                type="function",
                name=name,
                description=description,
                inputSchema=dict(
                    type="object",
                    properties=properties,
                    required=required,
                    additionalProperties=False,
                ),
            )
            for name, (description, properties, required) in definitions.items()
        ]

    def handlers(self):
        return {
            "research_spawn": self.spawn,
            "research_followup": self.followup,
            "research_workers": self.status,
        }

    def authorize(self, actor):
        if actor != self.parent.thread_id:
            raise PermissionError("only the principal controls worker lifecycle")

    def spawn(self, actor, args, request_id):
        return self.launch(actor, args, request_id, followup=False)

    def followup(self, actor, args, request_id):
        return self.launch(actor, args, request_id, followup=True)

    def launch(self, actor, args, request_id, *, followup):
        self.authorize(actor)
        name = identifier(args["name"])
        text(args["message"])
        with self.lock:
            if self.closing.is_set():
                raise PermissionError("worker pool is stopping")
            prior = self.rows.get(name)
            if prior and prior.get("request_id") == request_id:
                if prior["message"] != args["message"]:
                    raise ValueError("worker retry arguments changed")
                return dict(prior)
            if followup and (prior is None or not prior.get("actor_id")):
                raise ValueError("followup requires an existing registered worker")
            if not followup and prior is not None:
                raise ValueError("worker name exists; use research_followup")
            if name in self.running:
                raise ValueError("worker is active; read status or answer its durable questions")
            if len(self.running) >= 2:
                raise ValueError("two workers are active; wait or reuse one after completion")
            if self.parent.team_dispatch_guard:
                self.parent.team_dispatch_guard()
            row = {
                **(prior or {}),
                "name": name,
                "message": args["message"],
                "request_id": request_id,
                "status": "STARTING",
                "updated_at": time.time(),
                "reply": None,
            }
            self.rows[name] = row
            thread = threading.Thread(
                target=self._run,
                args=(name, followup),
                name="native-worker-" + name,
                daemon=True,
            )
            self.running[name] = thread
            self.save()
            thread.start()
            return dict(row)

    def status(self, actor, args, request_id):
        # All team members may see shared progress, never another run.
        with self.lock:
            return {
                "workers": [dict(v) for v in self.rows.values()],
                "active": len(self.running),
                "maximum_active_researchers": 2,
                "shared_account": True,
            }

    def _run(self, name, followup):
        client = None
        try:
            with self.lock:
                row = dict(self.rows[name])
            state = self.parent.state / "workers" / name
            factory = getattr(self.parent, "create_researcher", None)
            client = factory(state) if factory else ResearcherClient(self.parent, state)
            with self.lock:
                self.clients[name] = client
            started = client.start_or_resume(row.get("actor_id") if followup else None)
            actor = started["thread_id"]
            with self.lock:
                self.rows[name].update(actor_id=actor, status="RUNNING", updated_at=time.time())
                self.save()
            assignment = (
                self.parent.team.service.delegate(
                    self.parent.thread_id,
                    assignee=actor,
                    goal=row["message"],
                    acceptance="Return an evidence-linked deliverable for principal review; report uncertainty and incomplete work honestly.",
                    references=[],
                    request_id="worker-" + row["request_id"],
                )
                if not followup
                else None
            )
            prompt = (
                RESEARCHER_INSTRUCTIONS
                + f"\nYour actor ID is {actor}; principal is {self.parent.thread_id}. "
                "You have native code/tools and the same shared research APIs. Native nested delegation is disabled. "
                "Use team_ask for task ambiguity; then end this turn and wait for the principal to answer and follow up. "
                f"Write concurrent artifacts under agents/{name}/ inside the current work directory. "
                f"Read the immutable scientific task at {self.parent.task}/TASK.md. "
                "Only the principal can submit the final mechanism.\n"
                + (
                    f"Assigned task ID: {assignment['id']}\n"
                    if assignment
                    else "Resume your current task and read its latest version.\n"
                )
                + "Principal assignment:\n"
                + row["message"]
            )
            client.begin(prompt)
            while not self.closing.is_set():
                if client.poll():
                    break
            if self.closing.is_set():
                raise InterruptedError("parent runtime stopped")
            if (client.completed or {}).get("status") != "completed":
                raise RuntimeError("native worker turn failed")
            messages = [
                i.get("text", "")
                for i in (client.completed or {}).get("items", [])
                if i.get("type") == "agentMessage"
            ]
            with self.lock:
                self.rows[name].update(status="IDLE", reply="\n".join(messages))
        except Exception as exc:
            with self.lock:
                self.rows[name].update(
                    status="INTERRUPTED" if self.closing.is_set() else "FAILED",
                    error_type=type(exc).__name__,
                )
        finally:
            if client:
                try:
                    client.close()
                except Exception as exc:
                    with self.lock:
                        self.rows[name]["close_error"] = type(exc).__name__
            with self.lock:
                actor = self.rows[name].get("actor_id")
                if actor:
                    self.parent.team.service.store.native_lifecycle(actor, closed=True)
                self.rows[name]["updated_at"] = time.time()
                self.running.pop(name, None)
                self.clients.pop(name, None)
                self.save()
                self.parent.transcript.record(
                    "worker_state", {"threadId": actor, **self.rows[name]}
                )

    def close(self):
        self.closing.set()
        with self.lock:
            threads = list(self.running.values())
            for client in self.clients.values():
                if getattr(client, "process", None) is not None and client.process.poll() is None:
                    client.process.terminate()
        for thread in threads:
            thread.join(12)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("owned worker did not settle during shutdown")
