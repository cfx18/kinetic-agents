"""Node-specific identity/config/error tests; no model or solver calls."""
import json
import tomllib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_harness_adapters import context
from kinetic_agents.harnesses.kimi_node import KimiNodeSession
from kinetic_agents.harnesses.mcp import ToolServer


@pytest.fixture
def client(tmp_path):
    ctx = context(tmp_path, kind="kimi_code_node")
    with patch.object(ToolServer, "start", return_value="http://127.0.0.1:99/mcp"), patch.object(ToolServer, "close"):
        value = KimiNodeSession(ctx, "http://127.0.0.1:98", "scoped-test-only", [])
        value.start_or_resume()
        yield value
        value.close()


def test_native_settings_and_no_legacy_flags(client):
    argv, env, stdin = client.prompt_command("synthetic task")
    assert stdin == b"" and "--prompt" in argv
    assert not set(argv) & {"--print", "--afk", "--agent-file", "--session"}
    assert env["KIMI_CODE_HOME"] == str(client.home)
    cfg = tomllib.loads((client.home / "config.toml").read_text())
    assert cfg["models"]["research"]["default_effort"] == "max"
    assert "effort" not in cfg["thinking"]
    assert "max_output_size" not in cfg["models"]["research"]
    assert cfg["models"]["research"]["model"] == "synthetic-model"
    assert {v["pattern"] for v in cfg["permission"]["rules"]} == {"Agent", "AgentSwarm"}


def test_native_identity_is_distinct_and_locked(client):
    client.prompt_command("test")
    client.observe({"session_id": "native-123", "role": "assistant", "content": "Done."})
    saved = json.loads((client.state / "kimi-native-session.json").read_text())
    assert saved["session_id"] == "native-123" and saved["actor"] == client.thread_id
    argv, _, _ = client.prompt_command("continue")
    assert argv[-2:] == ["--session", "native-123"]
    client.observe({"session_id": "other-native-456"})
    assert client.reader_error == "native_session_changed"


def test_private_reasoning_excluded_and_native_error_detected(client):
    client.prompt_command("test")
    client.observe({"session_id": "n1", "role": "assistant", "content": "Public.", "reasoning_content": "PRIVATE-TEST"})
    client.observe({"type": "error", "error": "synthetic failure"})
    assert client.native_error and client.reader_error == "native_result_error"
    assert "PRIVATE-TEST" not in (client.state.parent / "transcript.jsonl").read_text()


def test_missing_native_receipt_not_silently_restarted(client):
    with pytest.raises(PermissionError, match="missing native"):
        client.start_or_resume(client.thread_id)


def test_invalid_native_session_rejected(client):
    client.prompt_command("test")
    client.observe({"session_id": "../../other"})
    assert client.reader_error == "invalid_native_session"


@pytest.mark.parametrize("code,session,terminal", [(1, "n1", True), (0, None, True), (0, "n1", False)])
def test_exit_is_not_success_without_native_receipt_and_final(client, code, session, terminal):
    client.prompt_command("test")
    client.native_session = session
    client.terminal_message = terminal
    client.process = SimpleNamespace(poll=lambda: code)
    client.readers = []
    assert client.poll()["status"] == "failed"


def test_expected_effort_is_checked_before_dispatch(tmp_path):
    # This is a static binding test; native tests exercise the actual requests.
    from kinetic_agents.harnesses.gateway import APIGateway
    gateway = APIGateway({"protocol": "chat_completions"}, ["kimi-k3"], tmp_path,
                         expected_efforts={"kimi-k3": "max"})
    assert gateway.expected_efforts == {"kimi-k3": "max"}
