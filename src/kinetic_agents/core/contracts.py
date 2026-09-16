"""Small explicit contracts shared by policies, environments and trajectories."""

from dataclasses import asdict
from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Protocol


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def policy_identity(policy) -> dict:
    """Bind implementation as well as declared configuration.

    All behavior-affecting configuration must be declared in identity(). Source
    locks catch an edited implementation or substituted class without a version
    bump, but do not sandbox trusted Python or detect every hidden dependency.
    """
    cls = type(policy)
    source = inspect.getsourcefile(cls)
    if source is None:
        raise ValueError("policy implementation must have inspectable Python source")
    return {
        "configuration": policy.identity(),
        "class": cls.__module__ + "." + cls.__qualname__,
        "implementation_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
    }


class NativeHarness(Protocol):
    """Adapt the real native session; never substitute a synthetic planner."""

    tools: dict[str, Callable]
    thread_id: str | None
    turn_id: str | None

    def start_or_resume(self, owned_thread: str | None = None) -> dict: ...
    def begin(self, text: str) -> None: ...
    def poll(self) -> dict | None: ...
    def close(self) -> None: ...


class ScienceBackend(Protocol):
    def rpc(self, request: dict) -> dict: ...
    def call(self, name: str, args: dict) -> dict: ...


class EventSink(Protocol):
    def record(self, kind: str, **payload: Any) -> None: ...


@dataclass(frozen=True)
class PolicyContext:
    """Public task only. No credentials, test labels or host-private paths."""

    task_text: str
    task_directory: str
    resumed: bool = False
    continuation_notice: str = ""


class ResearchPolicy(Protocol):
    """Policy extension point; the native harness still chooses science actions.

    Future evidence-aware policies can compose instructions from authorized
    evidence. Changing the host policy implementation creates a new experiment
    identity, not an unlogged mid-run patch. Registered in-run skill evolution
    should be explicit versioned state, not a silent host implementation change.
    The baseline passes the existing instructions intact.
    """

    policy_id: str
    version: str

    def start(self, context: PolicyContext) -> str: ...
    def continuation(self) -> str: ...
    def identity(self) -> dict: ...


@dataclass(frozen=True)
class RuntimeIdentity:
    schema: str
    harness: str
    model: str
    effort: str
    contract_sha256: str
    task_manifest_sha256: str
    tools_sha256: str
    policy_sha256: str
    adapter_sha256: str

    def __post_init__(self):
        for key, value in asdict(self).items():
            if not isinstance(value, str) or not value:
                raise ValueError(f"nonempty runtime identity required: {key}")
            if key.endswith("_sha256") and (
                len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError(f"invalid identity digest: {key}")

    def as_dict(self):
        return asdict(self)
