"""Opt-in team profile. Existing controlled/open-world profiles stay unchanged."""

from dataclasses import dataclass

from kinetic_agents.native.public_profile import NativeOpenWorldConfig


@dataclass(frozen=True)
class _UpstreamProxyConfig(NativeOpenWorldConfig):
    # Host-selected route, not an agent-controlled sandbox escape. Keep the
    # managed proxy and its domain/private-destination checks enabled. The host
    # transport receives proxy/CA variables; the agent shell does not inherit
    # host credentials or configuration. Preserve the legacy open-world class.
    allow_upstream_proxy: bool = True

    def permission_settings(self, work, task):
        return {
            **super().permission_settings(work, task),
            f"permissions.{self.permission_profile}.network.allow_upstream_proxy": self.allow_upstream_proxy,
        }


@dataclass(frozen=True)
class NativeTeamConfig(_UpstreamProxyConfig):
    profile: str = "native-public-research-team-fork-v3"
    permission_profile: str = "native-public-research-team-proxy-v3"
    multi_agent: bool = True
    child_thread_limit: int = 3
    child_depth_limit: int = 1

    def codex_overrides(self):
        # Native max_threads EXCLUDES the principal. Depth one means that the
        # principal delegates; a researcher cannot accidentally form another PI.
        return {
            **super().codex_overrides(),
            "agents.max_threads": self.child_thread_limit,
            "agents.max_depth": self.child_depth_limit,
        }


@dataclass(frozen=True)
class NativeSoloConfig(_UpstreamProxyConfig):
    """Same public environment and team APIs; no native child-spawn capability."""

    profile: str = "native-public-research-solo-v3"
    permission_profile: str = "native-public-research-team-proxy-v3"
    multi_agent: bool = False
    child_thread_limit: int = 3
    child_depth_limit: int = 1

    def codex_overrides(self):
        # Freeze identical scalar options. The installed client's feature switch
        # removes child tools; TeamIdentity(max_members=1) also rejects registration.
        return {
            **super().codex_overrides(),
            "agents.max_threads": self.child_thread_limit,
            "agents.max_depth": self.child_depth_limit,
        }
