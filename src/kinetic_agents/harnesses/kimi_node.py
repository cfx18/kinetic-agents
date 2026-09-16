"""Node Kimi Code native loop, distinct from the legacy Python CLI adapter.

The CLI owns planning, context compaction and native session files. The host
only binds its session ID to a budgeted actor, serves MCP and records public I/O.
"""

import json
import re
import uuid

from kinetic_agents.core.storage import atomic
from kinetic_agents.harnesses import sandbox
from kinetic_agents.harnesses.stream import StreamSession


class KimiNodeSession(StreamSession):
    def create_researcher(self, state):
        return KimiNodeSession(
            {**self.context, "state": state}, self.account, self.gateway_token,
            self.base_tools, parent=self,
        )

    def start_or_resume(self, owned_thread=None):
        receipt = self.state / "kimi-native-session.json"
        self.native_session = None
        if receipt.exists():
            value = json.loads(receipt.read_text())
            if not owned_thread or value["actor"] != owned_thread:
                raise PermissionError("native Kimi session belongs to another actor")
            if (value["model"], value["effort"]) != (self.model, self.effort):
                raise PermissionError("native Kimi model settings changed on resume")
            self.native_session = value["session_id"]
        elif owned_thread:
            # A prior unknown/failed start is not permission to create fresh history.
            raise PermissionError("missing native Kimi session receipt; cannot silently restart")
        return super().start_or_resume(owned_thread)

    def prompt_command(self, prompt):
        self.terminal_message = False
        self.native_error = False
        env = sandbox.environment(self.home)
        env.update(
            KIMI_CODE_HOME=str(self.home),
            KIMI_CODE_NO_AUTO_UPDATE="true",
            KIMI_DISABLE_TELEMETRY="true",
        )
        # Native TOML keys are snake_case. No upstream key or global settings.
        quote = json.dumps
        config = "\n".join([
            'default_provider = "research"',
            'default_model = "research"',
            'default_permission_mode = "auto"',
            'telemetry = false',
            'hooks = []',
            '[providers.research]',
            'type = "openai"',
            'base_url = ' + quote(self.account),
            'api_key = ' + quote(self.gateway_token),
            '[models.research]',
            'provider = "research"',
            'model = ' + quote(self.model),
            'max_context_size = ' + str(self.contract["context_tokens"]),
            'capabilities = ["thinking"]',
            'support_efforts = ["low", "high", "max"]',
            'default_effort = ' + quote(self.effort),
            '[thinking]',
            'enabled = true',
            '[loop_control]',
            'max_retries_per_step = 1',
            '[model_catalog]',
            'refresh_interval_ms = 0',
            'refresh_on_start = false',
            '[background]',
            'keep_alive_on_exit = false',
            '[[permission.rules]]',
            'decision = "deny"',
            'pattern = "Agent"',
            'reason = "Use the host-accounted research_spawn tool for researchers."',
            '[[permission.rules]]',
            'decision = "deny"',
            'pattern = "AgentSwarm"',
            'reason = "Use the host-accounted research_spawn tool for researchers."',
            '',
        ])
        # 0.28.1 migrates thinking.effort=max to high on first launch. Declare
        # the model's native default_effort instead; the outbound request is
        # asserted in real-CLI fake-API tests (including resumed/child sessions).
        temporary = self.home / ("config-" + uuid.uuid4().hex + ".toml")
        temporary.write_text(config)
        temporary.replace(self.home / "config.toml")
        # Empty user skill root prevents importing unrelated host skills. The
        # scientist can still create task-local skills and code in its workdir.
        skills = self.home / "skills"
        skills.mkdir(exist_ok=True)
        argv = [
            self.contract["harness"]["executable"],
            "--model", "research", "--output-format", "stream-json",
            "--skills-dir", str(skills), "--add-dir", str(self.task),
            "--prompt", prompt,
        ]
        if self.native_session:
            argv += ["--session", self.native_session]
        return (
            sandbox.command(self.work, self.task, self.home, self.contract["harness"], argv),
            env, b"",
        )

    def observe(self, value):
        session = value.get("session_id")
        if session:
            if not isinstance(session, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", session):
                self.reader_error = "invalid_native_session"
            elif self.native_session and self.native_session != session:
                self.reader_error = "native_session_changed"
            elif not self.native_session:
                self.native_session = session
                atomic(self.state / "kimi-native-session.json", {
                    "actor": self.thread_id, "session_id": session,
                    "model": self.model, "effort": self.effort,
                })
        if value.get("type") == "error" or value.get("is_error") is True:
            self.native_error = True
            self.reader_error = "native_result_error"
        message = value.get("message", value)
        if message.get("role") == "assistant" or value.get("type") == "assistant":
            self.terminal_message = bool(message.get("content")) and not message.get("tool_calls")
        super().observe(value)

    def poll(self):
        result = super().poll()
        if result and (not self.native_session or not self.terminal_message or self.native_error):
            result["status"] = "failed"
        return result
