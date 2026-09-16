"""Host-only API bindings. Dotenv is data, never shell code or a run artifact."""

from pathlib import Path
import os
import re
import stat
from urllib.parse import urlsplit

from kinetic_agents.native.subscription import plain

PROTOCOLS = {"responses", "chat_completions", "anthropic"}
COMPATIBLE = {
    "codex": {"responses"},
    "claude_code": {"anthropic"},
    "kimi_code": PROTOCOLS,
    "kimi_code_node": {"chat_completions"},
}


def dotenv(path):
    path = plain(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
        raise PermissionError(".env must be a regular single-link file below 64 KiB")
    if info.st_mode & 0o077:
        raise PermissionError(".env contains host credentials: chmod 600 before use")
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if not match or match[1] in values:
            raise ValueError(f"invalid or duplicate .env assignment at line {number}")
        key, value = match.groups()
        value = value.strip()
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"invalid .env quoting at line {number}")
            value = value[1:-1]
        if any(c in value for c in ("\x00", "\r", "\n", "`")) or "$(" in value or "${" in value:
            raise ValueError(f".env expansion is not supported at line {number}")
        values[key] = value
    return values


def validate_backend(value, harness):
    if value == {"auth": "subscription"}:
        if harness != "codex":
            raise ValueError("subscription adapter is Codex-only; select api for this harness")
        return value
    required = {"auth", "protocol", "env_file", "base_url_env", "api_key_env"}
    if not isinstance(value, dict) or set(value) != required or value["auth"] != "api":
        raise ValueError(
            "backend requires auth/protocol/env_file/base_url_env/api_key_env; no inline secrets"
        )
    if value["protocol"] not in COMPATIBLE[harness]:
        raise ValueError(
            "harness/API protocol mismatch; this adapter does not silently translate APIs"
        )
    for key in ("base_url_env", "api_key_env"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", value[key]):
            raise ValueError("backend environment names must be uppercase identifiers")
    if value["base_url_env"] == value["api_key_env"]:
        raise ValueError("endpoint and key must use distinct environment names")
    return value


def credentials(backend):
    values = dotenv(backend["env_file"])
    # File wins, so a login-shell export cannot silently change the experiment.
    endpoint = values.get(backend["base_url_env"], os.environ.get(backend["base_url_env"], ""))
    key = values.get(backend["api_key_env"], os.environ.get(backend["api_key_env"], ""))
    parts = urlsplit(endpoint)
    if (
        not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.scheme not in ("https", "http")
        or parts.scheme == "http"
        and parts.hostname not in ("127.0.0.1", "localhost", "::1")
    ):
        raise ValueError(
            "API base URL must be HTTPS (or loopback HTTP), without credentials/query/fragment"
        )
    if not key or any(c.isspace() for c in key):
        raise ValueError("API key is missing or invalid; fill the configured .env key")
    return endpoint.rstrip("/"), key


def bind_backend(backend):
    if backend["auth"] == "subscription":
        return dict(backend)
    endpoint, _ = credentials(backend)
    return {**backend, "base_url": endpoint}


def load_credentials(binding):
    endpoint, key = credentials(binding)
    if endpoint != binding["base_url"]:
        raise PermissionError("API endpoint changed after prepare; create a new run")
    return endpoint, key
