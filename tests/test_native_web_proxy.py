"""Model-free upstream routing and isolation checks using a synthetic proxy.

The opt-in native checks contact ONLY our local fixture through Codex's managed
proxy. No model, account, scientific input, or external HTTP origin is used.
A public IP literal avoids unrelated public DNS variability; the synthetic
upstream returns the response without connecting to that IP. Real HTTPS/DNS
reachability is checked separately and is not asserted by this fixture.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from kinetic_agents.native.baseline import NativeBaselineConfig, resolve_baseline
from kinetic_agents.native.permissions import SHELL_ENV, flags
from kinetic_agents.native.profiles import NativeSoloConfig, NativeTeamConfig
from kinetic_agents.native.public_profile import NativeOpenWorldConfig
from kinetic_agents.native.subscription import transport_env


class ProxyProfileTests(unittest.TestCase):
    def test_new_profiles_pin_upstream_route_and_keep_restrictions(self):
        for cls in (NativeSoloConfig, NativeTeamConfig):
            profile = cls()
            policy = profile.permission_settings("/synthetic/work", "/synthetic/input")
            prefix = f"permissions.{profile.permission_profile}"
            self.assertTrue(policy[prefix + ".network.allow_upstream_proxy"])
            self.assertFalse(policy[prefix + ".network.allow_local_binding"])
            self.assertEqual(policy[prefix + ".network.domains"]["localhost"], "deny")
            self.assertEqual(
                policy[prefix + ".network.domains"]["169.254.169.254"], "deny"
            )
            self.assertEqual(policy[prefix + ".filesystem"][":root"], "deny")
            self.assertEqual(policy["shell_environment_policy.inherit"], "none")
            self.assertEqual(policy["shell_environment_policy.set"], SHELL_ENV)
            self.assertTrue(profile.codex_overrides()["features.network_proxy"])
            self.assertEqual(resolve_baseline(profile).identity(), profile.identity())
            self.assertTrue(profile.identity()["settings"]["allow_upstream_proxy"])
            self.assertTrue(profile.profile.endswith("-v3"))

    def test_route_cannot_be_silently_overridden_or_old_snapshot_relabelled(self):
        for cls in (NativeSoloConfig, NativeTeamConfig):
            with self.assertRaises(ValueError):
                cls(allow_upstream_proxy=False)
            old = cls().snapshot()
            old["profile"] = old["profile"].replace("-v3", "-v2")
            with self.assertRaises(ValueError):
                cls.from_mapping(old)

    def test_legacy_profiles_unchanged_and_solo_team_networks_equal(self):
        old = NativeOpenWorldConfig()
        self.assertFalse(
            old.permission_settings("/w", "/t")[
                f"permissions.{old.permission_profile}.network.allow_upstream_proxy"
            ]
        )
        self.assertFalse(NativeBaselineConfig().network_access)
        self.assertEqual(
            NativeSoloConfig().permission_settings("/w", "/t"),
            NativeTeamConfig().permission_settings("/w", "/t"),
        )


@unittest.skipUnless(
    os.environ.get("RUN_NATIVE_WEB_QUALIFICATION") == "1",
    "explicit native sandbox + synthetic upstream proxy only",
)
class NativeWebProxyTests(unittest.TestCase):
    def check_profile(self, profile):
        observed = []

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append(self.path)
                body = b"cfx-upstream-fixture-public-response"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # As in the existing native tests: public input outside masked /tmp.
            with tempfile.TemporaryDirectory(
                prefix="cfx-web-input-", dir=Path(__file__).resolve().parents[1]
            ) as public, tempfile.TemporaryDirectory(
                prefix="cfx-web-native-"
            ) as directory:
                root, task = Path(directory), Path(public)
                work, state = root / "work", root / "state"
                work.mkdir()
                (state / "codex").mkdir(parents=True)
                (task / "INPUT.txt").write_text("synthetic public input")
                private = root / "private-canary"
                private.write_text("synthetic forbidden host content")
                (work / "escape").symlink_to(private)
                proxy = f"http://127.0.0.1:{server.server_port}"
                env = transport_env(
                    state,
                    state / "codex",
                    {
                        "HTTP_PROXY": proxy,
                        "HTTPS_PROXY": proxy,
                        "ALL_PROXY": proxy,
                        "http_proxy": proxy,
                        "https_proxy": proxy,
                        "all_proxy": proxy,
                    },
                )
                settings = {
                    **profile.codex_overrides(),
                    **profile.permission_settings(work, task),
                }
                domains = settings[
                    f"permissions.{profile.permission_profile}.network.domains"
                ]
                domains["blocked.example.com"] = "deny"  # Narrower test-only policy.
                source = r"""import json,os,socket,sys,urllib.request,urllib.error
from pathlib import Path
task,private=map(Path,sys.argv[1:3]); upstream_port=int(sys.argv[3])
assert (task/'INPUT.txt').read_text()=='synthetic public input'
Path('written.txt').write_text('native work write')
for path in (private,Path('escape')):
    try: path.read_bytes()
    except OSError as exc: assert exc.errno in (1,2,13),exc.errno
    else: raise AssertionError('private file or symlink escape allowed')
try: (task/'modified.txt').write_text('forbidden')
except OSError as exc: assert exc.errno in (1,13,30),exc.errno
else: raise AssertionError('public task is writable')
try: response=urllib.request.urlopen('http://1.1.1.1/cfx-public-proxy-fixture',timeout=8)
except urllib.error.HTTPError as exc:
    raise AssertionError('public fixture denied: '+exc.read(512).decode('utf-8','replace')) from exc
with response:
    assert response.status==200
    assert response.read()==b'cfx-upstream-fixture-public-response'
denied=[]
for target in ('http://localhost/cfx-denied','http://127.0.0.1/cfx-denied',
               'http://[::1]/cfx-denied','http://10.0.0.1/cfx-denied',
               'http://169.254.169.254/cfx-denied','http://blocked.example.com/cfx-denied'):
    try: urllib.request.urlopen(target,timeout=3)
    except urllib.error.HTTPError as exc:
        # CLI 0.153.4 rejects this bracketed IPv6 HTTP target at parsing (400).
        # Count rejection before upstream, not IPv6 parser/guard support.
        expected=(400,403) if target.startswith('http://[::1]') else (403,)
        assert exc.code in expected,(target,exc.code)
        denied.append(target)
    else: raise AssertionError('denied destination succeeded')
try: socket.create_connection(('127.0.0.1',upstream_port),timeout=2)
except OSError: pass
else: raise AssertionError('raw socket bypassed managed proxy')
print(json.dumps({'public_route':True,'denied_requests':len(denied),
    'private_file_denied':True,'symlink_denied':True,'task_read_only':True,'raw_bypass_denied':True}))
"""
                result = subprocess.run(
                    [
                        "codex",
                        *flags(settings),
                        "sandbox",
                        "-P",
                        profile.permission_profile,
                        "-C",
                        str(work),
                        "--",
                        "/usr/bin/python3",
                        "-I",
                        "-c",
                        source,
                        str(task),
                        str(private),
                        str(server.server_port),
                    ],
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=40,
                )
                self.assertEqual(
                    result.returncode, 0, result.stdout + result.stderr + repr(observed)
                )
                result_json = json.loads(result.stdout)
                self.assertTrue(result_json["public_route"])
                self.assertEqual(result_json["denied_requests"], 6)
                self.assertEqual(
                    observed,
                    ["http://1.1.1.1/cfx-public-proxy-fixture"],
                    "denied destinations must not reach upstream",
                )
                self.assertEqual(
                    (work / "written.txt").read_text(), "native work write"
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_solo_public_route_and_isolation(self):
        self.check_profile(NativeSoloConfig())

    def test_team_public_route_and_isolation(self):
        self.check_profile(NativeTeamConfig())
