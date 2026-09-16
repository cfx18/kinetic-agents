"""Claude Code / Kimi Code headless native loops, with live I/O and scoped MCP.

The host starts/resumes a turn; it does NOT plan model steps, select tools or
compress context. Each CLI does that itself. Worker sessions use the same driver.
"""

import json
from pathlib import Path
import subprocess
import threading
import time
import uuid

from kinetic_agents.core.storage import atomic
from kinetic_agents.team.router import TeamRouter
from kinetic_agents.team.tools import schemas
from kinetic_agents.native.scoped_tools import ScopedScienceTools
from kinetic_agents.native.workers import WorkerPool
from kinetic_agents.native.usage import SubscriptionTeamState
from kinetic_agents.observability.transcript import LiveTranscript, clean
from kinetic_agents.harnesses.mcp import ToolServer
from kinetic_agents.harnesses import sandbox


def public_event(value):
    """Preserve emitted public payloads, discard private thinking before capture."""
    if isinstance(value, list):
        return [
            public_event(v)
            for v in value
            if not isinstance(v, dict)
            or v.get("type")
            not in {
                "thinking",
                "redacted_thinking",
                "thinking_delta",
                "reasoning",
                "think",
            }
        ]
    if isinstance(value, dict):
        if value.get("type") in {
            "thinking",
            "redacted_thinking",
            "thinking_delta",
            "reasoning",
            "think",
        }:
            return {"type": value["type"], "omitted": True}
        return {
            k: public_event(v)
            for k, v in clean(value).items()
            if k
            not in {
                "thinking",
                "signature",
                "reasoning_content",
                "reasoningContent",
                "reasoning_details",
            }
        }
    return clean(value)


class StreamSession:
    def __init__(self, context, gateway, token, tools, *, parent=None):
        self.context, self.contract = context, context["contract"]
        self.work, self.task, self.state = (Path(context[n]) for n in ("work", "task", "state"))
        self.state.mkdir(parents=True, exist_ok=True)
        self.home = self.state / "cli-home"
        self.home.mkdir(mode=0o700, exist_ok=True)
        self.parent, self.account, self.gateway_token = parent, gateway, token
        self.team = parent.team if parent else TeamRouter(context["service"])
        self.model = self.contract["researcher_model"] if parent else self.contract["model"]
        self.effort = self.contract["researcher_effort"] if parent else self.contract["effort"]
        self.provider = self.contract["backend"]["protocol"]
        self.kind = self.contract["harness"]["name"]
        self.transcript = LiveTranscript(parent.transcript.root if parent else self.state.parent)
        self.team_observations = (
            parent.team_observations
            if parent
            else SubscriptionTeamState(
                self.state, self.team.service.store.identity, billing="configured_API"
            )
        )
        self.worker_pool = (
            WorkerPool(self) if not parent and self.contract["max_members"] > 1 else None
        )
        handlers = dict(context["environment"].handlers)
        self.specs = schemas() + list(tools)
        self.base_tools = list(tools)
        read_only = set(context["read_only"])
        if self.worker_pool:
            self.specs += self.worker_pool.schemas()
            handlers.update(self.worker_pool.handlers())
            read_only.add("research_workers")
            self.worker_pool.load()
        self.science_tools = ScopedScienceTools(self.team, handlers, read_only=read_only)
        self.team_dispatch_guard = parent.team_dispatch_guard if parent else None
        self.thread_id = self.turn_id = self.completed = None
        self.process = None
        self.messages, self.calls, self.tools = [], [], {}
        self.dispatch_lock = threading.RLock()
        self.reader_error = None
        self.server = ToolServer(self.specs, self.call)
        url = self.server.start()
        atomic(
            self.home / "mcp.json",
            {
                "mcpServers": {
                    "research": {
                        "type": "http",
                        "url": url,
                        "headers": {"Authorization": "Bearer " + self.server.token},
                    }
                }
            },
        )

    def create_researcher(self, state):
        return StreamSession(
            {**self.context, "state": state},
            self.account,
            self.gateway_token,
            self.base_tools,
            parent=self,
        )

    def start_or_resume(self, owned_thread=None):
        path = self.state / "session.json"
        previous = json.loads(path.read_text()) if path.exists() else None
        if (
            bool(owned_thread) != bool(previous)
            or previous
            and previous["thread_id"] != owned_thread
        ):
            raise PermissionError("resume must use this adapter's original owned session")
        self.thread_id = owned_thread or str(uuid.uuid4())
        self.resumed = bool(owned_thread)
        if self.parent:
            actor = self.thread_id
            self.team.service.store.register(actor, self.parent.thread_id)
            if owned_thread:
                self.team.service.store.native_lifecycle(actor, closed=False)
            self.team.members[actor] = dict(
                id=actor, parent=self.parent.thread_id, role="researcher"
            )
        else:
            self.team.bind_principal(self.thread_id)
        self.team_observations.bind(
            self.thread_id,
            self.parent.thread_id if self.parent else None,
            self.model,
            self.effort,
            observed=False,
        )
        atomic(
            path,
            {
                "thread_id": self.thread_id,
                "model": self.model,
                "effort": self.effort,
                "harness": self.kind,
            },
        )
        self.transcript.record(
            "actor_registered",
            dict(
                threadId=self.thread_id,
                parent=self.parent.thread_id if self.parent else None,
                model=self.model,
                effort=self.effort,
                role="researcher" if self.parent else "principal",
                native_metadata_verified=False,
            ),
        )
        return {
            "thread_id": self.thread_id,
            "model": self.model,
            "provider": self.provider,
            "resumed": self.resumed,
        }

    def argv_env(self):
        binary = self.contract["harness"]["executable"]
        env = sandbox.environment(self.home)
        mcp = str(self.home / "mcp.json")
        if self.kind == "claude_code":
            # No inherited settings, hooks, MCP servers, OAuth or native child model defaults.
            env.update(
                ANTHROPIC_BASE_URL=self.account,
                ANTHROPIC_API_KEY=self.gateway_token,
                CLAUDE_CONFIG_DIR=str(self.home / "claude"),
                CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                ANTHROPIC_DEFAULT_HAIKU_MODEL=self.model,
                ANTHROPIC_DEFAULT_SONNET_MODEL=self.model,
                ANTHROPIC_DEFAULT_OPUS_MODEL=self.model,
                ENABLE_TOOL_SEARCH="false",
            )
            argv = [
                binary,
                "--print",
                "--verbose",
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                "--model",
                self.model,
                "--effort",
                self.effort,
                "--setting-sources",
                "",
                "--strict-mcp-config",
                "--mcp-config",
                mcp,
                "--permission-mode",
                "dontAsk",
                "--allowedTools",
                "Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,mcp__research__*",
                "--disallowedTools",
                "Agent,Task",
                "--session-id" if not self.resumed else "--resume",
                self.thread_id,
            ]
        else:
            provider_type = {
                "responses": "openai_responses",
                "chat_completions": "openai_legacy",
                "anthropic": "anthropic",
            }[self.provider]
            config = {
                "default_model": "research",
                "default_thinking": self.effort == "thinking",
                "providers": {
                    "configured": {
                        "type": provider_type,
                        "base_url": self.account,
                        "api_key": self.gateway_token,
                    }
                },
                "models": {
                    "research": {
                        "provider": "configured",
                        "model": self.model,
                        "max_context_size": self.contract["context_tokens"],
                        "capabilities": (["thinking"] if self.effort == "thinking" else []),
                    }
                },
                "loop_control": {"max_retries_per_step": 1},
            }
            atomic(self.home / "kimi-config.json", config)
            # Extend native defaults, retaining coding/web/compaction, disabling only untracked forks.
            atomic(
                self.home / "agent.json",
                {
                    "version": 1,
                    "agent": {
                        "extend": "default",
                        "exclude_tools": ["kimi_cli.tools.agent:Agent"],
                        "subagents": {},
                    },
                },
            )
            env.update(
                KIMI_SHARE_DIR=str(self.home / "kimi"),
                KIMI_CODE_HOME=str(self.home / "kimi"),
            )
            argv = [
                binary,
                "--print",
                "--output-format",
                "stream-json",
                "--input-format",
                "text",
                "--afk",
                "--config-file",
                str(self.home / "kimi-config.json"),
                "--agent-file",
                str(self.home / "agent.json"),
                "--mcp-config-file",
                mcp,
                "--session",
                self.thread_id,
                "--thinking" if self.effort == "thinking" else "--no-thinking",
            ]
        return (
            sandbox.command(self.work, self.task, self.home, self.contract["harness"], argv),
            env,
        )

    def prompt_command(self, prompt):
        argv, env = self.argv_env()
        return argv, env, prompt.encode()

    def begin(self, prompt):
        if self.process and self.process.poll() is None:
            raise PermissionError("previous native turn is still running")
        self.turn_id, self.completed = str(uuid.uuid4()), None
        self.messages, self.reader_error = [], None
        argv, env, stdin = self.prompt_command(prompt)
        self.transcript.record(
            "model_input",
            {
                "threadId": self.thread_id,
                "turnId": self.turn_id,
                "text": prompt,
                "harness": self.kind,
                "model": self.model,
                "effort": self.effort,
            },
        )
        self.process = subprocess.Popen(
            argv,
            cwd=self.work,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.readers = [
            threading.Thread(target=self._read, args=(self.process.stdout, False), daemon=True),
            threading.Thread(target=self._read, args=(self.process.stderr, True), daemon=True),
        ]
        for thread in self.readers:
            thread.start()
        if stdin:
            self.process.stdin.write(stdin)
        self.process.stdin.close()
        self.resumed = True

    def _read(self, stream, stderr):
        try:
            for line in iter(stream.readline, b""):
                if stderr:
                    # Arbitrary errors can echo credentials/config; keep sanitized text only.
                    payload = {"stderr": line.decode(errors="replace")}
                else:
                    try:
                        payload = json.loads(line)
                    except ValueError:
                        payload = {"unparsed_stdout": line.decode(errors="replace")}
                self.observe(payload)
        except Exception as exc:
            self.reader_error = type(exc).__name__

    def observe(self, value):
        event = public_event(value)
        # Redact even unknown error echoes of the per-run proxy tokens.
        serialized = (
            json.dumps(event, ensure_ascii=False)
            .replace(self.gateway_token, "[SCOPED_TOKEN]")
            .replace(self.server.token, "[SCOPED_TOKEN]")
        )
        event = json.loads(serialized)
        self.transcript.record(
            "native_event",
            {
                "threadId": self.thread_id,
                "turnId": self.turn_id,
                "harness": self.kind,
                "event": event,
            },
        )
        if (
            self.kind == "claude_code"
            and value.get("type") == "system"
            and value.get("subtype") == "init"
        ):
            if value.get("session_id") != self.thread_id or value.get("model") != self.model:
                self.reader_error = "native_session_or_model_mismatch"
                if self.process:
                    self.process.terminate()
        if self.kind == "claude_code" and value.get("type") == "result" and value.get("is_error"):
            self.reader_error = "native_result_error"
        message = event.get("message", event)
        if message.get("role") == "assistant" or event.get("type") == "assistant":
            if isinstance(message.get("content"), str):
                self.messages.append({"type": "agentMessage", "text": message["content"]})
            for part in message.get("content", []):
                if isinstance(part, dict) and part.get("type") == "text":
                    self.messages.append({"type": "agentMessage", "text": part.get("text", "")})

    def call(self, name, arguments, call_id):
        # A CLI can restart and reuse MCP integer IDs; namespace by turn.
        with self.dispatch_lock:
            params = dict(
                threadId=self.thread_id,
                turnId=self.turn_id,
                callId="mcp-" + call_id,
                tool=name,
                arguments=arguments,
            )
            self.transcript.record("tool_request", params)
            try:
                if self.team_dispatch_guard:
                    self.team_dispatch_guard()
                if (
                    name == "finish_research"
                    and self.worker_pool
                    and self.worker_pool.status(None, {}, None)["active"]
                ):
                    raise ValueError("collect active researchers before final submission")
                result = (
                    self.team.dispatch(params)
                    if name.startswith("team_")
                    else self.science_tools.dispatch(params, guard=self.team_dispatch_guard)
                )
            except Exception as exc:
                result = self.team.error(exc)
            self.transcript.record("tool_result", {**params, "result": result})
            return {
                "isError": not result.get("success", False),
                "content": [
                    {"type": "text", "text": item["text"]}
                    for item in result.get("contentItems", [])
                    if "text" in item
                ],
            }

    def poll(self):
        if self.process is None:
            return None
        code = self.process.poll()
        if code is None or any(t.is_alive() for t in self.readers):
            time.sleep(0.01)
            return None
        self.completed = {
            "status": "completed" if code == 0 and not self.reader_error else "failed",
            "items": list(self.messages),
        }
        return self.completed

    def close(self):
        if self.worker_pool:
            self.worker_pool.close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for thread in getattr(self, "readers", []):
            thread.join(2)
        self.server.close()
        self.transcript.close()
