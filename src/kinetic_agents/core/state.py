"""Pure projections of authoritative state. A running process is not progress."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class EnvironmentState:
    stop_recorded: bool
    infrastructure_failed: bool
    unresolved_jobs: int
    resource_stopped: bool
    cpu_remaining_seconds: float
    job_count: int
    execution_stop_reason: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot: Mapping):
        # Required fields fail closed; never turn missing service state into zero
        # pending work or a successful scientific result.
        jobs = snapshot["jobs"]
        resources = snapshot["resources"]
        if not isinstance(jobs, list) or not isinstance(resources, dict):
            raise ValueError("malformed science snapshot")
        import math

        remaining = resources["cpu_remaining_seconds"]
        if (
            isinstance(remaining, bool)
            or not isinstance(remaining, (float, int))
            or not math.isfinite(remaining)
        ):
            raise ValueError("finite CPU balance required")
        return cls(
            bool(snapshot["stop"]),
            bool(snapshot.get("solver_health", {}).get("fatal_infrastructure_error")),
            sum(job["status"] == "FAILED_UNCERTAIN" for job in jobs),
            bool(snapshot["resource_stopped"]),
            remaining,
            len(jobs),
            resources.get("execution_stop_reason"),
        )

    @property
    def must_stop_for_budget(self):
        # Preserve the existing service's admission reserve, not a new policy.
        return (
            self.resource_stopped
            or self.cpu_remaining_seconds <= 60
            or self.execution_stop_reason == "slurm_allocation_ending"
        )
