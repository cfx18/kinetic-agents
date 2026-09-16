"""Opt-in real subscription smoke test; synthetic input, no science or scheduler.

Uses production SubscriptionTeamClient/WorkerPool/TeamStore/LiveTranscript.
Five native turns maximum: solo, team dispatch, two workers, resumed principal.
Native turns are NOT provider-request counts. Output is private, never a benchmark.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kinetic_agents.config import load_config
from kinetic_agents.core.storage import atomic
from kinetic_agents.native.subscription import (
    SubscriptionAccount,
    SubscriptionTeamClient,
    plain,
)
from kinetic_agents.observability.transcript import clean
from kinetic_agents.research.environment import ScientificTeamService
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore


TASK = (
    "Synthetic interface acceptance only: compute sums of the provided integers. "
    "No chemistry, internet search, remote jobs, dependency installation, or real experiments. "
    "Use only this input directory, current work directory, and registered team tools. "
    "Never read account files, host state, other runs, or credentials."
)
NUMBERS = [3, 5, 8, 13]
EXPECTED = {"solo": 29, "sum_check": 29, "squares_check": 267}
PROBE = dict(
    type="function",
    name="acceptance_probe",
    description="Synthetic tool roundtrip. Validates your numeric result and local JSON artifact; returns a receipt to cite.",
    inputSchema=dict(
        type="object",
        additionalProperties=False,
        properties={"label": {"type": "string"}, "value": {"type": "integer"}},
        required=["label", "value"],
    ),
)


def public_reply(client):
    return "\n".join(
        item.get("text", "")
        for item in (client.completed or {}).get("items", [])
        if item.get("type") == "agentMessage"
    )


def await_turn(client, deadline):
    while time.monotonic() < deadline:
        if client.poll():
            if client.completed.get("status") != "completed":
                raise RuntimeError(
                    "native turn failed; inspect public transcript and typed error receipt"
                )
            return public_reply(client)
    raise TimeoutError("synthetic acceptance deadline reached; no automatic retry")


def json_file(path):
    path = plain(path)
    if not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("missing or oversized synthetic result")
    return json.loads(path.read_text())


def actor_task(label, task):
    artifact = "SOLO.json" if label == "solo" else f"agents/{label}/RESULT.json"
    operation = "sum of squares" if label == "squares_check" else "sum"
    return (
        f"Read {task}/INPUT.json with your native shell/code tool. Compute the {operation} "
        f"of its numbers using Python, and write {artifact} as JSON with exactly label={label!r} "
        "and numeric value. Invoke acceptance_probe with those same label and value. "
        "Include the returned receipt verbatim in your final public reply. "
        "This is a tiny interface check, not a research problem. Do not spawn others or browse. "
        + (
            "End this turn after the receipt."
            if label == "solo"
            else "Claim your assigned team task, then submit it for review using its latest version; "
            "include your artifact path and receipt in the submission summary, references=[]. End this turn."
        )
    )


def run_profile(root, profile, account, deadline):
    cfg = load_config(PROJECT / "configs" / f"{profile}.yaml")[1]
    if cfg["harness"]["name"] != "codex" or cfg["backend"]["auth"] != "subscription":
        raise ValueError(
            "this acceptance only qualifies native Codex subscription profiles"
        )
    mixed = profile == "team"
    folder = root / profile
    task, work = folder / "input", folder / "work"
    task.mkdir(parents=True)
    work.mkdir()
    (task / "TASK.md").write_text(TASK)
    atomic(task / "INPUT.json", {"numbers": NUMBERS})
    model = cfg["model"]
    identity = TeamIdentity(
        "accept-" + profile + "-" + uuid.uuid4().hex[:12],
        hashlib.sha256(TASK.encode()).hexdigest(),
        model["name"],
        model["reasoning_effort"],
        model["max_agents"],
        researcher_model=model.get("researcher_model"),
        researcher_effort=model.get("researcher_effort"),
    )
    service = ScientificTeamService(TeamStore(folder / "team", identity))
    receipts = {}
    launched = set()

    def probe(actor, args, request_id):
        label, value = args["label"], args["value"]
        permitted = {"sum_check", "squares_check"} if mixed else {"solo"}
        if label not in permitted or type(value) is not int or value != EXPECTED[label]:
            raise ValueError("incorrect synthetic value or label")
        artifact = work / (f"agents/{label}/RESULT.json" if mixed else "SOLO.json")
        if json_file(artifact) != {"label": label, "value": value}:
            raise ValueError("native-written JSON differs from tool arguments")
        row = dict(
            label=label,
            value=value,
            actor_id=actor,
            receipt="receipt-" + uuid.uuid4().hex[:16],
            request_id=request_id,
        )
        receipts[label] = row
        atomic(folder / "receipts" / f"{label}.json", row)
        return row

    def make_client():
        native = SubscriptionTeamClient(
            work,
            task,
            folder / "native",
            model["name"],
            model["reasoning_effort"],
            account,
            None,
            service,
            extra_tools=[PROBE],
            actor_tools={"acceptance_probe": probe},
        )
        if native.worker_pool:
            spawn = native.science_tools.handlers["research_spawn"]

            def bounded_spawn(actor, args, request_id):
                if (
                    args.get("name") not in {"sum_check", "squares_check"}
                    or args["name"] in launched
                ):
                    raise PermissionError(
                        "synthetic acceptance allows each of the two named workers only once"
                    )
                row = spawn(actor, args, request_id)
                launched.add(args["name"])
                return row

            def no_followup(actor, args, request_id):
                raise PermissionError(
                    "synthetic acceptance permits no extra worker turns"
                )

            native.science_tools.handlers["research_spawn"] = bounded_spawn
            native.science_tools.handlers["research_followup"] = no_followup
        return native

    client = None
    turns = []
    begun = time.monotonic()
    try:
        client = make_client()
        started = client.start_or_resume()
        atomic(
            folder / "identity.json",
            {"config": cfg, "native": started, "synthetic": True},
        )
        if not mixed:
            if client.worker_pool is not None or any(
                s["name"] == "research_spawn" for s in client.specs
            ):
                raise AssertionError("solo unexpectedly exposes child creation")
            client.begin(TASK + "\n" + actor_task("solo", task))
            reply = await_turn(client, deadline)
            turns.append(dict(phase="solo", status="completed", reply=reply))
            if receipts.get("solo", {}).get("receipt", "missing-probe") not in reply:
                raise AssertionError(
                    "solo final reply does not cite the actual tool return"
                )
        else:
            assignments = [
                {"name": label, "message": actor_task(label, task)}
                for label in ("sum_check", "squares_check")
            ]
            client.begin(
                TASK
                + "\nUse research_spawn exactly twice with these arguments: "
                + json.dumps(assignments)
                + ". Then reply TEAM_DISPATCHED and END this turn immediately. "
                "Do not poll or wait. The host will await the workers and resume this same thread for review."
            )
            turns.append(
                dict(
                    phase="dispatch",
                    status="completed",
                    reply=await_turn(client, deadline),
                )
            )
            while client.worker_pool.running and time.monotonic() < deadline:
                time.sleep(0.1)
            if client.worker_pool.running:
                raise TimeoutError(
                    "workers did not complete within synthetic acceptance window"
                )
            workers = client.worker_pool.status(None, {}, None)["workers"]
            if len(workers) != 2 or any(w["status"] != "IDLE" for w in workers):
                raise AssertionError(
                    "expected exactly two successfully completed native workers"
                )
            if set(receipts) != {"sum_check", "squares_check"}:
                raise AssertionError(
                    "workers did not both roundtrip through the shared tool"
                )
            if len({row["actor_id"] for row in receipts.values()}) != 2:
                raise AssertionError(
                    "worker results not from two independent native actors"
                )
            for worker in workers:
                if receipts[worker["name"]]["receipt"] not in worker["reply"]:
                    raise AssertionError(
                        "worker public reply is missing its tool-return receipt"
                    )
            old_thread = client.thread_id
            client.close()
            client = make_client()
            resumed = client.start_or_resume(old_thread)
            if not resumed["resumed"] or resumed["thread_id"] != old_thread:
                raise AssertionError("principal did not resume its original thread")
            client.begin(
                "Your two synthetic workers have finished. Call research_workers to read their public "
                "replies; use team_list(kind='task') and team_review to accept both correct delivered tasks "
                "using their latest versions. Read their RESULT.json files. Write TEAM_ACCEPTANCE.json "
                "in cwd with keys sum, sum_of_squares, receipts (a list of both returned receipt strings). "
                "Reply TEAM_ACCEPTED and end this turn. Do not spawn/followup any worker or start science."
            )
            turns.append(
                dict(
                    phase="resumed_review",
                    status="completed",
                    reply=await_turn(client, deadline),
                )
            )
            result = json_file(work / "TEAM_ACCEPTANCE.json")
            if result != {
                "sum": 29,
                "sum_of_squares": 267,
                "receipts": result.get("receipts"),
            } or set(result.get("receipts", [])) != {
                r["receipt"] for r in receipts.values()
            }:
                raise AssertionError(
                    "resumed principal did not correctly collect worker evidence"
                )
            tasks = service.list(client.thread_id, kind="task")["rows"]
            if len(tasks) != 2 or any(t["status"] != "accepted" for t in tasks):
                raise AssertionError(
                    "principal review did not accept both delivered tasks"
                )
        atomic(folder / "public-turns.json", turns)
        if any((folder / "native").rglob("auth.json")):
            raise AssertionError(
                "managed credentials copied into native research state"
            )
    finally:
        if client is not None:
            client.close()
    events = [
        json.loads(line)
        for line in (folder / "transcript.jsonl").read_text().splitlines()
    ]
    counts = dict(Counter(e["kind"] for e in events))
    for kind in ("model_input", "tool_request", "tool_return", "actor_registered"):
        if not counts.get(kind):
            raise AssertionError("missing live transcript category: " + kind)
    if not (folder / "transcript.md").stat().st_size:
        raise AssertionError("human-readable live transcript missing")
    usage = json_file(folder / "native/subscription_team.json")
    result = dict(
        status="PASS",
        profile=profile,
        model=model,
        native_principal_turns=len(turns),
        wall_seconds=round(time.monotonic() - begun, 3),
        transcript_counts=counts,
        subscription_usage=usage,
        api_dollars=None,
        scientist_solves=0,
        ssh_commands=0,
        limitation="Synthetic native adapter acceptance, not full scientific pipeline/evaluator qualification.",
    )
    atomic(folder / "acceptance.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-real-models", action="store_true", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if not 60 <= args.timeout_seconds <= 900:
        parser.error("synthetic acceptance timeout must be 60..900 seconds")
    cfg = load_config(PROJECT / "configs/solo.yaml")[1]
    team = load_config(PROJECT / "configs/team.yaml")[1]
    if cfg["account"] != team["account"]:
        parser.error("solo/team account directories differ")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = (
        PROJECT
        / "local/acceptance"
        / ("subscription-" + stamp + "-" + uuid.uuid4().hex[:6])
    )
    root.mkdir(parents=True, mode=0o700)
    account = SubscriptionAccount(cfg["account"]["auth_home"], root / "auth-host")
    results = dict(
        status="RUNNING",
        output=str(root),
        profiles=[],
        real_models=True,
        scientific_runs_started=False,
        max_native_turns=5,
        provider_request_count="not a turn count",
    )
    atomic(root / "acceptance.json", results)
    print(json.dumps({"output": str(root), "status": "RUNNING"}), flush=True)
    deadline = time.monotonic() + args.timeout_seconds
    try:
        account.start()
        for profile in ("solo", "team"):
            result = run_profile(root, profile, account, deadline)
            results["profiles"].append(result)
            atomic(root / "acceptance.json", results)
            print(
                json.dumps({"profile": profile, "status": result["status"]}), flush=True
            )
        results["status"] = "PASS"
    except (Exception, KeyboardInterrupt) as exc:
        results.update(
            status="FAIL", error_type=type(exc).__name__, error=clean(str(exc))[:500]
        )
    finally:
        account.close()
        atomic(root / "acceptance.json", results)
    print(
        json.dumps(
            {k: v for k, v in results.items() if k != "profiles"}, ensure_ascii=False
        ),
        flush=True,
    )
    return 0 if results["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
