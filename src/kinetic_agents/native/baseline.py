"""Explicit settings for the existing controlled native Codex baseline.

This is configuration, not a new agent loop or an isolation implementation.
The adapter still delegates native execution to Codex and permission enforcement
to ``kinetic_agents.native.permissions``. Only the already-used controlled profile
is accepted. Enabling web access, delegation, memory extraction, or different
permissions needs a new profile plus isolation/accounting acceptance; it cannot
be achieved by silently overriding this object's settings.

``compaction='native_default'`` means no custom thresholds or summarization
requests are configured. It does not assert that the installed client/provider
has been qualified for every long-context path. Bind the installed adapter/CLI
identity separately before comparing scientific outcomes.
"""

from collections.abc import Mapping
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import fields
import hashlib
import json


@dataclass(frozen=True)
class NativeBaselineConfig:
    """Fail-closed, credential-free and serializable baseline configuration."""

    profile: str = "native-controlled-v1"
    web_search: str = "disabled"
    multi_agent: bool = False
    memories: bool = False
    shell_tool: bool = True
    code_mode: bool = False
    project_doc_max_bytes: int = 0
    permission_profile: str = "native-research"
    network_access: bool = False
    shell_environment_inherit: str = "none"
    approval_policy: str = "never"
    compaction: str = "native_default"
    provider_fallback: bool = False
    inherit_global_auth: bool = False

    def __post_init__(self):
        # Strict types prevent 0/1 from masquerading as validated booleans.
        # A named experimental profile must be implemented and qualified, not
        # simply supplied through a free-form Codex configuration dictionary.
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not type(field.default) or value != field.default:
                raise ValueError(
                    "Unsupported or unqualified native baseline setting: " + field.name
                )

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, Mapping):
            raise TypeError("Native baseline configuration must be a mapping.")
        expected = {field.name for field in fields(cls)}
        if any(key not in expected for key in value):
            # Do not echo arbitrary keys: they may contain accidentally pasted
            # credentials, paths, or an entire model request.
            raise ValueError("Unknown native baseline configuration field.")
        return cls(**dict(value))

    @classmethod
    def from_dict(cls, value):
        """Serialization alias with the same strict validation as from_mapping."""
        return cls.from_mapping(value)

    def snapshot(self):
        """Return fresh JSON-compatible public settings, without runtime paths."""
        return asdict(self)

    def identity(self):
        """Configuration identity only, not a paid/native capability certificate."""
        settings = self.snapshot()
        digest = hashlib.sha256(
            json.dumps(settings, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        return {
            "schema": "native-baseline-settings.v1",
            "settings": settings,
            "settings_sha256": digest,
            "qualification": "configuration_only_not_runtime_attestation",
        }

    def codex_overrides(self):
        """Exactly the previous native feature overrides; no compaction override."""
        return {
            "web_search": self.web_search,
            "project_doc_max_bytes": self.project_doc_max_bytes,
            "features.memories": self.memories,
            "features.multi_agent": self.multi_agent,
            "features.shell_tool": self.shell_tool,
            "features.code_mode": self.code_mode,
        }

    def permission_settings(self, work, task):
        """Reuse existing OS/native profile rather than inventing a new sandbox."""
        from kinetic_agents.native.permissions import NAME
        from kinetic_agents.native.permissions import SHELL_ENV
        from kinetic_agents.native.permissions import config

        if self.permission_profile != NAME:
            raise PermissionError("Native permission profile does not match the baseline.")
        settings = config(work, task)
        if (
            settings.get("default_permissions") != self.permission_profile
            or settings.get("shell_environment_policy.inherit") != self.shell_environment_inherit
            or settings.get("shell_environment_policy.set") != SHELL_ENV
            or settings.get("permissions." + NAME + ".network.enabled") is not self.network_access
        ):
            raise PermissionError("Native permission implementation differs from the baseline.")
        return settings


def resolve_baseline(value=None):
    """Resolve optional host configuration without reading files or environment."""
    if value is None:
        return NativeBaselineConfig()
    from kinetic_agents.native.public_profile import NativeOpenWorldConfig
    from kinetic_agents.native.profiles import NativeTeamConfig
    from kinetic_agents.native.profiles import NativeSoloConfig

    if type(value) is NativeSoloConfig:
        return NativeSoloConfig.from_mapping(value.snapshot())
    if type(value) is NativeTeamConfig:
        return NativeTeamConfig.from_mapping(value.snapshot())
    if type(value) is NativeOpenWorldConfig:
        # Explicit host-selected type; the existing controlled mapping/profile
        # remains fail-closed. Do not widen prior ResearchProfile contracts.
        return NativeOpenWorldConfig.from_mapping(value.snapshot())
    if type(value) is NativeBaselineConfig:
        # Revalidate even if trusted host code modified a frozen object directly.
        return NativeBaselineConfig.from_mapping(value.snapshot())
    return NativeBaselineConfig.from_mapping(value)
