"""Science environment port. Reuse the trusted service and its exact semantics."""

import time
import re
import math
from copy import deepcopy

from kinetic_agents.core.state import EnvironmentState


class ScienceEnvironment:
    def __init__(
        self,
        backend,
        tools,
        trajectory,
        *,
        deadline,
        resource_details=None,
        clock=time.time,
        monotonic=time.monotonic,
        sleep=time.sleep,
        expected_research_identity=None
    ):
        self.backend, self._tools, self.trajectory = backend, deepcopy(tools), trajectory
        self.deadline, self.resource_details = deadline, resource_details
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep
        self.expected_research_identity = deepcopy(expected_research_identity)

    def tool_specs(self):
        return deepcopy(self._tools)

    def snapshot(self):
        raw = self.backend.rpc({"op": "state"})
        if (
            self.expected_research_identity is not None
            and raw.get("research_profile_identity") != self.expected_research_identity
        ):
            raise PermissionError("remote scientific profile/source identity mismatch")
        state = EnvironmentState.from_snapshot(raw)
        self.trajectory.record(
            "RR_ENV_SNAPSHOT",
            job_count=state.job_count,
            unresolved_jobs=state.unresolved_jobs,
            stop_recorded=state.stop_recorded,
            infrastructure_failed=state.infrastructure_failed,
            resource_stopped=state.resource_stopped,
            cpu_remaining_seconds=state.cpu_remaining_seconds,
            execution_stop_reason=state.execution_stop_reason,
        )
        return raw, state

    def activate(self):
        return self.backend.rpc({"op": "activate"})

    def execute(self, name, args):
        if name not in {spec["name"] for spec in self._tools}:
            raise PermissionError("tool outside environment capabilities")
        # Freeze the original request for every transport retry, not a new
        # scientific action. Backend enforces idempotency and scientific bounds.
        original = deepcopy(args)
        action_id = self.trajectory.start_tool(name, original)
        started = self.monotonic()
        try:
            for attempt in range(5):
                try:
                    result = self.backend.call(name, deepcopy(original))
                    break
                except ConnectionError:
                    self.trajectory.record(
                        "RR_TOOL_RECONNECT",
                        action_id=action_id,
                        tool_name=name,
                        remote_attempt=attempt + 1,
                    )
                    if attempt == 4 or self.clock() + 10 >= self.deadline:
                        raise
                    self.sleep(min(10, 2**attempt))
            if name == "resource_status" and self.resource_details is not None:
                result.update(self.resource_details())
            self.trajectory.finish_tool(action_id, name, result, self.monotonic() - started)
            if name in (
                "research_open",
                "research_read",
                "research_notes",
                "research_note",
                "research_candidates",
                "research_groups",
                "research_raw",
                "research_compare",
            ):
                metadata = {"action_id": action_id, "tool_name": name}
                for key in ("snapshot_id", "scope_id", "source_snapshot_id"):
                    value = result.get(key)
                    if isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value):
                        metadata[key] = value
                for key in (
                    "case_count",
                    "candidate_count",
                    "evidence_record_count",
                    "query_attempt_count",
                    "version",
                    "journal_sequence",
                    "total_rows",
                ):
                    if type(result.get(key)) is int:
                        metadata[key] = result[key]
                if name in (
                    "research_read",
                    "research_candidates",
                    "research_groups",
                    "research_raw",
                    "research_compare",
                ):
                    metadata["section"] = original.get("section", name.removeprefix("research_"))
                    metadata["result_count"] = len(result.get("rows", []))
                if name == "research_note":
                    metadata["operation"] = original.get("operation")
                    metadata["note_id"] = result.get("note_id")
                    self.trajectory.record("RR_NOTE_REVISION", **metadata)
                else:
                    self.trajectory.record("RR_KNOWLEDGE_OBSERVED", **metadata)
            elif name in ("research_advise", "research_advice"):
                metadata = {"action_id": action_id, "tool_name": name}
                for key in ("advice_id", "snapshot_id"):
                    value = result.get(key)
                    if isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value):
                        metadata[key] = value
                if result.get("status") in ("COMPLETE", "UNAVAILABLE"):
                    metadata["status"] = result["status"]
                if type(result.get("cached")) is bool:
                    metadata["cached"] = result["cached"]
                if (
                    type(result.get("target_case_count")) is int
                    and result["target_case_count"] >= 0
                ):
                    metadata["target_case_count"] = result["target_case_count"]
                cpu = result.get("new_fit_cpu_seconds")
                if type(cpu) in (int, float) and math.isfinite(cpu) and cpu >= 0:
                    metadata["new_fit_cpu_seconds"] = cpu
                self.trajectory.record("RR_ADVICE_OBSERVED", **metadata)
            return result
        except Exception as exc:
            self.trajectory.fail_tool(action_id, name, exc, self.monotonic() - started)
            raise
