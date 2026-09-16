"""Run-scoped transparent API relay. No protocol translation, retries or keys in CLI env."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import ssl
import threading
import time
import urllib.request
import urllib.error

from kinetic_agents.connections import load_credentials
from kinetic_agents.observability.transcript import open_append


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PermissionError("API redirects are disabled")


class APIGateway:
    def __init__(self, binding, models, state, guard=lambda: None, *, network_environment=None, expected_efforts=None):
        self.binding, self.models, self.state, self.guard = (
            binding,
            set(models),
            Path(state),
            guard,
        )
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.closed = False
        self.server = None
        self.network_environment = network_environment
        self.expected_efforts = dict(expected_efforts or {})

    def audit(self, **values):
        # Metadata only. Provider bodies may contain reasoning or echoed secrets.
        self.state.mkdir(parents=True, exist_ok=True)
        with self.lock, open_append(self.state / "api_requests.jsonl") as stream:
            stream.write(json.dumps({"at": time.time(), **values}) + "\n")
            stream.flush()

    def start(self):
        endpoint, key = load_credentials(self.binding)
        gateway = self
        handlers = [NoRedirect()]
        if self.network_environment is None:
            handlers.append(urllib.request.ProxyHandler({}))  # Legacy explicit direct route.
        else:
            from kinetic_agents.network import api_proxy_map

            env = self.network_environment
            context = ssl.create_default_context(
                cafile=env.get("CODEX_CA_CERTIFICATE") or env.get("SSL_CERT_FILE"),
                capath=env.get("SSL_CERT_DIR"),
            )
            handlers.extend([
                urllib.request.ProxyHandler(api_proxy_map(env)),
                urllib.request.HTTPSHandler(context=context),
            ])
        opener = urllib.request.build_opener(*handlers)
        routes = {
            "responses": {"/responses", "/responses/compact"},
            "chat_completions": {"/chat/completions"},
            "anthropic": {"/v1/messages", "/v1/messages/count_tokens"},
        }[self.binding["protocol"]]

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def do_POST(self):
                headers_sent = False
                request_id = secrets.token_hex(12)
                try:
                    supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    supplied = self.headers.get("x-api-key", supplied)
                    if (
                        not secrets.compare_digest(supplied, gateway.token)
                        or gateway.closed
                        or self.path not in routes
                    ):
                        self.send_error(403)
                        return
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 32 * 1024 * 1024:
                        self.send_error(413)
                        return
                    body = self.rfile.read(size)
                    value = json.loads(body)
                    if value.get("model") not in gateway.models:
                        self.send_error(403, "Unregistered model")
                        return
                    expected = gateway.expected_efforts.get(value["model"])
                    if expected is not None and value.get("reasoning_effort") != expected:
                        gateway.audit(id=request_id, status="REJECTED_CONFIGURATION", model=value["model"])
                        self.send_error(403, "Reasoning effort differs from frozen contract")
                        return
                    gateway.guard()
                    gateway.audit(
                        id=request_id,
                        status="STARTED",
                        model=value["model"],
                        route=self.path,
                        request_settings={k: value[k] for k in (
                            "reasoning_effort", "max_tokens", "max_completion_tokens", "stream"
                        ) if isinstance(value.get(k), (str, int, bool))},
                    )
                    headers = {
                        "Content-Type": "application/json",
                        "Accept": self.headers.get("Accept", "application/json"),
                    }
                    if gateway.binding["protocol"] == "anthropic":
                        headers.update(
                            {
                                "x-api-key": key,
                                "anthropic-version": self.headers.get(
                                    "anthropic-version", "2023-06-01"
                                ),
                            }
                        )
                        if self.headers.get("anthropic-beta"):
                            headers["anthropic-beta"] = self.headers["anthropic-beta"]
                    else:
                        headers["Authorization"] = "Bearer " + key
                    suffix = self.path
                    if endpoint.endswith("/v1") and suffix.startswith("/v1/"):
                        suffix = suffix[3:]
                    request = urllib.request.Request(endpoint + suffix, data=body, headers=headers)
                    with opener.open(request, timeout=120) as upstream:
                        self.send_response(upstream.status)
                        content_type = upstream.headers.get("Content-Type", "application/json")
                        self.send_header("Content-Type", content_type)
                        self.end_headers()
                        headers_sent = True
                        while True:
                            block = (
                                upstream.readline()
                                if "event-stream" in content_type
                                else upstream.read(65536)
                            )
                            if not block:
                                break
                            self.wfile.write(block)
                            self.wfile.flush()
                            # Extract only accounting fields, not a raw response dump.
                            if block.startswith(b"data: "):
                                try:
                                    event = json.loads(block[6:])
                                    payload = event.get("response", event.get("message", event))
                                    usage = payload.get("usage")
                                    if isinstance(usage, dict):
                                        safe = {
                                            k: v
                                            for k, v in usage.items()
                                            if type(v) is int and v >= 0
                                        }
                                        if safe:
                                            gateway.audit(
                                                id=request_id,
                                                status="USAGE_OBSERVATION",
                                                usage=safe,
                                            )
                                except (ValueError, AttributeError):
                                    pass
                    gateway.audit(id=request_id, status="FINISHED", model=value["model"])
                except Exception as exc:
                    gateway.audit(
                        id=request_id,
                        status="FAILED",
                        error_type=type(exc).__name__,
                        upstream_status=getattr(exc, "code", None),
                    )
                    if not headers_sent:
                        self.send_error(
                            502, "Upstream request failed; see host accounting metadata"
                        )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.closed = True
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
