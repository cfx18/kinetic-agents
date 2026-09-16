"""Native-session lifecycle, not an LLM planner.

All dependencies capable of model calls or science are injected. Test doubles
exercise this same loop without a model, network, credentials, or a solver.
Monitoring is not a dependency of execution. The host's original Store remains
the authority for budgets, recovery limits and terminal transitions.
"""

from kinetic_agents.core.inputs import task_directory
import hashlib
from pathlib import Path
import time

from kinetic_agents.core.contracts import PolicyContext
from kinetic_agents.core.contracts import RuntimeIdentity
from kinetic_agents.core.contracts import fingerprint
from kinetic_agents.core.contracts import policy_identity
from kinetic_agents.core.policy import NativeBaselinePolicy

TERMINAL = {"COMPLETED", "BUDGET_STOPPED", "FAILED_REVIEW", "CANCELLED"}


def verify_task(root, manifest):
    """Read only the declared flat, readonly public capsule; no path escapes."""
    if not isinstance(manifest, dict) or "TASK.md" not in manifest:
        raise ValueError("task manifest must identify TASK.md")
    task = task_directory(root)
    if task.is_symlink() or not task.is_dir():
        raise PermissionError("invalid public task directory")
    if {p.name for p in task.iterdir()} != set(manifest):
        raise PermissionError("unexpected file in shared public task directory")
    for name, expected in manifest.items():
        if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name:
            raise PermissionError("task manifest path outside capsule")
        path = task / name
        if path.is_symlink() or not path.is_file():
            raise PermissionError("task artifact is not an owned regular file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise PermissionError("task artifact hash mismatch")


def run_native(
    root,
    store,
    contract,
    *,
    environment,
    harness_factory,
    gateway_factory,
    finalize,
    save,
    task_manifest,
    adapter_identity,
    policy=None,
    monotonic=time.monotonic,
    sleep=time.sleep
):
    root = Path(root)
    if store.get("status") in TERMINAL | {"FINALIZING"}:
        return finalize(root, store)
    verify_task(root, task_manifest)
    if contract.get("task_contract") is not None:
        from kinetic_agents.core.task_contract import read_original

        read_original(task_directory(root) / "TASK.md", contract["task_contract"])
    policy = NativeBaselinePolicy() if policy is None else policy
    trace = environment.trajectory
    available = environment.tool_specs()
    identity = RuntimeIdentity(
        "research-runtime.v1",
        adapter_identity["harness"],
        contract["model"],
        contract["effort"],
        fingerprint(contract),
        fingerprint(task_manifest),
        fingerprint(available),
        fingerprint(policy_identity(policy)),
        fingerprint(adapter_identity),
    )
    # Bind before model or science access. Resume with a different strategy or
    # adapter must not masquerade as the same experimental trajectory.
    try:
        trace.bind(identity)
    except PermissionError as exc:
        store.update(
            "RR_IDENTITY_REJECTED",
            error_type=type(exc).__name__,
            science_reconciliation_required=bool(store.get("thread_id")),
        )
        store.advance("fatal")
        raise
    task_text = (task_directory(root) / "TASK.md").read_bytes().decode("utf-8")
    dynamic = [
        {
            "type": "function",
            "name": spec["name"],
            "description": spec["description"],
            "inputSchema": spec["parameters"],
        }
        for spec in available
    ]

    def inspect_environment():
        raw, state = environment.snapshot()
        save("remote_status.json", raw)
        if state.stop_recorded:
            save("stop.json", raw["stop"])
            store.advance("stop_validated")
            return False
        if state.infrastructure_failed:
            store.update("SOLVER_INFRASTRUCTURE_FAILURE", solver_health=raw["solver_health"])
            store.advance("fatal")
            return False
        if state.unresolved_jobs:
            store.update("SCIENCE_UNCERTAIN", science_reconciliation_required=True)
            store.advance("fatal")
            return False
        if state.must_stop_for_budget:
            store.update(
                "EXECUTION_LIMIT_REACHED",
                stop_reason=state.execution_stop_reason
                or ("remote_resource_stopped" if state.resource_stopped else "cpu_admission_limit"),
            )
            store.advance("budget_exhausted")
            return False
        return True

    while store.advance("tick") not in TERMINAL:
        if store.get("status") == "RETRY_WAIT":
            sleep(0.5)
            continue
        client = gateway = None
        try:
            if not inspect_environment():
                break
            environment.activate()
            gateway = gateway_factory()
            url = gateway.start()
            client = harness_factory(url, gateway.token, dynamic)
            if hasattr(client, "dispatch_observer"):
                client.dispatch_observer = lambda name, call_id, timing: trace.record(
                    "RR_TOOL_RECEIVED",
                    tool_name=name,
                    call_id_sha256=fingerprint(call_id),
                    **timing
                )

            def call(name, args):
                # Strict checks belong to effect boundaries, not streamed deltas.
                if store.advance("tick") in TERMINAL | {"FINALIZING"}:
                    raise PermissionError("run is terminal; no tool dispatch")
                result = environment.execute(name, args)
                if name == "stop_search":
                    save("stop.json", result)
                return result

            client.tools = {
                spec["name"]: (lambda args, name=spec["name"]: call(name, args))
                for spec in available
            }
            startup = client.start_or_resume(store.get("thread_id"))
            if store.get("thread_id") and startup["thread_id"] != store.get("thread_id"):
                raise PermissionError("resumed harness changed thread identity")
            store.update("THREAD_BOUND", thread_id=startup["thread_id"], startup=startup)
            trace.record(
                "RR_SESSION_BOUND",
                thread_id=startup["thread_id"],
                resumed=startup["resumed"],
                model=contract["model"],
                effort=contract["effort"],
            )
            store.advance("ready")
            context = PolicyContext(
                task_text,
                str(task_directory(root)),
                startup["resumed"],
                store.get("continuation_notice", ""),
            )

            def begin(prompt):
                trace.record(
                    "RR_TURN_REQUESTED",
                    thread_id=client.thread_id,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    prompt_sha256=fingerprint(prompt),
                )
                # A native client can deliver notifications while turn/start is
                # still pending. Never attribute them to the previous turn.
                trace.bind_turn(client.thread_id, None, policy.policy_id, policy.version)
                client.begin(prompt)
                trace.bind_turn(client.thread_id, client.turn_id, policy.policy_id, policy.version)
                trace.record(
                    "RR_TURN_STARTED",
                    thread_id=client.thread_id,
                    turn_id=client.turn_id,
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                )

            begin(policy.start(context))
            next_remote_check = 0.0
            next_heartbeat = 0.0
            while True:
                now = monotonic()
                if now >= next_heartbeat:
                    if store.advance("tick") in TERMINAL:
                        break
                    # Only the timed heartbeat is persisted, never each delta.
                    trace.record("RR_HEARTBEAT", interval_seconds=5.0)
                    next_heartbeat = monotonic() + 5.0
                completed = client.poll()
                if completed:
                    trace.record(
                        "RR_TURN_FINISHED",
                        thread_id=client.thread_id,
                        turn_id=client.turn_id,
                        status=completed.get("status"),
                    )
                if (root / "stop.json").exists():
                    store.advance("stop_validated")
                    break
                # A protocol-specific submit can occur during the last poll.
                # Inspect before starting another paid turn, not 30s later.
                if completed or monotonic() >= next_remote_check:
                    if not inspect_environment():
                        break
                    next_remote_check = monotonic() + 30
                if completed:
                    if completed.get("status") != "completed":
                        raise ConnectionError("native turn interrupted")
                    store.advance("reply_completed")
                    begin(policy.continuation())
            if store.get("status") == "FINALIZING":
                break
        except (ConnectionError, TimeoutError, EOFError) as exc:
            trace.record("RR_RECOVERY_REQUIRED", error_type=type(exc).__name__)
            store.advance("recoverable_fault")
        except Exception as exc:
            safe_codes = {
                "native_team_capacity_mismatch",
                "native_child_foreign_parent",
                "native_spawn_foreign_sender",
                "native_child_model_mismatch",
                "native_team_protocol_violation",
            }
            code = getattr(exc, "diagnostic_code", None)
            store.update(
                "PRODUCTION_FAULT",
                error_type=type(exc).__name__,
                **({"diagnostic_code": code} if code in safe_codes else {})
            )
            store.advance("fatal")
        finally:
            if client:
                client.close()
            if gateway:
                gateway.close()
    return finalize(root, store)
