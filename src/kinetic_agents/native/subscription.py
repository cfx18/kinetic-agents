"""Official Codex/ChatGPT subscription transport; no API proxy or API billing.

The host's metadata-only Codex process owns OAuth refresh. Only a short-lived
access token crosses a private stdio RPC into the isolated research app-server.
No auth.json, refresh token, global config or previous sessions are copied into
the research home. This is the documented experimental chatgptAuthTokens mode.
"""

import json
import hashlib
import os
from pathlib import Path
import shutil
import stat
import time
import threading

from kinetic_agents.native.rpc import MetadataClient
from kinetic_agents.native.dispatch import DispatchReceiver
from kinetic_agents.native.permissions import flags
from kinetic_agents.core.storage import atomic
from kinetic_agents.native.team_client import NativeTeamClient
from kinetic_agents.native.profiles import NativeSoloConfig
from kinetic_agents.native.profiles import NativeTeamConfig
from kinetic_agents.observability.transcript import LiveTranscript
from kinetic_agents.observability.transcript import TranscriptTransport

MODEL = "gpt-6-astra"
EFFORT = "xhigh"


def plain(path):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise PermissionError("plain host path required")
    return path


def clean_env(home, codex_home):
    # Do not pass API keys, SSH agents, proxy secrets or the caller's PYTHONPATH.
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
    }


HOST_NETWORK_ENV = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "CODEX_CA_CERTIFICATE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
)


def transport_env(home, codex_home, source=None):
    """Network routing for trusted host transports, NOT the agent's shell.

    Subscription OAuth/model traffic must use the host's configured route and CA.
    Do not copy API keys, caller identity, startup files, or arbitrary environment.
    Agent commands still get the fixed shell_environment_policy from the profile.
    Proxy URLs may contain credentials: never serialize these values to evidence.
    """
    source = os.environ if source is None else source
    return {
        **clean_env(home, codex_home),
        **{key: source[key] for key in HOST_NETWORK_ENV if source.get(key)},
    }


class AccountHost(MetadataClient):
    METHODS = {"initialize", "account/read"}  # Cannot create/resume a research thread.


class SubscriptionAccount:
    """A lifecycle dependency, not a network gateway. Never log RPC credentials."""

    token = None

    def __init__(self, auth_home, state):
        self.auth_home, self.state = plain(auth_home), plain(state)
        self.client = None
        self.account_id = None
        self.lock = threading.RLock()

    def start(self):
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        settings = {
            "sqlite_home": str(self.state / "sqlite"),
            "log_dir": str(self.state / "logs"),
            "project_doc_max_bytes": 0,
            "features.memories": False,
            "features.multi_agent": False,
        }
        self.client = AccountHost(
            ["codex", *flags(settings), "app-server", "--stdio"],
            str(self.state),
            30,
            env=transport_env(self.auth_home.parent, self.auth_home),
        )
        try:
            self.client.request(
                "initialize",
                {"clientInfo": {"name": "cfx_subscription_auth_host", "version": "1"}},
            )
            self.client.send({"method": "initialized", "params": {}})
            self.tokens()  # Fail before creating a research thread if not ChatGPT/file auth.
            self.client.timeout = (
                8  # Server-requested token refresh has a ~10s RPC deadline.
            )
        except BaseException:
            self.close()
            raise
        return self

    def tokens(self, previous_account_id=None):
        with self.lock:
            return self._tokens(previous_account_id)

    def _tokens(self, previous_account_id=None):
        account = self.client.request("account/read", {"refreshToken": True})
        if (account.get("account") or {}).get("type") != "chatgpt":
            raise PermissionError(
                "ChatGPT subscription login required; API fallback disabled"
            )
        # Managed Codex refreshes its own file. We do not implement OAuth or copy
        # refresh/id tokens. OS keychain-only auth needs a separate Codex login.
        path = plain(self.auth_home / "auth.json")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > 131072
                or info.st_nlink != 1
            ):
                raise PermissionError(
                    "regular bounded host authentication file required"
                )
            data = json.load(stream)
        tokens = data.get("tokens") or {}
        access, account_id = tokens.get("access_token"), tokens.get("account_id")
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(account_id, str)
            or not account_id
        ):
            raise PermissionError("managed file-backed ChatGPT tokens unavailable")
        if (self.account_id is not None and account_id != self.account_id) or (
            previous_account_id is not None and previous_account_id != account_id
        ):
            raise PermissionError("subscription account changed during the run")
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        binding = self.state / "account_identity.json"
        identity = {"account_sha256": hashlib.sha256(account_id.encode()).hexdigest()}
        if binding.exists() and json.loads(binding.read_text()) != identity:
            raise PermissionError(
                "subscription account differs from the original session"
            )
        if not binding.exists():
            atomic(binding, identity)
        self.account_id = account_id
        return {"accessToken": access, "chatgptAccountId": account_id}

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None


def quota_snapshot(value):
    """Allowlisted metadata only; never account email, auth, or arbitrary errors."""
    buckets = value.get("rateLimitsByLimitId") or {"codex": value.get("rateLimits")}
    result = {}
    for name, row in buckets.items():
        if not isinstance(row, dict):
            continue
        item = {}
        for window in ("primary", "secondary"):
            raw = row.get(window)
            if isinstance(raw, dict):
                item[window] = {
                    key: raw[key]
                    for key in ("usedPercent", "windowDurationMins", "resetsAt")
                    if isinstance(raw.get(key), (int, float))
                }
        if isinstance(row.get("rateLimitReachedType"), str):
            item["limit_reached"] = True
        result[str(name)[:100]] = item
    return result


class SubscriptionTeamClient(TranscriptTransport, NativeTeamClient):
    is_researcher = False
    allowed_team_sizes = {1, 3}
    provider = "openai"
    METHODS = NativeTeamClient.METHODS | {
        "account/login/start",
        "account/read",
        "account/rateLimits/read",
        "model/list",
    }

    def __init__(self, *args, **kwargs):
        # Both principal and workers use the installed native loop. Only the
        # lifecycle transport is host-scoped: native V2 forks lose dynamic tools.
        from kinetic_agents.native.workers import WorkerPool

        self.executable = kwargs.pop("executable", None)
        service = args[7] if len(args) > 7 else kwargs["service"]
        self.worker_pool = (
            WorkerPool(self) if service.store.identity.max_members > 1 else None
        )
        if self.worker_pool:
            kwargs["extra_tools"] = (
                list(kwargs.get("extra_tools", ())) + self.worker_pool.schemas()
            )
            kwargs["actor_tools"] = {
                **kwargs.get("actor_tools", {}),
                **self.worker_pool.handlers(),
            }
            kwargs["actor_read_only"] = set(kwargs.get("actor_read_only", ())) | {
                "research_workers"
            }
        super().__init__(*args, **kwargs)
        if self.worker_pool:
            self.worker_pool.load()

    def close(self):
        try:
            if getattr(self, "worker_pool", None):
                self.worker_pool.close()
        finally:
            super().close()

    def prepare_worker_account(self, client):
        return SubscriptionTeamClient.prepare_account(client)

    def request(self, method, params):
        if method == "thread/start":
            # Version-qualified experimental observation only. The recorder
            # accepts public calls/outputs, never reasoning or auth items.
            params = {**params, "experimentalRawEvents": True}
        result = super().request(method, params)
        if method == "thread/resume":
            from kinetic_agents.observability.rollout import LiveRollout

            thread = result.get("thread") or {}
            if thread.get("id") != params.get("threadId") or not thread.get("path"):
                raise PermissionError(
                    "owned resume path required for complete observable tool capture"
                )
            if getattr(self, "rollout_capture", None) is not None:
                raise PermissionError("one live rollout attachment per native client")
            self.rollout_capture = LiveRollout(
                thread["path"],
                self.state / "codex/sessions",
                thread["id"],
                self.transcript,
            )
        return result

    def poll(self):
        tail = getattr(self, "rollout_capture", None)
        if tail is not None:
            tail.check()
        return super().poll()

    def baseline_identity(self):
        return {
            **super().baseline_identity(),
            "subscription_overrides": {
                "agents.enabled": False,
                "features.multi_agent_v2": False,
                "delegation_transport": (
                    "host_scoped_native_workers"
                    if self.team.service.store.identity.max_members > 1
                    else "disabled"
                ),
                "researcher_model": self.team.service.store.identity.researcher_model,
                "researcher_effort": self.team.service.store.identity.researcher_effort,
                "max_members": self.team.service.store.identity.max_members,
                "model": self.model,
                "model_reasoning_effort": self.effort,
                "effective_tool_transport": "native_Astra_code_mode",
                "live_capture": "public_only_experimentalRawEvents_qualified_0.153.4",
                "note": "legacy feature flags alone do not describe Astra effective tool surface",
            },
        }

    def _transport_init(
        self,
        work,
        task,
        state,
        model,
        effort,
        account,
        token,
        tools,
        *,
        baseline_config
    ):
        identity = self.team.service.store.identity
        mixed = identity.max_members == 3
        worker = getattr(self, "is_researcher", False)
        expected = (
            (identity.researcher_model, identity.researcher_effort)
            if worker
            else (identity.model, identity.effort)
        )
        if (model, effort) != expected or type(baseline_config) is not (
            NativeSoloConfig if worker or not mixed else NativeTeamConfig
        ):
            raise PermissionError(
                "subscription model/effort must match the registered role"
            )
        self.baseline, self.account = baseline_config, account
        self.work, self.task, self.state = (plain(p) for p in (work, task, state))
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.state / "codex").mkdir(mode=0o700, exist_ok=True)
        if (self.state / "codex/auth.json").exists():
            raise PermissionError("research home must not contain managed credentials")
        self.model, self.effort, self.specs = model, effort, tools
        settings = {
            **self.baseline.permission_settings(self.work, self.task),
            **self.baseline.codex_overrides(),
            "model": model,
            "model_provider": "openai",
            "model_reasoning_effort": effort,
            "agents.enabled": False,
            "features.multi_agent_v2": False,
            "features.multi_agent": False,
            "service_tier": "default",
            "cli_auth_credentials_store": "ephemeral",
            "sqlite_home": str(self.state / "sqlite"),
            "log_dir": str(self.state / "logs"),
        }
        binary = getattr(self, "executable", None) or shutil.which("codex")
        if binary is None:
            raise RuntimeError("Codex executable unavailable")
        self.transcript = LiveTranscript(
            getattr(self, "transcript_root", self.state.parent)
        )
        try:
            MetadataClient.__init__(
                self,
                [binary, *flags(settings), "app-server", "--stdio"],
                str(self.work),
                120,
                env=transport_env(self.state, self.state / "codex"),
            )
        except BaseException:
            self.transcript.close()
            raise
        self.receiver = DispatchReceiver(
            self.process.stdout, self.process.stderr, observer=self.transcript.incoming
        )
        self.dispatch_observer = None
        self.thread_id = self.turn_id = self.completed = self.usage = None
        self.tools, self.calls, self.unexpected_items, self.rejected_requests = (
            {},
            [],
            [],
            [],
        )
        self.subscription_stop = None
        self.subscription_save = lambda name, value: atomic(self.state / name, value)
        from kinetic_agents.native.usage import SubscriptionTeamState

        self.team_observations = getattr(
            self, "shared_observations", None
        ) or SubscriptionTeamState(self.state, identity)

    def start_or_resume(self, owned_thread=None):
        result = super().start_or_resume(owned_thread)
        self.team_observations.bind(
            result["thread_id"], None, self.model, self.effort, observed=True
        )
        self.transcript.record(
            "actor_registered",
            dict(
                threadId=result["thread_id"],
                parent=None,
                role="principal",
                model=self.model,
                effort=self.effort,
                native_metadata_verified=True,
            ),
        )
        return result

    def prepare_account(self):
        result = self.request(
            "account/login/start",
            {"type": "chatgptAuthTokens", **self.account.tokens()},
        )
        if result.get("type") != "chatgptAuthTokens":
            raise PermissionError("official external ChatGPT authentication rejected")
        account = self.request("account/read", {"refreshToken": False})
        if (account.get("account") or {}).get("type") != "chatgpt":
            raise PermissionError("not a ChatGPT subscription; no API fallback")
        identity = getattr(
            getattr(getattr(self, "team", None), "service", None), "store", None
        )
        identity = identity.identity if identity is not None else None
        required = (
            {(identity.model, identity.effort)}
            if identity is not None
            else {(MODEL, EFFORT)}
        )
        if identity is not None and identity.researcher_model is not None:
            required.add((identity.researcher_model, identity.researcher_effort))
        found, cursor = [], None
        for _ in range(20):
            page = self.request(
                "model/list", {"includeHidden": True, "limit": 100, "cursor": cursor}
            )
            found.extend(page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        else:
            raise PermissionError("model catalog pagination exceeded bound")
        offered = {
            (m.get("model", m.get("id")), v["reasoningEffort"])
            for m in found
            for v in m.get("supportedReasoningEfforts", [])
        }
        if not required <= offered:
            raise PermissionError(
                "requested principal/researcher model or reasoning effort unavailable; no substitution"
            )
        old_timeout = self.timeout
        self.timeout = min(old_timeout, 15)
        try:
            limits = self.request("account/rateLimits/read", {})
        except (RuntimeError, TimeoutError) as exc:
            # This metadata endpoint is not available for every account/channel.
            # Do not confuse a missing usage dashboard with a successful quota
            # check or a failed model. Native service limits remain authoritative.
            self.subscription_save(
                "subscription_quota.json",
                dict(
                    status="UNAVAILABLE",
                    error_type=type(exc).__name__,
                    at=time.time(),
                    scope="shared_account_not_per_run_cost",
                    automatic_purchase=False,
                ),
            )
        else:
            self._quota(limits)
        finally:
            self.timeout = old_timeout
        self.subscription_save(
            "subscription_identity.json",
            dict(
                model=getattr(self, "model", MODEL),
                effort=getattr(self, "effort", EFFORT),
                provider="openai",
                auth="chatgptAuthTokens",
                max_members=identity.max_members if identity else 1,
                required_models=[
                    {"model": m, "effort": e} for m, e in sorted(required)
                ],
                api_fallback=False,
                metadata_verified=True,
                real_inference_verified=False,
                host_network_env_names=sorted(
                    key for key in HOST_NETWORK_ENV if os.environ.get(key)
                ),
                at=time.time(),
            ),
        )

    def _quota(self, value):
        snapshot = quota_snapshot(value)
        self.subscription_save(
            "subscription_quota.json",
            dict(
                buckets=snapshot,
                at=time.time(),
                scope="shared_account_not_per_run_cost",
                automatic_purchase=False,
            ),
        )
        # Do not consume earned resets, buy credits or switch to a paid API.
        if any(
            row.get("limit_reached")
            or any(
                row.get(w, {}).get("usedPercent", 0) >= 100
                for w in ("primary", "secondary")
            )
            for row in snapshot.values()
        ):
            if self.subscription_stop:
                self.subscription_stop("subscription_quota_exhausted")
            raise PermissionError("subscription quota exhausted; state retained")

    def handle_message(self, message):
        method, params = message.get("method"), message.get("params") or {}
        if (
            method == "item/tool/call"
            and params.get("tool") == "finish_research"
            and getattr(self, "worker_pool", None)
            and self.worker_pool.status(None, {}, None)["active"]
        ):
            self.send(
                {
                    "id": message["id"],
                    "result": self.team.error(
                        ValueError(
                            "researchers are still active; collect their deliverables before final submission"
                        )
                    ),
                }
            )
            return
        if method == "account/chatgptAuthTokens/refresh" and "id" in message:
            try:
                result = self.account.tokens(params.get("previousAccountId"))
            except Exception:
                self.send(
                    {
                        "id": message["id"],
                        "error": {
                            "code": -32000,
                            "message": "Host subscription refresh unavailable",
                        },
                    }
                )
                raise PermissionError(
                    "host subscription authentication refresh failed"
                ) from None
            self.send({"id": message["id"], "result": result})
            return
        if method == "account/rateLimits/updated":
            self._quota(params)
            return
        if method == "model/rerouted":
            raise PermissionError("model rerouting forbidden in the pinned run")
        if method == "turn/completed" and (
            params.get("threadId") == self.thread_id
            or params.get("threadId")
            in getattr(getattr(self, "team", None), "members", {})
        ):
            turn = params.get("turn") or {}
            if turn.get("status") != "completed":
                error = turn.get("error") or {}
                info = error.get("codexErrorInfo")
                # Only typed protocol categories, not raw provider text/prompts.
                kinds = (
                    [info]
                    if isinstance(info, str)
                    else list(info) if isinstance(info, dict) else []
                )
                kinds = [
                    v
                    for v in kinds
                    if isinstance(v, str) and v.isalnum() and len(v) < 80
                ]
                self.subscription_save(
                    "subscription_error.json",
                    dict(
                        at=time.time(),
                        turn_status=turn.get("status"),
                        categories=kinds,
                        error_message_logged=False,
                    ),
                )
                if any(
                    v.lower()
                    in ("usagelimitexceeded", "ratelimitexceeded", "usagelimitreached")
                    for v in kinds
                ):
                    if self.subscription_stop:
                        self.subscription_stop("subscription_quota_exhausted")
        observations = getattr(self, "team_observations", None)
        if observations and method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "subAgentActivity" and item.get("kind") == "started":
                raise PermissionError(
                    "unqualified native fork; use the scoped research_spawn interface"
                )
        if observations and method == "item/completed":
            item = params.get("item") or {}
            if (
                item.get("type") == "collabAgentToolCall"
                and item.get("tool") == "spawnAgent"
                and item.get("status") == "completed"
            ):
                raise PermissionError(
                    "unqualified native fork; use the scoped research_spawn interface"
                )
        if observations and method == "thread/tokenUsage/updated":
            if observations.usage(
                params.get("threadId"), params.get("tokenUsage") or {}
            ):
                return super().handle_message(message)
        if (
            not observations
            and method == "thread/tokenUsage/updated"
            and params.get("threadId") == self.thread_id
        ):
            usage = params.get("tokenUsage") or {}
            # Cumulative server totals replace a snapshot, rather than summing
            # repeated updates. Missing metrics stay missing, not zero dollars.
            safe = {
                kind: {k: v for k, v in raw.items() if type(v) is int and v >= 0}
                for kind, raw in usage.items()
                if kind in ("total", "last") and isinstance(raw, dict)
            }
            self.subscription_save(
                "subscription_usage.json",
                dict(
                    thread_id=self.thread_id,
                    token_usage=safe,
                    billing="ChatGPT_subscription",
                    api_usd=None,
                    api_request_count=None,
                    auxiliary_calls_included="native_thread_usage_as_reported",
                    at=time.time(),
                ),
            )
        return super().handle_message(message)
