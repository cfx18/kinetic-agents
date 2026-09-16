"""No real model, proxy or compute: reproduce clean-shell launch and fast failure."""

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from kinetic_agents import config, network, runner
from kinetic_agents.native.subscription import clean_env, transport_env

PROJECT = Path(__file__).resolve().parents[1]


def private_config(tmp_path, content="HTTPS_PROXY=http://127.0.0.1:43210\n"):
    path = tmp_path / "network.env"
    path.write_text(content)
    path.chmod(0o600)
    return dict(env_file=str(path), require_proxy=True, timeout_seconds=2)


def test_file_wins_from_clean_or_polluted_shell_and_never_exports_other_keys(tmp_path):
    cfg = private_config(tmp_path)
    clean = network.host_environment(cfg, {})
    dirty = network.host_environment(cfg, {
        "http_proxy": "http://wrong:80", "HTTPS_PROXY": "http://wrong:80",
        "NO_PROXY": "*", "SSL_CERT_FILE": "/bad", "HOST_KEEP": "yes",
    })
    for key in network.HOST_NETWORK_ENV:
        assert clean.get(key) == dirty.get(key)
    assert dirty["HOST_KEEP"] == "yes"
    assert clean["HTTPS_PROXY"] == clean["https_proxy"] == "http://127.0.0.1:43210"
    assert not set(clean_env("/home", "/state")) & network.HOST_NETWORK_ENV
    host = transport_env("/home", "/state", dirty)
    assert "HOST_KEEP" not in host and host["HTTPS_PROXY"] == clean["HTTPS_PROXY"]


@pytest.mark.parametrize("content", [
    "HTTPS_PROXY=\n", "HTTP_PROXY=http://127.0.0.1:80\n",
    "API_KEY=must-not-be-echoed\n", "HTTPS_PROXY=http://secret:bad-port\n",
    "HTTPS_PROXY=http://secret:80/path\n", "HTTPS_PROXY=$(do-not-execute)\n",
    "HTTPS_PROXY=http://x:80\nHTTPS_PROXY=http://y:80\n",
])
def test_invalid_private_configuration_fails_without_probe_or_leak(tmp_path, content):
    cfg = private_config(tmp_path, content)
    with patch.object(network.subprocess, "run") as run:
        with pytest.raises(network.NetworkPreflightError) as caught:
            network.preflight(cfg, source={})
        run.assert_not_called()
    text = str(caught.value) + json.dumps(caught.value.receipt)
    assert "secret" not in text and "must-not-be-echoed" not in text
    assert caught.value.receipt["model_calls"] == 0


def test_insecure_or_missing_file_not_rescued_by_shell_proxy(tmp_path):
    cfg = private_config(tmp_path)
    Path(cfg["env_file"]).chmod(0o644)
    with pytest.raises(network.NetworkPreflightError):
        network.preflight(cfg, source={"HTTPS_PROXY": "http://valid:80"})
    Path(cfg["env_file"]).unlink()
    with pytest.raises(network.NetworkPreflightError):
        network.preflight(cfg, source={"HTTPS_PROXY": "http://valid:80"})


@pytest.mark.parametrize("http,rc,passed", [
    ("401", 0, True), ("200", 0, True), ("405", 0, True),
    ("403", 0, False), ("407", 0, False), ("302", 0, False),
    ("429", 0, False), ("502", 0, False), ("000", 28, False),
    ("401", 35, False), ("bad-output", 0, False),
])
def test_probe_is_bounded_redacted_and_does_not_mistake_http_block_for_pass(tmp_path, http, rc, passed):
    cfg = private_config(tmp_path, "HTTPS_PROXY=http://name:private-password@127.0.0.1:43210\n")
    with patch.object(network.subprocess, "run", return_value=SimpleNamespace(
        returncode=rc, stdout=http, stderr="private-password"
    )) as run:
        if passed:
            _, receipt = network.preflight(cfg, source={"API_KEY": "secret"})
            assert receipt["status"] == "TRANSPORT_REACHABLE_NOT_MODEL_QUALIFIED"
        else:
            with pytest.raises(network.NetworkPreflightError) as caught:
                network.preflight(cfg, source={"API_KEY": "secret"})
            receipt = caught.value.receipt
    args, kwargs = run.call_args
    assert args[0][:3] == ["curl", "--disable", "--silent"]
    assert "--location" not in args[0] and "--insecure" not in args[0]
    assert "API_KEY" not in kwargs["env"] and "HOME" not in kwargs["env"]
    assert kwargs["timeout"] == 4
    assert "private-password" not in json.dumps(receipt)
    assert "private-password" not in str(args[0])
    assert receipt["authenticated"] is False and receipt["model_calls"] == 0


def test_probe_timeout_and_missing_binary_are_safe_failures(tmp_path):
    cfg = private_config(tmp_path)
    for error in (subprocess.TimeoutExpired("secret", 1), FileNotFoundError("secret")):
        with patch.object(network.subprocess, "run", side_effect=error):
            with pytest.raises(network.NetworkPreflightError) as caught:
                network.preflight(cfg, source={})
        assert "secret" not in str(caught.value)


def test_required_proxy_cannot_be_bypassed_by_no_proxy(tmp_path):
    cfg = private_config(tmp_path, "HTTPS_PROXY=http://127.0.0.1:80\nNO_PROXY=*\n")
    with patch.object(network.subprocess, "run") as probe:
        with pytest.raises(network.NetworkPreflightError, match="NO_PROXY"):
            network.preflight(cfg, source={})
        probe.assert_not_called()


def test_api_relay_rejects_unimplemented_socks_before_probe(tmp_path):
    cfg = private_config(tmp_path, "ALL_PROXY=socks5h://127.0.0.1:80\n")
    with patch.object(network.subprocess, "run") as probe:
        with pytest.raises(network.NetworkPreflightError, match="API_relay_requires"):
            network.preflight(cfg, {"auth": "api", "base_url": "https://api.example.invalid"}, source={})
        probe.assert_not_called()


def test_api_gateway_uses_explicit_network_route_not_legacy_direct_default(tmp_path):
    from kinetic_agents.harnesses import gateway

    fake_server = SimpleNamespace(
        serve_forever=lambda: None, shutdown=lambda: None, server_close=lambda: None,
        server_port=43210,
    )
    for env in (None, {"HTTPS_PROXY": "http://127.0.0.1:43211"}):
        relay = gateway.APIGateway(
            {"protocol": "responses"}, ["synthetic"], tmp_path,
            network_environment=env,
        )
        with patch.object(gateway, "load_credentials", return_value=("https://api.example.invalid", "secret")), patch.object(
            gateway, "ThreadingHTTPServer", return_value=fake_server
        ), patch.object(gateway.urllib.request, "build_opener") as opener:
            try:
                relay.start()
                handlers = opener.call_args.args
                proxies = [handler.proxies for handler in handlers if isinstance(handler, gateway.urllib.request.ProxyHandler)]
                assert proxies == ([{}] if env is None else [{"https": env["HTTPS_PROXY"]}])
            finally:
                relay.close()


def test_prepare_freezes_only_network_policy_and_path_not_private_values(tmp_path):
    import hashlib
    import sys
    from test_project_layout import fixture_inputs

    task, kwargs = fixture_inputs(tmp_path)
    cfg = private_config(tmp_path, "HTTPS_PROXY=http://user:private-proxy-canary@127.0.0.1:80\n")
    run = tmp_path / "run"
    with patch.object(runner, "PARENT_SHA", hashlib.sha256((task / "parent.yaml").read_bytes()).hexdigest()), patch.object(
        runner, "cli_version", return_value=runner.CLI
    ), patch.dict(sys.modules, {"cantera": SimpleNamespace(__version__="synthetic")}):
        runner.prepare(run, **kwargs, network=cfg)
        _, contract, _ = runner.load(run)
    assert contract["network"] == cfg
    assert "private-proxy-canary" not in (run / "preflight.json").read_text()
    assert not list(run.rglob("*.env"))
    assert not (run / "solo-max/runtime.sqlite").exists()


def test_common_profiles_pin_same_private_network_without_reading_file():
    with patch.object(network, "dotenv", side_effect=AssertionError("validate must not read secrets")):
        solo = config.load_config(PROJECT / "configs/solo.yaml")[1]
        team = config.load_config(PROJECT / "configs/team.yaml")[1]
    assert solo["network"] == team["network"]
    assert solo["network"]["env_file"] == str(PROJECT / ".env.network")
    # The distributable example does not assume the developer's private proxy.
    # Explicit require_proxy=True behavior is exercised by the network tests.
    assert solo["network"]["require_proxy"] is False


@pytest.mark.parametrize("env_path", ["runs/network.env", "tasks/usc_ii/network.env"])
def test_network_config_cannot_be_exposed_to_scientist(tmp_path, env_path):
    common = yaml.safe_load((PROJECT / "configs/common.yaml").read_text())
    common["network"]["env_file"] = env_path
    common["output"]["root"] = "runs"
    common["inputs"]["task"] = "tasks/usc_ii"
    (tmp_path / "common.yaml").write_text(yaml.safe_dump(common))
    (tmp_path / "solo.yaml").write_text((PROJECT / "configs/solo.yaml").read_text())
    with pytest.raises(PermissionError):
        config.load_config(tmp_path / "solo.yaml")


def launch_fixture(tmp_path):
    root = tmp_path / "solo-max"
    (root / "work").mkdir(parents=True)
    cfg = private_config(tmp_path)
    contract = {**runner.resource_contract(512, 24, 64), "network": cfg,
                "arm": "solo-max", "model": "gpt-6-astra", "effort": "xhigh"}
    return root, contract


def test_failed_preflight_cannot_start_clock_or_supervisor(tmp_path):
    root, contract = launch_fixture(tmp_path)
    with patch.object(runner, "load", return_value=(root, contract, {})), patch.object(
        runner, "task_directory", return_value=Path("/synthetic-public-input")
    ), patch.object(network.subprocess, "run", return_value=SimpleNamespace(
        stdout="000", returncode=7
    )), patch.object(runner.subprocess, "Popen") as spawn:
        with pytest.raises(network.NetworkPreflightError):
            runner.start(tmp_path)
        spawn.assert_not_called()
    assert not (root / "launch_intent.json").exists()
    assert not (root / "runtime.sqlite").exists()
    assert json.loads((root / "network_preflight.json").read_text())["status"] == "FAILED_BEFORE_LAUNCH"


def test_successful_clean_shell_passes_verified_environment_to_detached_supervisor(tmp_path):
    root, contract = launch_fixture(tmp_path)
    with patch.dict(os.environ, {}, clear=True), patch.object(
        runner, "load", return_value=(root, contract, {})
    ), patch.object(runner, "task_directory", return_value=Path("/synthetic-public-input")), patch.object(
        network.subprocess, "run", return_value=SimpleNamespace(stdout="401", returncode=0)
    ), patch.object(runner.subprocess, "Popen", return_value=SimpleNamespace(pid=123)) as spawn, patch.object(
        runner, "checkpoint"
    ):
        result = runner.start(tmp_path)
        assert result["pid"] == 123
        assert spawn.call_args.kwargs["env"]["HTTPS_PROXY"] == "http://127.0.0.1:43210"
        assert spawn.call_args.kwargs["env"]["https_proxy"] == "http://127.0.0.1:43210"
        assert spawn.call_args.kwargs["start_new_session"] is True
        assert (root / "runtime.sqlite").exists()
        with pytest.raises(PermissionError, match="already attempted"):
            runner.start(tmp_path)
        assert spawn.call_count == 1


def test_default_service_tier_remains_explicit():
    # Regression for the user's explicit decision to keep standard service.
    import inspect
    from kinetic_agents.native.subscription import SubscriptionTeamClient

    assert '"service_tier": "default"' in inspect.getsource(SubscriptionTeamClient)
