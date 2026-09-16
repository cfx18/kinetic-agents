"""Subscription controls: zero real model calls; no secrets or scientific jobs."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from kinetic_agents.native.subscription import SubscriptionAccount
from kinetic_agents.native.subscription import SubscriptionTeamClient
from kinetic_agents.native.subscription import MODEL
from kinetic_agents.native.subscription import EFFORT
from kinetic_agents.native.subscription import clean_env
from kinetic_agents.native.subscription import transport_env
from kinetic_agents.native.subscription import quota_snapshot


class AccountTests(unittest.TestCase):
    def test_only_access_token_passes_and_account_switch_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "auth"
            home.mkdir()
            path = home / "auth.json"
            path.write_text(
                json.dumps(
                    {
                        "tokens": {
                            "access_token": "synthetic-access",
                            "account_id": "fixture",
                            "refresh_token": "never-export-this",
                            "id_token": "never-export-this",
                        },
                        "OPENAI_API_KEY": "never-export-this",
                    }
                )
            )
            account = SubscriptionAccount(home, Path(d) / "state")
            account.client = Mock()
            account.client.request.return_value = {"account": {"type": "chatgpt"}}
            value = account.tokens()
            self.assertEqual(
                value,
                {"accessToken": "synthetic-access", "chatgptAccountId": "fixture"},
            )
            account.client.request.assert_called_with(
                "account/read", {"refreshToken": True}
            )
            with self.assertRaises(PermissionError):
                account.tokens("foreign")
            self.assertNotIn("refresh_token", json.dumps(value))
            account.client.request.return_value = {"account": {"type": "apiKey"}}
            with self.assertRaises(PermissionError):
                account.tokens()

    def test_linked_auth_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "auth"
            home.mkdir()
            (Path(d) / "other").write_text("{}")
            (home / "auth.json").symlink_to(Path(d) / "other")
            account = SubscriptionAccount(home, Path(d) / "state")
            account.client = Mock()
            account.client.request.return_value = {"account": {"type": "chatgpt"}}
            with self.assertRaises(PermissionError):
                account.tokens()

    def test_environment_has_no_inherited_secrets_or_history(self):
        env = clean_env("/synthetic-state", "/synthetic-codex")
        self.assertEqual(set(env), {"PATH", "LANG", "HOME", "CODEX_HOME"})
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_host_transport_keeps_network_route_but_not_identity_or_shell_secrets(self):
        source = {
            "HTTPS_PROXY": "http://fixture-user:fixture-password@127.0.0.1:19001",
            "https_proxy": "http://127.0.0.1:19001",
            "NO_PROXY": "localhost,127.0.0.1",
            "CODEX_CA_CERTIFICATE": "/synthetic/ca.pem",
            "OPENAI_API_KEY": "never-copy-api-key",
            "CODEX_ACCESS_TOKEN": "never-copy-account",
            "BASH_ENV": "/synthetic/unsafe-startup",
            "CODEX_HOME": "/synthetic/wrong-account",
            "PYTHONPATH": "/synthetic/foreign-code",
        }
        env = transport_env("/owned-state", "/owned-codex", source)
        self.assertEqual(env["HTTPS_PROXY"], source["HTTPS_PROXY"])
        self.assertEqual(env["CODEX_HOME"], "/owned-codex")
        self.assertEqual(
            set(env),
            set(clean_env("/owned-state", "/owned-codex"))
            | {
                "HTTPS_PROXY",
                "https_proxy",
                "NO_PROXY",
                "CODEX_CA_CERTIFICATE",
            },
        )
        from kinetic_agents.native.profiles import NativeSoloConfig

        policy = NativeSoloConfig().permission_settings(
            "/synthetic/work", "/synthetic/task"
        )
        self.assertEqual(policy["shell_environment_policy.inherit"], "none")
        self.assertNotIn("HTTPS_PROXY", policy["shell_environment_policy.set"])
        self.assertNotIn("fixture-password", json.dumps(policy))

    def test_transport_without_proxy_keeps_original_clean_environment(self):
        self.assertEqual(
            transport_env("/owned", "/owned/codex", {}),
            clean_env("/owned", "/owned/codex"),
        )


def bare_client():
    client = object.__new__(SubscriptionTeamClient)
    client.subscription_save = Mock()
    client.subscription_stop = Mock()
    client.account = Mock()
    client.account.tokens.return_value = {
        "accessToken": "synthetic",
        "chatgptAccountId": "fixture",
    }
    client.thread_id = "principal"
    client.timeout = 120
    return client


class SubscriptionControlTests(unittest.TestCase):
    def test_pinned_metadata_before_any_research_thread(self):
        client = bare_client()
        replies = {
            "account/login/start": {"type": "chatgptAuthTokens"},
            "account/read": {"account": {"type": "chatgpt"}},
            "model/list": {
                "data": [
                    {
                        "model": MODEL,
                        "supportedReasoningEfforts": [{"reasoningEffort": EFFORT}],
                    }
                ]
            },
            "account/rateLimits/read": {"rateLimits": {"primary": {"usedPercent": 20}}},
        }
        client.request = Mock(side_effect=lambda m, p: replies[m])
        client.prepare_account()
        methods = [c.args[0] for c in client.request.call_args_list]
        self.assertNotIn("turn/start", methods)
        self.assertNotIn("thread/start", methods)
        self.assertEqual(client.provider, "openai")
        saved = [c.args for c in client.subscription_save.call_args_list]
        self.assertNotIn("synthetic", json.dumps(saved))
        self.assertFalse(saved[-1][1]["real_inference_verified"])

    def test_unavailable_exact_effort_does_not_fallback(self):
        client = bare_client()

        def response(m, p):
            if m == "account/login/start":
                return {"type": "chatgptAuthTokens"}
            if m == "account/read":
                return {"account": {"type": "chatgpt"}}
            return {
                "data": [
                    {
                        "model": MODEL,
                        "supportedReasoningEfforts": [{"reasoningEffort": "ultra"}],
                    }
                ]
            }

        client.request = Mock(side_effect=response)
        with self.assertRaises(PermissionError):
            client.prepare_account()
        self.assertFalse(
            any(c.args[0] == "turn/start" for c in client.request.call_args_list)
        )

    def test_quota_exhaustion_is_explicit_no_credit_reset(self):
        client = bare_client()
        with self.assertRaises(PermissionError):
            client._quota(
                {"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": 12345}}}
            )
        client.subscription_stop.assert_called_once_with("subscription_quota_exhausted")
        self.assertEqual(
            quota_snapshot(
                {
                    "rateLimits": {
                        "email": "private",
                        "primary": {"usedPercent": 3, "secret": "no"},
                    }
                }
            ),
            {"codex": {"primary": {"usedPercent": 3}}},
        )

    def test_auth_refresh_never_enters_scientific_dispatch_or_logs(self):
        client = bare_client()
        client.send = Mock()
        client.handle_message(
            {
                "id": 7,
                "method": "account/chatgptAuthTokens/refresh",
                "params": {"previousAccountId": "fixture"},
            }
        )
        client.account.tokens.assert_called_once_with("fixture")
        self.assertEqual(
            client.send.call_args.args[0]["result"]["accessToken"], "synthetic"
        )
        client.subscription_save.assert_not_called()

    def test_rerouted_model_is_not_accepted(self):
        client = bare_client()
        with self.assertRaises(PermissionError):
            client.handle_message(
                {"method": "model/rerouted", "params": {"toModel": "other"}}
            )

    def test_usage_snapshot_does_not_sum_duplicate_notifications(self):
        client = bare_client()
        with patch("kinetic_agents.native.team_client.NativeTeamClient.handle_message"):
            message = {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "principal",
                    "tokenUsage": {
                        "total": {"inputTokens": 120, "outputTokens": 20, "bad": "no"},
                        "last": {"totalTokens": 10},
                    },
                },
            }
            client.handle_message(message)
            client.handle_message(message)
        value = client.subscription_save.call_args.args[1]
        self.assertEqual(value["token_usage"]["total"]["inputTokens"], 120)
        self.assertIsNone(value["api_usd"])
        self.assertNotIn("bad", value["token_usage"]["total"])


if __name__ == "__main__":
    unittest.main()
