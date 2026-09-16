"""Extracted reusable implementation; historical launchers intentionally excluded."""

from dataclasses import dataclass
from pathlib import Path
from kinetic_agents.native.baseline import NativeBaselineConfig


@dataclass(frozen=True)
class NativeOpenWorldConfig(NativeBaselineConfig):
    profile: str = "native-public-research-v1"
    permission_profile: str = "native-public-research"
    network_access: bool = True

    def codex_overrides(self):
        # Public web through shell/network tools, not the provider-specific
        # hosted web_search Responses tool (unqualified on this backend).
        return {**super().codex_overrides(), "features.network_proxy": True}

    def permission_settings(self, work, task):
        from kinetic_agents.native.permissions import SHELL_ENV

        name = self.permission_profile
        return {
            "default_permissions": name,
            f"permissions.{name}.filesystem": {
                ":root": "deny",
                ":minimal": "read",
                ":tmpdir": "deny",
                ":slash_tmp": "deny",
                str(Path(work).resolve()): "write",
                str(Path(task).resolve()): "read",
            },
            f"permissions.{name}.network.enabled": True,
            f"permissions.{name}.network.allow_local_binding": False,
            f"permissions.{name}.network.allow_upstream_proxy": False,
            f"permissions.{name}.network.domains": {
                "*": "allow",
                "localhost": "deny",
                "127.0.0.1": "deny",
                "::1": "deny",
                "169.254.169.254": "deny",
            },
            "shell_environment_policy.inherit": "none",
            "shell_environment_policy.set": dict(SHELL_ENV),
        }
