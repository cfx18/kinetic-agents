"""Selectable native adapters; validation never performs inference."""

from pathlib import Path
import hashlib
import shutil
import subprocess
from typing import Protocol


class HarnessSession(Protocol):
    """Host lifecycle interface, not a replacement for a native agent loop.

    Drivers must bind scientific calls to the actual actor; retain their own
    context/session; append public I/O live; never get the upstream credential.
    poll is bounded and must not synchronously wait for a whole model turn.
    """

    thread_id: str | None
    turn_id: str | None
    completed: dict | None

    def start_or_resume(self, owned_thread: str | None = None) -> dict: ...
    def begin(self, prompt: str) -> None: ...
    def poll(self) -> dict | None: ...
    def close(self) -> None: ...


COMMANDS = {"codex": "codex", "claude_code": "claude", "kimi_code": "kimi", "kimi_code_node": "kimi"}
EFFORTS = {
    "codex": {"minimal", "low", "medium", "high", "xhigh"},
    "claude_code": {"low", "medium", "high", "xhigh", "max"},
    "kimi_code": {"thinking", "off"},
    "kimi_code_node": {"low", "high", "max"},
}


def validate(harness, model):
    if (
        not isinstance(harness, dict)
        or set(harness) != {"name", "executable"}
        or harness["name"] not in COMMANDS
    ):
        raise ValueError("harness requires a registered name and executable")
    executable = harness["executable"]
    if (
        not isinstance(executable, str)
        or not executable
        or any(c in executable for c in "\n\r\x00$")
    ):
        raise ValueError("harness executable is a path or command name, not a shell command")
    for name, effort in [(model["name"], model["reasoning_effort"])] + (
        [(model.get("researcher_model"), model.get("researcher_effort"))]
        if model["max_agents"] > 1
        else []
    ):
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 160
            or any(c.isspace() for c in name)
        ):
            raise ValueError("explicit model ID required")
        provider_max = (harness["name"] == "codex" and name in
                        {"deepseek-flash", "deepseek-v4-pro"} and effort == "max")
        if effort not in EFFORTS[harness["name"]] and not provider_max:
            raise ValueError("reasoning_effort is not supported by this harness; no silent mapping")
    if type(model["max_agents"]) is not int or model["max_agents"] not in (1, 3):
        raise ValueError("supported team sizes are 1 or 3, including principal")
    return harness


def inspect(harness):
    binary = shutil.which(harness["executable"])
    if binary is None:
        raise FileNotFoundError("configured harness executable not found")
    binary = str(Path(binary).resolve())
    if harness["name"] == "kimi_code_node":
        import json
        package = Path(binary).parent.parent / "package.json"
        if not package.is_file() or json.loads(package.read_text()).get("name") != "@moonshot-ai/kimi-code":
            raise ValueError("kimi_code_node requires the actual @moonshot-ai/kimi-code entry point; no Python fallback")
    result = subprocess.run([binary, "--version"], capture_output=True, timeout=20, text=True)
    if result.returncode or not result.stdout.strip():
        raise ValueError("harness --version failed (no inference attempted)")
    with Path(binary).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "name": harness["name"],
        "executable": binary,
        "version": result.stdout.strip()[:160],
        "sha256": digest,
    }


def verify(binding):
    if inspect(binding) != binding:
        raise PermissionError("harness executable/version changed since prepare")


def client(context, gateway, token, tools):
    """Registry boundary. Add a driver here without changing science/runtime."""
    contract = context["contract"]
    if contract["harness"]["name"] == "codex":
        from kinetic_agents.native.subscription import SubscriptionTeamClient
        from kinetic_agents.native.api import APITeamClient

        cls = (
            SubscriptionTeamClient
            if contract["backend"]["auth"] == "subscription"
            else APITeamClient
        )
        value = cls(
            context["work"],
            context["task"],
            context["state"],
            contract["model"],
            contract["effort"],
            gateway,
            token,
            context["service"],
            extra_tools=tools,
            actor_tools=context["environment"].handlers,
            actor_read_only=context["read_only"],
            executable=contract["harness"]["executable"],
        )
        return value
    if contract["harness"]["name"] == "kimi_code_node":
        from kinetic_agents.harnesses.kimi_node import KimiNodeSession
        return KimiNodeSession(context, gateway, token, tools)
    from kinetic_agents.harnesses.stream import StreamSession

    return StreamSession(context, gateway, token, tools)
