"""Dedicated API-native Codex client, no managed ChatGPT account path."""

import json
import os
from pathlib import Path
import shutil
import time

from kinetic_agents.native.rpc import MetadataClient
from kinetic_agents.native.dynamic import DynamicClient
from kinetic_agents.native.permissions import flags
from kinetic_agents.native.dispatch import DispatchReceiver
from kinetic_agents.native.baseline import resolve_baseline


class NativeClient(DynamicClient):
    provider = "deepseek"
    METHODS = {
        "initialize",
        "thread/start",
        "thread/resume",
        "thread/read",
        "turn/start",
        "turn/interrupt",
        "thread/compact/start",
    }

    def __init__(
        self, work, task, state, model, effort, gateway_url, token, tools, *, baseline_config=None
    ):
        # Fail before creating state or starting a process if a caller attempts
        # an unqualified capability expansion. Legacy eight-argument calls keep
        # exactly the previous controlled native settings.
        self.baseline = resolve_baseline(baseline_config)
        self.work, self.task, self.state = (Path(p).resolve() for p in (work, task, state))
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "codex").mkdir(mode=0o700, exist_ok=True)
        self.model, self.effort, self.specs = model, effort, tools
        settings = {
            **self.baseline.permission_settings(self.work, self.task),
            "model": model,
            "model_provider": self.provider,
            "model_providers."
            + self.provider: {
                "name": "Configured API",
                "base_url": gateway_url,
                "wire_api": "responses",
                "env_key": "PAIR_GATEWAY_TOKEN",
                "requires_openai_auth": False,
                "request_max_retries": 0,
                "stream_max_retries": 0,
            },
            "model_reasoning_effort": effort,
            **self.baseline.codex_overrides(),
            "sqlite_home": str(self.state / "sqlite"),
            "log_dir": str(self.state / "logs"),
        }
        # A fresh home prevents inherited auth, hooks, MCP servers or project history.
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": str(self.state),
            "CODEX_HOME": str(self.state / "codex"),
            "PAIR_GATEWAY_TOKEN": token,
        }
        if getattr(self, "host_scoped_workers", False):
            settings.update(
                {
                    "agents.enabled": False,
                    "features.multi_agent_v2": False,
                    "features.multi_agent": False,
                }
            )
        binary = getattr(self, "executable", None) or shutil.which("codex")
        if binary is None:
            raise RuntimeError("Codex executable unavailable")
        MetadataClient.__init__(
            self,
            [binary, *flags(settings), "app-server", "--stdio"],
            str(self.work),
            120,
            env=env,
        )
        self.receiver = DispatchReceiver(
            self.process.stdout,
            self.process.stderr,
            **({"observer": self.transcript.incoming} if hasattr(self, "transcript") else {})
        )
        self.dispatch_observer = None
        self.thread_id = self.turn_id = self.completed = self.usage = None
        self.tools, self.calls, self.unexpected_items, self.rejected_requests = (
            {},
            [],
            [],
            [],
        )

    def baseline_snapshot(self):
        return self.baseline.snapshot()

    def baseline_identity(self):
        return self.baseline.identity()

    def request(self, method, params):
        if method in ("thread/start", "thread/resume", "turn/start"):
            if (
                params.get("model") != self.model
                or params.get("permissions") != self.baseline.permission_profile
            ):
                raise PermissionError("fixed model and permissions required")
        if method in (
            "turn/start",
            "turn/interrupt",
            "thread/read",
            "thread/compact/start",
        ):
            if not self.thread_id or params.get("threadId") != self.thread_id:
                raise PermissionError("foreign thread")
        return MetadataClient.request(self, method, params)

    def start_or_resume(self, owned_thread=None):
        self.request(
            "initialize",
            {
                "clientInfo": {"name": "deepseek_durable_pair", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.send({"method": "initialized", "params": {}})
        self.prepare_account()
        params = {
            "model": self.model,
            "modelProvider": self.provider,
            "cwd": str(self.work),
            "permissions": self.baseline.permission_profile,
            "approvalPolicy": self.baseline.approval_policy,
            "config": {"model_reasoning_effort": self.effort},
            "allowProviderModelFallback": self.baseline.provider_fallback,
        }
        if owned_thread:
            params["threadId"] = owned_thread
            result = self.request("thread/resume", params)
        else:
            params.update(ephemeral=False, dynamicTools=self.specs)
            result = self.request("thread/start", params)
        thread = result["thread"]
        if (owned_thread and thread["id"] != owned_thread) or result.get("model") != self.model:
            raise PermissionError("model or persistent identity mismatch")
        if result.get("modelProvider") != self.provider:
            raise PermissionError("unexpected provider")
        self.thread_id = thread["id"]
        return {
            "thread_id": self.thread_id,
            "model": result["model"],
            "provider": result["modelProvider"],
            "resumed": bool(owned_thread),
        }

    def prepare_account(self):
        """API-native clients need no managed account RPC. Subscription overrides."""

    def begin(self, text):
        self.completed = None
        response = self.request(
            "turn/start",
            {
                "threadId": self.thread_id,
                "model": self.model,
                "effort": self.effort,
                "permissions": self.baseline.permission_profile,
                "input": [{"type": "text", "text": text}],
            },
        )
        self.turn_id = response["turn"]["id"]

    def receive(self, deadline):
        return self.receiver.receive(deadline)

    def poll(self):
        # Drain a bounded FIFO batch, without paying a lifecycle transaction per notification.
        end = time.monotonic() + 0.05
        for index in range(256):
            try:
                message = self.receive(time.monotonic() + 0.1 if index == 0 else end)
            except TimeoutError:
                break
            except EOFError as exc:
                if not self.completed:
                    raise ConnectionError("native server transport closed") from exc
                break
            self.handle_message(message)
            if self.completed or time.monotonic() >= end:
                break
        # Drain buffered control events even if the process has already exited.
        # The reader reports EOF only AFTER its retained FIFO is exhausted.
        return self.completed

    def handle_message(self, message):
        params = message.get("params") or {}
        if (
            getattr(self, "dispatch_observer", None) is not None
            and "id" in message
            and message.get("method") == "item/tool/call"
            and params.get("threadId") == self.thread_id
            and params.get("tool") in self.tools
        ):
            receipt = self.receiver.last_receipt
            if receipt:
                self.dispatch_observer(params["tool"], params.get("callId"), dict(receipt))
        return super().handle_message(message)

    def close(self):
        self.receiver.close()
        MetadataClient.close(self)
