import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request
import urllib.error

import pytest

from kinetic_agents.config import load_config
from kinetic_agents.connections import dotenv, bind_backend, load_credentials
from kinetic_agents.harnesses.catalog import validate
from kinetic_agents.harnesses.mcp import ToolServer
from kinetic_agents.harnesses.stream import public_event, StreamSession
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore
from kinetic_agents.research.environment import ScientificTeamService

PROJECT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "name",
    [
        "solo",
        "team",
        "codex-api-solo",
        "codex-api-team",
        "claude-solo",
        "claude-team",
        "kimi-solo",
        "kimi-team",
    ],
)
def test_common_inputs_all_profiles_no_inference(name):
    config = load_config(PROJECT / "configs" / (name + ".yaml"))[1]
    assert config["inputs"] == load_config(PROJECT / "configs/solo.yaml")[1]["inputs"]
    assert "api_key" not in config["backend"]


@pytest.mark.parametrize("arm,members", [("solo", 1), ("team", 3)])
def test_staged_kimi_expert_profiles_keep_exact_astra_task(arm, members):
    kimi = load_config(PROJECT / "configs" / f"expert-v2-kimi-{arm}.yaml")[1]
    astra = load_config(PROJECT / "configs" / f"expert-v2-{arm}.yaml")[1]
    assert kimi["inputs"] == astra["inputs"]
    assert kimi["resources"] == astra["resources"]
    assert kimi["model"]["max_agents"] == members
    assert kimi["backend"]["auth"] == "api"
    assert kimi["backend"]["env_file"] == str(PROJECT / ".env")
    assert kimi["backend"]["base_url_env"] == "KIMI_API_BASE_URL"
    assert kimi["backend"]["api_key_env"] == "KIMI_API_KEY"
    assert kimi["output"]["prefix"] != astra["output"]["prefix"]
    assert kimi["harness"]["name"] == "kimi_code_node"
    assert kimi["harness"]["executable"].endswith("/@moonshot-ai/kimi-code/dist/main.mjs")
    assert kimi["model"]["reasoning_effort"] == "max"
    if members > 1:
        assert kimi["model"]["researcher_model"] == kimi["model"]["name"]


@pytest.mark.parametrize("arm", ["solo", "team"])
def test_kimi_original_task_pair_only_changes_task_and_output(arm):
    baseline = load_config(PROJECT / "configs" / f"baseline-v1-kimi-{arm}.yaml")[1]
    expert = load_config(PROJECT / "configs" / f"expert-v2-kimi-{arm}.yaml")[1]
    assert baseline["inputs"]["task"] == str(PROJECT / "tasks/usc_ii")
    assert baseline["output"]["prefix"] != expert["output"]["prefix"]
    for key in ("model", "harness", "backend", "resources", "deployment", "network", "account", "transcript"):
        assert baseline[key] == expert[key]
    for key in ("accepted", "evaluation_base"):
        assert baseline["inputs"][key] == expert["inputs"][key]
    original = (PROJECT / "tasks/usc_ii/TASK.md").read_bytes()
    guided = (PROJECT / "tasks/usc_ii_expert_v2/TASK.md").read_bytes()
    assert hashlib.sha256(original).hexdigest() == "85715d6567a53b9a67773487163013b079d9ccb2e530a258bda9018e861f0836"
    assert b"https://" not in original
    assert guided.split(b"### Domain-expert guidance")[0].strip() == original.strip()


@pytest.mark.skipif(
    os.environ.get("RUN_LOCAL_INPUT_QUALIFICATION") != "1",
    reason="Separately supplied parent mechanisms are not distributed with source",
)
def test_optional_registered_tasks_use_identical_local_parent():
    # Explicit opt-in fails if either file is absent; never synthesize a real input.
    assert (PROJECT / "tasks/usc_ii/parent.yaml").read_bytes() == (PROJECT / "tasks/usc_ii_expert_v2/parent.yaml").read_bytes()


def test_env_never_evaluates_or_leaks(tmp_path):
    path = tmp_path / ".env"
    path.write_text("BASE=https://example.invalid/v1\nKEY=secret-synthetic\n")
    path.chmod(0o600)
    backend = dict(
        auth="api",
        protocol="responses",
        env_file=str(path),
        base_url_env="BASE",
        api_key_env="KEY",
    )
    binding = bind_backend(backend)
    assert "secret-synthetic" not in json.dumps(binding)
    assert load_credentials(binding) == (
        "https://example.invalid/v1",
        "secret-synthetic",
    )
    path.write_text("BASE=https://different.invalid/v1\nKEY=rotated\n")
    with pytest.raises(PermissionError):
        load_credentials(binding)
    path.write_text("KEY=$(touch stolen)\n")
    with pytest.raises(ValueError):
        dotenv(path)
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        dotenv(path)


def test_protocol_mismatch_and_effort_not_silently_mapped():
    from kinetic_agents.connections import validate_backend

    with pytest.raises(ValueError, match="protocol mismatch"):
        validate_backend(
            dict(
                auth="api",
                protocol="chat_completions",
                env_file=".env",
                base_url_env="BASE",
                api_key_env="KEY",
            ),
            "codex",
        )
    with pytest.raises(ValueError, match="reasoning_effort"):
        validate(
            {"name": "kimi_code", "executable": "kimi"},
            dict(name="anything", reasoning_effort="xhigh", max_agents=1),
        )


def test_public_event_excludes_nested_private_thinking():
    event = {
        "type": "stream_event",
        "event": {"delta": {"type": "thinking_delta", "thinking": "private-secret"}},
    }
    assert "private-secret" not in json.dumps(public_event(event))
    event = {
        "role": "assistant",
        "content": [
            {"type": "think", "text": "private-secret"},
            {"type": "text", "text": "public"},
        ],
        "reasoning_content": "private-secret",
    }
    result = json.dumps(public_event(event))
    assert "private-secret" not in result and "public" in result


@pytest.mark.skipif(
    os.environ.get("RUN_NATIVE_TEAM_QUALIFICATION") != "1",
    reason="loopback only; explicit local qualification",
)
def test_mcp_token_scope_and_full_results():
    calls = []
    server = ToolServer(
        [{"name": "echo", "description": "fixture", "inputSchema": {"type": "object"}}],
        lambda n, a, i: calls.append((n, a, i))
        or {"content": [{"type": "text", "text": json.dumps(a)}]},
    )
    url = server.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"raw": 3.25}},
            }
        ).encode()
        with pytest.raises(urllib.error.HTTPError) as denied:
            opener.open(urllib.request.Request(url, data=body))
        assert denied.value.code == 403 and not calls
        request = urllib.request.Request(
            url, data=body, headers={"Authorization": "Bearer " + server.token}
        )
        value = json.load(opener.open(request))
        assert calls == [("echo", {"raw": 3.25}, "9")]
        assert "3.25" in value["result"]["content"][0]["text"]
    finally:
        server.close()


def context(root, kind="kimi_code", members=1):
    for name in ("work", "task"):
        (root / name).mkdir()
    task = "Synthetic native harness test, no chemistry or real account."
    (root / "task/TASK.md").write_text(task)
    effort = "max" if kind == "kimi_code_node" else "thinking"
    identity = TeamIdentity(
        "fixture-native",
        hashlib.sha256(task.encode()).hexdigest(),
        "synthetic-model",
        effort,
        members,
        **(
            {"researcher_model": "synthetic-worker", "researcher_effort": effort}
            if members > 1
            else {}
        )
    )
    service = ScientificTeamService(TeamStore(root / "team", identity))
    contract = dict(
        model="synthetic-model",
        effort=effort,
        max_members=members,
        context_tokens=128000,
        harness={
            "name": kind,
            "executable": "/root/.local/share/uv/tools/kimi-cli/bin/kimi",
        },
        backend={"auth": "api", "protocol": "chat_completions"},
    )
    if members > 1:
        contract.update(researcher_model="synthetic-worker", researcher_effort=effort)
    if kind == "kimi_code_node":
        contract["harness"]["executable"] = str(
            Path("/root/shared-nvme/Caifeixue/AgentCFD/AgentCFD_Terminal_Bench/.harness-runtime/kimi-code-0.28.1/node_modules/@moonshot-ai/kimi-code/dist/main.mjs")
        )
    return dict(
        contract=contract,
        work=root / "work",
        task=root / "task",
        state=root / "native",
        service=service,
        environment=SimpleNamespace(handlers={}),
        read_only=(),
    )


def test_kimi_command_uses_scoped_credentials_and_native_session(tmp_path):
    ctx = context(tmp_path)
    with patch.object(ToolServer, "start", return_value="http://127.0.0.1:99/mcp"), patch.object(
        ToolServer, "close"
    ):
        client = StreamSession(ctx, "http://127.0.0.1:98", "scoped-not-real-secret", [])
        try:
            session = client.start_or_resume()
            argv, env = client.argv_env()
            assert "--print" in argv and "--session" in argv and session["thread_id"] in argv
            assert not any(
                v in env for v in ("OPENAI_API_KEY", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY")
            )
            config = json.loads((client.home / "kimi-config.json").read_text())
            assert config["providers"]["configured"]["api_key"] == "scoped-not-real-secret"
            assert "scoped-not-real-secret" not in (tmp_path / "transcript.jsonl").read_text()
        finally:
            client.close()


def test_claude_command_is_native_and_disables_untracked_forks(tmp_path):
    ctx = context(tmp_path, "claude_code")
    ctx["contract"].update(effort="high")
    with patch.object(ToolServer, "start", return_value="http://127.0.0.1:99/mcp"), patch.object(
        ToolServer, "close"
    ):
        client = StreamSession(ctx, "http://127.0.0.1:98", "scoped-only", [])
        try:
            client.start_or_resume()
            argv, env = client.argv_env()
            assert "--strict-mcp-config" in argv and "Agent,Task" in argv
            assert "--dangerously-skip-permissions" not in argv
            assert env["ANTHROPIC_API_KEY"] == "scoped-only"
            assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "synthetic-model"
        finally:
            client.close()


def test_configurable_prepare_freezes_identity_not_credentials(tmp_path):
    from test_project_layout import fixture_inputs
    from kinetic_agents import runner
    from kinetic_agents.harnesses import catalog
    import sys

    task, kwargs = fixture_inputs(tmp_path)
    env = tmp_path / ".env"
    env.write_text("BASE=https://synthetic.invalid/v1\nKEY=synthetic-not-for-export\n")
    env.chmod(0o600)
    execution = {
        "harness": {"name": "codex", "executable": "codex"},
        "backend": {
            "auth": "api",
            "protocol": "responses",
            "env_file": str(env),
            "base_url_env": "BASE",
            "api_key_env": "KEY",
        },
        "model": {"name": "my-independent-model", "reasoning_effort": "high", "max_agents": 1},
    }
    binding = {
        "name": "codex",
        "executable": "/synthetic/codex",
        "version": "synthetic",
        "sha256": "a" * 64,
    }
    run = tmp_path / "run"
    with patch.object(catalog, "inspect", return_value=binding), patch.object(
        runner, "cli_version", return_value=runner.CLI
    ), patch.object(
        runner, "PARENT_SHA", hashlib.sha256((task / "parent.yaml").read_bytes()).hexdigest()
    ), patch.dict(
        sys.modules, {"cantera": SimpleNamespace(__version__="synthetic")}
    ):
        runner.prepare(run, **kwargs, execution=execution)
        root, contract, _ = runner.load(run)
        assert contract["model"] == "my-independent-model" and contract["harness"] == binding
        assert contract["api_usd"] is None
        assert contract["billing"] == "configured_API_no_dollar_limit"
        assert not (root / "runtime.sqlite").exists()
        assert not (root / "task").exists()
        for name in ("preflight.json", "commitment.json", "overview.json", "RUN_REPORT.md"):
            assert "synthetic-not-for-export" not in (run / name).read_text()
        from kinetic_agents.evaluation.submission import identify_root

        assert identify_root(root) == contract["evaluation_id"]


def test_failed_cli_bootstrap_is_rejected_without_model_or_budget(tmp_path):
    from kinetic_agents.harnesses.sandbox import qualify

    ctx = context(tmp_path, "claude_code")
    with patch(
        "kinetic_agents.harnesses.sandbox.subprocess.run",
        side_effect=[
            SimpleNamespace(
                returncode=0,
                stdout=b'RUNTIME_FEATURES={"self_maps":true,"self_fd":true,"private_tmp_writable":true}\nISOLATION_OK',
                stderr=b"",
            ),
            SimpleNamespace(returncode=134, stdout=b"", stderr=b"synthetic Bun abort"),
        ],
    ):
        with pytest.raises(PermissionError, match="no budget clocks or model requests"):
            qualify(tmp_path, ctx["task"], ctx["contract"]["harness"])
    assert json.loads((tmp_path / "native/bootstrap.json").read_text())["status"] == "FAIL"
    assert not (tmp_path / "runtime.sqlite").exists()


@pytest.mark.parametrize("missing", ["self_maps", "self_fd"])
def test_claude_missing_process_metadata_stops_before_cli(tmp_path, missing):
    from kinetic_agents.harnesses.sandbox import qualify

    ctx = context(tmp_path, "claude_code")
    features = dict(self_maps=True, self_fd=True, private_tmp_writable=True)
    features[missing] = False
    result = SimpleNamespace(
        returncode=0,
        stderr=b"",
        stdout=("RUNTIME_FEATURES=" + json.dumps(features) + "\nISOLATION_OK").encode(),
    )
    with patch("kinetic_agents.harnesses.sandbox.subprocess.run", return_value=result) as run:
        with pytest.raises(PermissionError, match="no budget clocks or model requests"):
            qualify(tmp_path, ctx["task"], ctx["contract"]["harness"])
        assert run.call_count == 1
    receipt = json.loads((tmp_path / "native/bootstrap.json").read_text())
    assert receipt["error_code"] == "CLAUDE_PROCESS_METADATA_UNAVAILABLE"
    assert receipt["model_calls"] == 0 and not (tmp_path / "runtime.sqlite").exists()


def test_private_scratch_not_masked_by_tmpdir_alias(tmp_path):
    import tomllib
    from kinetic_agents.harnesses.sandbox import command, environment

    ctx = context(tmp_path)
    home = tmp_path / "owned-home"
    env = environment(home)
    argv = command(ctx["work"], ctx["task"], home, ctx["contract"]["harness"], ["true"])
    raw = next(
        arg.split("=", 1)[1]
        for arg in argv
        if arg.startswith("permissions.external-native-research.filesystem=")
    )
    filesystem = tomllib.loads("fs=" + raw)["fs"]
    assert filesystem[":slash_tmp"] == "deny" and ":tmpdir" not in filesystem
    assert filesystem[str(home)] == "write" and Path(env["TMPDIR"]).is_relative_to(home)
