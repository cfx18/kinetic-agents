"""Host-only network configuration and bounded, unauthenticated launch checks.

Routing values never enter a run snapshot or an Agent shell. An explicit file
replaces (rather than merges with) the caller's routing environment, so launching
from a clean terminal behaves like launching from an already configured one.
"""

import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import proxy_bypass_environment

from kinetic_agents.connections import dotenv
from kinetic_agents.native.subscription import HOST_NETWORK_ENV

FILE_KEYS = frozenset(key for key in HOST_NETWORK_ENV if key.isupper())
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def validate_network(value):
    if not isinstance(value, dict) or set(value) != {
        "env_file", "require_proxy", "timeout_seconds"
    }:
        raise ValueError("network requires env_file, require_proxy, timeout_seconds")
    path = value["env_file"]
    if (not isinstance(path, str) or not path or path.startswith("~")
            or any(char in path for char in ("$", "\x00", "\n", "\r"))):
        raise ValueError("network.env_file must be a literal host path")
    if type(value["require_proxy"]) is not bool:
        raise ValueError("network.require_proxy must be a boolean")
    if type(value["timeout_seconds"]) is not int or not 1 <= value["timeout_seconds"] <= 30:
        raise ValueError("network.timeout_seconds must be an integer in 1..30")
    return dict(value)


def host_environment(config, source=None):
    """Read the private file once; return the exact environment later launched."""
    validate_network(config)
    try:
        values = dotenv(config["env_file"])
    except (OSError, ValueError):
        # Neither parser input nor OS messages are safe to forward to transcripts.
        raise ValueError(
            "cannot load network.env_file; use a plain chmod-600 dotenv file"
        ) from None
    if set(values) - FILE_KEYS:
        raise ValueError("network.env_file contains unsupported keys; only proxy/CA names allowed")
    env = dict(os.environ if source is None else source)
    for key in HOST_NETWORK_ENV:
        env.pop(key, None)
    for key, value in values.items():
        if not value:
            continue
        if key in PROXY_KEYS:
            try:
                parts = urlsplit(value)
                valid = (parts.scheme in ("http", "https", "socks5", "socks5h")
                         and parts.hostname and parts.port
                         and parts.path in ("", "/") and not parts.query and not parts.fragment
                         and not any(char.isspace() for char in value))
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("invalid network proxy URL (value withheld)")
        elif key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "CODEX_CA_CERTIFICATE"):
            if not Path(value).is_absolute() or not Path(value).exists():
                raise ValueError("network CA path must exist and be absolute (value withheld)")
        env[key] = value
        if key in (*PROXY_KEYS, "NO_PROXY"):
            env[key.lower()] = value
    if config["require_proxy"] and not any(env.get(key) for key in ("HTTPS_PROXY", "ALL_PROXY")):
        raise ValueError("network requires an HTTPS_PROXY or ALL_PROXY; no model or clock started")
    return env


class NetworkPreflightError(ConnectionError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(
            f"network preflight failed: {receipt['reason']}; no model or clock started. "
            "Check network.env_file with run.py doctor --config <profile.yaml>"
        )


def preflight(config, backend=None, *, source=None):
    """No tokens, prompts, inference, redirects or automatic retries.

    A 401 from the model service establishes HTTPS reachability, NOT successful
    authentication or model access. Inference may still fail after this check.
    """
    started = time.monotonic()
    receipt = dict(
        schema="host-network-preflight.v1", at=time.time(),
        status="FAILED_BEFORE_LAUNCH", model_calls=0, authenticated=False,
        scope="HTTPS_transport_only_not_model_or_scientific_acceptance",
        host_network_env_names=[],
    )
    try:
        env = host_environment(config, source)
    except ValueError:
        receipt["reason"] = "invalid_or_missing_private_network_configuration"
        raise NetworkPreflightError(receipt) from None
    receipt["host_network_env_names"] = sorted(key for key in HOST_NETWORK_ENV if env.get(key))
    backend = backend or {"auth": "subscription"}
    endpoint = (
        "https://chatgpt.com/backend-api/codex/models"
        if backend["auth"] == "subscription" else backend["base_url"]
    )
    if config["require_proxy"] and proxy_bypass_environment(
        urlsplit(endpoint).hostname, {"no": env.get("NO_PROXY", "")}
    ):
        receipt["reason"] = "NO_PROXY_bypasses_required_endpoint_proxy"
        raise NetworkPreflightError(receipt)
    if backend["auth"] == "api" and any(
        env.get(key) and urlsplit(env[key]).scheme not in ("http", "https")
        for key in PROXY_KEYS
    ):
        receipt["reason"] = "API_relay_requires_HTTP_or_HTTPS_proxy"
        raise NetworkPreflightError(receipt)
    timeout = config["timeout_seconds"]
    # Do not inherit curlrc, API keys, netrc or arbitrary authentication settings.
    probe_env = {key: env[key] for key in HOST_NETWORK_ENV if env.get(key)}
    probe_env.update(PATH="/usr/bin:/bin", LANG="C.UTF-8")
    command = [
        "curl", "--disable", "--silent", "--output", "/dev/null",
        "--write-out", "%{http_code}", "--connect-timeout", str(timeout),
        "--max-time", str(timeout),
    ]
    ca = env.get("CODEX_CA_CERTIFICATE") or env.get("SSL_CERT_FILE")
    if ca:
        command += ["--cacert", ca]
    if env.get("SSL_CERT_DIR"):
        command += ["--capath", env["SSL_CERT_DIR"]]
    try:
        result = subprocess.run(
            [*command, endpoint], env=probe_env, capture_output=True,
            text=True, timeout=timeout + 2, check=False,
        )
        raw = result.stdout.strip()
        code = int(raw) if len(raw) == 3 and raw.isdigit() else 0
        receipt.update(http_status=code, curl_exit_code=result.returncode)
        # Fail closed on proxy blocks, redirects, rate limits and server errors.
        # Accepted auth/path errors only prove that an HTTP service was reached.
        good = not result.returncode and code in (200, 204, 400, 401, 404, 405)
        receipt["reason"] = "HTTPS_response_received" if good else f"http_{code}_curl_{result.returncode}"
    except (OSError, subprocess.TimeoutExpired):
        good = False
        receipt["reason"] = "transport_unavailable_or_probe_timeout"
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if not good:
        raise NetworkPreflightError(receipt)
    receipt["status"] = "TRANSPORT_REACHABLE_NOT_MODEL_QUALIFIED"
    return env, receipt


def api_proxy_map(environment):
    """Explicit routing for the trusted API relay; no implicit shell fallback."""
    return {
        scheme: value for scheme, value in (
            ("http", environment.get("HTTP_PROXY") or environment.get("ALL_PROXY")),
            ("https", environment.get("HTTPS_PROXY") or environment.get("ALL_PROXY")),
        ) if value
    }
