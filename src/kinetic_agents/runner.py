"""Standalone configurable native-harness launcher, with no chat observer.

prepare: local files only, no budget clock, model call or SSH.
submit/start: explicit new resource account; detach a non-LLM supervisor.
status/logs: local observations only. No model calls or state transitions.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from kinetic_agents.execution.cpu import run as cpu_run
from kinetic_agents.core.store import Store
from kinetic_agents.core.storage import atomic
from kinetic_agents.core.contracts import fingerprint
from kinetic_agents.release import build
from kinetic_agents.release import verify
from kinetic_agents.core.runtime import verify_task
from kinetic_agents.core.runtime import TERMINAL
from kinetic_agents.native.subscription import MODEL
from kinetic_agents.native.subscription import EFFORT
from kinetic_agents.native.subscription import SubscriptionAccount
from kinetic_agents.native.subscription import SubscriptionTeamClient
from kinetic_agents.native.subscription import plain
from kinetic_agents.native.subscription import HOST_NETWORK_ENV
from kinetic_agents.team.contracts import TeamIdentity
from kinetic_agents.team.store import TeamStore
from kinetic_agents.research.environment import PairEnvironment
from kinetic_agents.research.environment import ScientificTeamService
from kinetic_agents.execution.finalize import finalize as pair_finalize
from kinetic_agents.execution.finalize import postrun_review
from kinetic_agents.execution.jobs import RemoteJobs
from kinetic_agents.execution.jobs import LOCAL_OWNER_CEILING
from kinetic_agents.research.policy import run_team
from kinetic_agents.native.profiles import NativeSoloConfig
from kinetic_agents.native.profiles import NativeTeamConfig
from kinetic_agents.core.inputs import task_directory

REPO = Path(__file__).resolve().parents[1]
TASK_SHA = "85715d6567a53b9a67773487163013b079d9ccb2e530a258bda9018e861f0836"
APPROVED_TASKS = {
    TASK_SHA: "usc_ii_v1",
    "dee27db8a8c1332d8e704fc55697eb52eee8d88acac4bc9c4ca9acd33284a2a5": "usc_ii_expert_v2",
}
PARENT_SHA = "eefd846bf67253ecf8a35c99bf2387b9a38b3f69a94640c013d99b0f16277aee"
CLI = "codex-cli 0.153.4"
MODULE = "kinetic_agents.runner"


def sha(path):
    return hashlib.sha256(plain(path).read_bytes()).hexdigest()


def cli_version():
    row = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=10)
    if row.returncode or row.stdout.strip() != CLI:
        raise PermissionError(
            "Codex CLI differs from qualified 0.153.4; run interface acceptance first"
        )
    return row.stdout.strip()


def read_task(directory):
    blob = plain(Path(directory) / "TASK.md").read_bytes()
    if hashlib.sha256(blob).hexdigest() not in APPROVED_TASKS:
        raise PermissionError("task changed; do not silently rewrite the experiment")
    return blob


def resource_contract(cpu_hours, wall_hours, evaluation_hours):
    for value in (cpu_hours, wall_hours, evaluation_hours):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("finite explicit resource limits required")
    # Reuse the already qualified 48 local core-hour reservation / 2 local cores.
    if not 64 <= cpu_hours <= 512 or not 1 <= wall_hours <= 24 or evaluation_hours != 64:
        raise ValueError(
            "supported limits: search 64..512 core-hours, wall 1..24 hours, separate evaluator 64 core-hours"
        )
    return dict(
        cpu_seconds=cpu_hours * 3600,
        wall_seconds=wall_hours * 3600,
        evaluation_cpu_seconds=evaluation_hours * 3600,
        api_usd=0.0,
        billing="chatgpt_subscription_not_metered_API",
        api_fallback=False,
        model_call_count_limit=None,
        subscription_quota_shared_with_user=True,
    )


def prepare(
    output,
    *,
    cpu_hours,
    wall_hours,
    evaluation_hours,
    auth_home,
    task,
    accepted,
    evaluation_base,
    deployment,
    config_snapshot=None,
    researcher_model=None,
    researcher_effort=None,
    execution=None,
    network=None,
):
    output, auth_home = plain(output), plain(auth_home)
    if output.exists():
        raise FileExistsError("new run directory required; old accounts are never reset")
    resources = resource_contract(cpu_hours, wall_hours, evaluation_hours)
    from kinetic_agents.execution.deployment import validate_deployment

    deployment = validate_deployment(deployment)
    mixed = researcher_model is not None
    if execution is None and (
        mixed
        and (researcher_model != "gpt-5.5" or researcher_effort not in ("medium", "high"))
        or not mixed
        and researcher_effort is not None
    ):
        raise ValueError("explicit supported researcher model/effort pair required")
    model, effort = MODEL, EFFORT
    if execution is not None:
        from kinetic_agents.harnesses.catalog import validate, inspect
        from kinetic_agents.connections import bind_backend, validate_backend

        validate(execution["harness"], execution["model"])
        validate_backend(execution["backend"], execution["harness"]["name"])
        harness_binding = inspect(execution["harness"])
        backend_binding = bind_backend(execution["backend"])
        model, effort = (
            execution["model"]["name"],
            execution["model"]["reasoning_effort"],
        )
        if model.startswith("replace-with-") or (researcher_model or "").startswith(
            "replace-with-"
        ):
            raise ValueError(
                "replace template model IDs with your provider's exact model IDs before prepare"
            )
        if (researcher_model, researcher_effort) != (
            execution["model"].get("researcher_model"),
            execution["model"].get("researcher_effort"),
        ):
            raise ValueError("execution and researcher model identities disagree")
    arm, members = ("team-max", 3) if mixed else ("solo-max", 1)
    if auth_home.is_relative_to(output):
        raise PermissionError("host subscription credentials must remain outside the run")
    task_root = plain(task)
    if (
        output.is_relative_to(task_root)
        or task_root.is_relative_to(output)
        or auth_home.is_relative_to(task_root)
        or task_root.is_relative_to(auth_home)
    ):
        raise PermissionError("canonical input, output and credentials must be disjoint")
    if {p.name for p in task_root.iterdir()} != {"TASK.md", "parent.yaml"}:
        raise PermissionError("public task directory may contain ONLY TASK.md and parent.yaml")
    parent = plain(task_root / "parent.yaml")
    accepted = plain(accepted)
    base = plain(evaluation_base)
    task_sha = hashlib.sha256(read_task(task_root)).hexdigest()
    if sha(parent) != PARENT_SHA:
        raise PermissionError("approved clean USC-II parent hash mismatch")
    qualified = json.loads(accepted.read_text())
    if qualified.get("status") != "PASS" or not re.fullmatch(
        "[a-f0-9]{64}", qualified.get("image_sha256", "")
    ):
        raise PermissionError("qualified remote container receipt required")
    from kinetic_agents.evaluation.scoring import validate_pool
    from kinetic_agents.evaluation.pins import source_pins
    import cantera as ct

    evaluation = json.loads(base.read_text())
    validate_pool(evaluation)
    if evaluation["solver_version"] != ct.__version__ or evaluation["source_pins"] != source_pins():
        raise PermissionError("existing evaluation solver/source definitions changed")
    version = cli_version()
    ident = uuid.uuid4().hex[:24]
    contract = {
        **resources,
        "deployment": deployment,
        "mode": "astra-subscription-team-v1" if mixed else "astra-subscription-solo-v1",
        "arm": arm,
        "model": model,
        "effort": effort,
        "max_members": members,
        "task_sha256": task_sha,
        "parent_sha256": PARENT_SHA,
        "run_id": "cfx_astra_" + ident,
        "evaluation_id": "cfx_astra_" + ident + "_endpoint",
        "subscription_native_overrides": {
            "agents.enabled": False,
            "features.multi_agent_v2": False,
            "effective_tool_transport": "native_Astra_code_mode",
        },
        "native_profile": (NativeTeamConfig() if mixed else NativeSoloConfig()).identity(),
        "auth_home": str(auth_home),
        "scientific_start": {
            "provided_case_pool": False,
            "provided_scientific_toolchain": False,
            "old_candidates_or_memory": False,
            "public_web": True,
            "case_and_tool_selection": "agent_selected",
        },
        "feedback_to_search": False,
        "evaluation_exposure": "historically_exposed_not_pristine_blind",
    }
    if network is not None:
        from kinetic_agents.network import validate_network

        validate_network(network)
        network_file = plain(network["env_file"])
        if network_file.is_relative_to(output) or network_file.is_relative_to(task_root):
            raise PermissionError("network.env_file must be host-only, outside task and run")
        contract["network"] = {**network, "env_file": str(network_file)}
    if execution is not None:
        contract.update(
            harness=harness_binding,
            backend=backend_binding,
            context_tokens=execution["model"].get("context_tokens"),
            mode="native-harness-team-v2" if mixed else "native-harness-solo-v2",
            adapter_schema="native-harness.v2",
        )
        contract["subscription_native_overrides"]["effective_tool_transport"] = harness_binding[
            "name"
        ]
        if backend_binding["auth"] == "api":
            contract.update(
                api_usd=None,
                billing="configured_API_no_dollar_limit",
                subscription_quota_shared_with_user=False,
            )
    if mixed:
        contract.update(
            researcher_model=researcher_model,
            researcher_effort=researcher_effort,
            comparison_scope="mixed_model_system_not_model_controlled_architecture_ablation",
        )
        contract["subscription_native_overrides"].update(
            {
                "delegation_transport": "host_scoped_native_workers",
                "maximum_active_researchers": 2,
                "researcher_model": researcher_model,
                "researcher_effort": researcher_effort,
            }
        )
    output.mkdir(mode=0o700, parents=True)
    config_files = []
    if config_snapshot is not None:
        import yaml

        for kind in ("requested", "resolved"):
            name = f"config.{kind}.yaml"
            with (output / name).open("x", encoding="utf-8") as stream:
                yaml.safe_dump(config_snapshot[kind], stream, allow_unicode=True, sort_keys=False)
            (output / name).chmod(0o444)
            config_files.append(name)
    root = output / arm
    (root / "work").mkdir(mode=0o700, parents=True)
    atomic(
        root / "input-reference.json",
        {"schema": "shared-task.v1", "task_directory": str(task_root)},
    )
    config_files.append(arm + "/input-reference.json")
    (output / "qualification").mkdir(mode=0o700)
    atomic(output / "qualification/accepted.json", qualified)
    # Only evaluator definitions/data are copied into this HOST-ONLY capsule;
    # old scores, mechanisms and solver caches are not part of the task.
    evaluation["total_cpu_cap_seconds"] = resources["evaluation_cpu_seconds"]
    atomic(output / "evaluation_base.json", evaluation)
    atomic(
        output / "qualification/endpoint.json",
        dict(
            status="PASS",
            base_sha256=sha(output / "evaluation_base.json"),
            scientific_solver_calls=0,
            evidence="same pre-existing solver/source pins; not new scientific validation",
        ),
    )
    manifest = {"TASK.md": task_sha, "parent.yaml": PARENT_SHA}
    source = build(output / "source-release")
    files = [
        "evaluation_base.json",
        "qualification/accepted.json",
        "qualification/endpoint.json",
    ] + config_files
    plan = dict(
        schema="astra-subscription-standalone.v1",
        contracts={arm: contract},
        task_manifest=manifest,
        source_release_sha256=source["release_sha256"],
        native_version=version,
        capsule_pins={name: sha(output / name) for name in files},
        clocks_started=False,
        no_model_calls_during_prepare=True,
        observer_required=False,
        qualification="reused science/container implementation; live subscription metadata checked at start",
    )
    atomic(output / "preflight.json", plan)
    atomic(
        output / "commitment.json",
        dict(preflight_sha256=sha(output / "preflight.json")),
    )
    with (output / "README.md").open("x", encoding="utf-8") as stream:
        stream.write(
            (
                "# 本次实验\n\n"
                "- `config.requested.yaml` / `config.resolved.yaml`：YAML 入口的原始参数/绝对路径快照。\n"
                "- `preflight.json` / `source-release/`：完整执行契约/冻结源码。\n"
                "- `current.json`：准备、启动、搜索结束、评测结束时的检查点；实时状态用 status 命令。\n"
                "- `solo-max/transcript.md` / `transcript.jsonl`：接口实时追加，启动前为空；无需 review 生成。\n"
                "- `solo-max/work/`：Agent 正在形成的脚本、候选和报告，不代表已锁定提交。\n"
                "- `solo-max/result.json`：搜索结束结果；`solo-max/final_artifacts/`：锁定提交（若产生）。\n"
                "- `solo-max/endpoint/`：独立评测；不把终点评分反馈给本轮搜索。\n"
                "- `solo-max/review/`：已有中文逐步复盘。\n\n"
                "transcript 的 stream_sequence 是单捕获会话序号，时间是本地观察时间。\n"
                "包含原生公开片段和完成文本；不包含认证/隐藏推理，不是模型 HTTP 镜像。\n"
            ).replace("solo-max", arm)
        )
    from kinetic_agents.observability.transcript import LiveTranscript

    capture = LiveTranscript(root)
    capture.record(
        "run_prepared",
        dict(
            model=model,
            effort=effort,
            max_agents=members,
            task="task/TASK.md",
            config="config.resolved.yaml",
            status="PREPARED_NOT_STARTED",
        ),
    )
    capture.close()
    checkpoint(output, "prepared")
    return dict(
        status="PREPARED_NOT_STARTED",
        run=str(output),
        model=model,
        effort=effort,
        max_members=members,
        task_sha256=task_sha,
        search_cpu_hours=cpu_hours,
        evaluation_cpu_hours=evaluation_hours,
        wall_hours=wall_hours,
        observer_required=False,
    )


def load(run, *, executing=False):
    run = plain(run)
    plan = json.loads((run / "preflight.json").read_text())
    if (
        json.loads((run / "commitment.json").read_text())["preflight_sha256"]
        != sha(run / "preflight.json")
        or plan["schema"] != "astra-subscription-standalone.v1"
    ):
        raise PermissionError("original run commitment changed")
    root = run_root(run, plan)
    contract = plan["contracts"][root.name]
    mixed = root.name == "team-max"
    task_sha = contract["task_sha256"]
    if task_sha not in APPROVED_TASKS or plan["task_manifest"] != {
        "TASK.md": task_sha, "parent.yaml": PARENT_SHA
    }:
        raise PermissionError("unapproved or inconsistent scientific task identity")
    if (
        contract["model"],
        contract["effort"],
        contract["max_members"],
        contract["task_sha256"],
        contract["parent_sha256"],
    ) != (
        contract["model"] if "harness" in contract else MODEL,
        contract["effort"] if "harness" in contract else EFFORT,
        3 if mixed else 1,
        task_sha,
        PARENT_SHA,
    ):
        raise PermissionError("fixed principal/task/team identity changed")
    if (
        "harness" not in contract
        and mixed
        and (
            contract.get("mode") != "astra-subscription-team-v1"
            or contract.get("researcher_model") != "gpt-5.5"
            or contract.get("researcher_effort") not in ("medium", "high")
        )
    ):
        raise PermissionError("fixed researcher identity changed")
    if "harness" in contract:
        from kinetic_agents.harnesses.catalog import verify as verify_harness

        verify_harness(contract["harness"])
        if contract.get("adapter_schema") != "native-harness.v2":
            raise PermissionError("unrecognized native adapter schema")
    resource_contract(
        contract["cpu_seconds"] / 3600,
        contract["wall_seconds"] / 3600,
        contract["evaluation_cpu_seconds"] / 3600,
    )
    for name, expected in plan["capsule_pins"].items():
        if (
            name
            not in {
                "evaluation_base.json",
                "qualification/accepted.json",
                "qualification/endpoint.json",
                "config.requested.yaml",
                "config.resolved.yaml",
                root.name + "/input-reference.json",
            }
            or sha(run / name) != expected
        ):
            raise PermissionError("host task/evaluation capsule changed")
    verify_task(root, plan["task_manifest"])
    release = run / "source-release"
    if verify(release)["release_sha256"] != plan["source_release_sha256"]:
        raise PermissionError("frozen source release changed")
    if (run / "recovery-active.json").exists():
        from kinetic_agents.recovery import execution_release

        release, receipt = execution_release(run, plan)
        plan = {**plan, "execution_source_release_sha256": receipt["new_source_sha256"]}
    if executing and Path(__file__).resolve().parents[1] != release:
        raise PermissionError("execute only from this run's frozen release")
    cli_version()
    return root, contract, plan


class SubscriptionEnvironment(PairEnvironment):
    def budget(self, actor, args, request_id):
        result = super().budget(actor, args, request_id)
        result.pop("model_limit_usd", None)
        result["model"] = dict(
            billing="ChatGPT subscription",
            api_usd=None,
            separate_API_charge_enabled=False,
            quota_shared_with_user=True,
            automatic_credit_purchase=False,
            request_count_limit=None,
        )
        for name, key in [
            ("subscription_usage.json", "usage"),
            ("subscription_quota.json", "account_quota"),
        ]:
            path = self.root / "native" / name
            if path.exists():
                result["model"][key] = json.loads(path.read_text())
        binding = self.store.get("contract").get("backend", {})
        if binding.get("auth") == "api":
            result["model"].update(
                billing="configured API",
                separate_API_charge_enabled=True,
                quota_shared_with_user=False,
                api_usd=None,
                cost_note="No dollar cap configured; provider invoice is authoritative",
                request_audit="native/api_requests.jsonl",
            )
        return result


def finalize(root, store, service):
    result = pair_finalize(root, store, service)
    result["model_account"] = dict(
        billing="ChatGPT subscription",
        api_usd=None,
        usage_file="native/subscription_usage.json",
        quota_file="native/subscription_quota.json",
        automatic_credit_purchase=False,
        api_fallback=False,
    )
    if store.get("contract").get("backend", {}).get("auth") == "api":
        result["model_account"].update(
            billing="configured API",
            quota_file=None,
            request_audit="native/api_requests.jsonl",
            usd_limit=None,
            api_usd=None,
        )
    atomic(root / "result.json", result)
    return result


def worker(run):
    root, contract, plan = load(run, executing=True)
    store = Store(root / "runtime.sqlite")
    if store.get("contract") != contract:
        raise PermissionError("original resource account changed")
    owner = json.loads((root / "local_cpu.json").read_text())
    if owner.get("status") != "ACTIVE" or owner.get("pid") != os.getppid():
        raise PermissionError("owned local process tree required")
    identity = TeamIdentity(
        contract["run_id"],
        contract["task_sha256"],
        contract["model"],
        contract["effort"],
        contract["max_members"],
        researcher_model=contract.get("researcher_model"),
        researcher_effort=contract.get("researcher_effort"),
    )
    service = ScientificTeamService(TeamStore(root / "team", identity))
    environment = SubscriptionEnvironment(root, store, service)

    def harness(account, token, dynamic):
        if "harness" in contract:
            from kinetic_agents.harnesses.catalog import client as build_client

            client = build_client(
                dict(
                    contract=contract,
                    work=root / "work",
                    task=task_directory(root),
                    state=root / "native",
                    service=service,
                    environment=environment,
                    read_only=(
                        "research_budget",
                        "research_compute_status",
                        "research_compute_read",
                        "research_review_read",
                    ),
                ),
                account,
                token,
                [s for s in dynamic if not s["name"].startswith("team_")],
            )
            if hasattr(client, "subscription_stop"):

                def stop_configured(reason):
                    store.update("MODEL_CHANNEL_STOP", reason=reason)
                    store.advance("budget_exhausted")

                client.subscription_stop = stop_configured
            return client
        client = SubscriptionTeamClient(
            root / "work",
            task_directory(root),
            root / "native",
            contract["model"],
            contract["effort"],
            account,
            token,
            service,
            extra_tools=[s for s in dynamic if not s["name"].startswith("team_")],
            actor_tools=environment.handlers,
            actor_read_only=(
                "research_budget",
                "research_compute_status",
                "research_compute_read",
                "research_review_read",
            ),
        )

        def stop(reason):
            store.update("SUBSCRIPTION_STOP", reason=reason)
            store.advance("budget_exhausted")

        client.subscription_stop = stop
        return client

    def gateway():
        if contract.get("backend", {}).get("auth") == "api":
            from kinetic_agents.harnesses.gateway import APIGateway

            def guard():
                if store.advance("tick") in {
                    "COMPLETED",
                    "BUDGET_STOPPED",
                    "FAILED_REVIEW",
                    "CANCELLED",
                    "FINALIZING",
                }:
                    raise PermissionError("run ended; API request rejected")

            return APIGateway(
                contract["backend"],
                [v for v in (contract["model"], contract.get("researcher_model")) if v],
                root / "native",
                guard,
                network_environment=(
                    {key: os.environ[key] for key in HOST_NETWORK_ENV if os.environ.get(key)}
                    if "network" in contract else None
                ),
                expected_efforts=(
                    {v: contract["effort"] if v == contract["model"] else contract["researcher_effort"]
                     for v in (contract["model"], contract.get("researcher_model")) if v}
                    if contract.get("harness", {}).get("name") == "kimi_code_node" else None
                ),
            )
        return SubscriptionAccount(contract["auth_home"], root / "auth-host")

    environment.remote.start()
    try:
        return run_team(
            root,
            store,
            contract,
            service=service,
            environment=environment,
            harness_factory=harness,
            gateway_factory=gateway,
            finalize=lambda r, s: finalize(r, s, service),
            save=lambda n, v: atomic(root / n, v),
            task_manifest=plan["task_manifest"],
            adapter_identity=dict(
                harness=contract.get("harness", {}).get(
                    "name", "native-codex-chatgpt-" + root.name + "-v2"
                ),
                auth=contract.get("backend", {}).get("auth", "official-external-chatgpt-tokens"),
                cli=plan["native_version"],
                source_release_sha256=plan.get(
                    "execution_source_release_sha256", plan["source_release_sha256"]
                ),
            ),
        )
    finally:
        environment.remote.close()


def supervise(run):
    root, contract, plan = load(run, executing=True)
    store = Store(root / "runtime.sqlite")
    with (root / "coordinator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / "local_cpu.json").exists():
            raise PermissionError("execution owner already exists; never reset or duplicate a run")
        result, row = {}, None
        try:
            row = cpu_run(
                [sys.executable, "-B", "-m", MODULE, "worker", "--run", str(run)],
                root / "local_cpu.json",
                LOCAL_OWNER_CEILING - store.get("recovery_prior_local_cpu_seconds", 0),
                2,
                store.get("deadline"),
            )
            atomic(
                root / "owner_finished.json",
                dict(status=row["status"], cpu_seconds=row["cpu_seconds"], at=time.time()),
            )
        finally:
            if store.get("status") not in TERMINAL | {"FINALIZING"}:
                store.advance(
                    "budget_exhausted"
                    if row and row.get("reason") in {"deadline", "cpu_limit"}
                    else "fatal"
                )
            jobs = RemoteJobs(root, store)
            try:
                jobs.close()
                for _ in range(12):
                    jobs.step()
                    if all(r["status"] in ("SETTLED", "REJECTED") for r in jobs.rows()):
                        break
                    time.sleep(5)
                if any(r["status"] not in ("SETTLED", "REJECTED") for r in jobs.rows()):
                    raise ConnectionError("remote accounting/collection pending")
            except Exception as exc:
                atomic(
                    root / "remote_cleanup_pending.json",
                    dict(error_type=type(exc).__name__, at=time.time()),
                )
            try:
                if (root / "team/team.sqlite").exists():
                    service = ScientificTeamService(TeamStore(root / "team"))
                    result = finalize(root, store, service)
                else:
                    result = dict(
                        status=store.get("status"),
                        submission=None,
                        reason="startup_failed_before_scientist",
                        scientifically_verified=False,
                    )
                    atomic(root / "result.json", result)
            finally:
                checkpoint(run, "search_ended")
                postrun_review(root, row or {"status": "UNSETTLED"}, result)
                checkpoint(run, "postrun_finished")
        return result


def start(run):
    root, contract, plan = load(run)
    if contract.get("backend", {}).get("auth") == "api":
        from kinetic_agents.connections import load_credentials

        load_credentials(contract["backend"])
    if contract.get("harness", {}).get("name") in ("claude_code", "kimi_code", "kimi_code_node"):
        from kinetic_agents.harnesses.sandbox import qualify

        qualify(root, task_directory(root), contract["harness"])
    # Codex masks host /tmp in its native shell sandbox. Reject an unqualified
    # public-input location before committing clocks or consuming a model turn.
    import tempfile

    public_task = task_directory(root)
    if public_task.is_relative_to(Path("/tmp")) or public_task.is_relative_to(
        Path(tempfile.gettempdir())
    ):
        raise PermissionError(
            "public task inputs must be outside the native sandbox temporary mount"
        )
    with (root / "launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / "launch_intent.json").exists() or (root / "runtime.sqlite").exists():
            raise PermissionError("launch already attempted; do not reset the original account")
        if any((root / "work").iterdir()):
            raise PermissionError("clean parent-only start required")
        env = dict(os.environ)
        if "network" in contract:
            from kinetic_agents.network import preflight, NetworkPreflightError

            try:
                env, receipt = preflight(contract["network"], contract.get("backend"))
            except NetworkPreflightError as exc:
                atomic(root / "network_preflight.json", exc.receipt)
                raise
            atomic(root / "network_preflight.json", receipt)
        now = time.time()
        atomic(
            root / "launch_intent.json",
            dict(
                status="STARTING",
                at=now,
                authority="user invoked standalone start/submit with explicit new resource limits",
            ),
        )
        store = Store(root / "runtime.sqlite")
        store.initialize(contract, now=now)
        command = [
            sys.executable,
            "-B",
            "-m",
            MODULE,
            "supervise",
            "--run",
            str(plain(run)),
        ]
        # The native process receives a separate stripped environment. This host
        # supervisor needs the existing SSH configuration but never exports it.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        with (root / "coordinator.log").open("xb") as log:
            process = subprocess.Popen(
                command,
                cwd=plain(run) / "source-release",
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        value = dict(
            pid=process.pid,
            run=str(plain(run)),
            model=contract["model"],
            effort=contract["effort"],
            max_members=contract.get("max_members", 1),
            deadline=store.get("deadline"),
            observer_required=False,
            note="started supervisor; model acceptance and science outcomes are not yet verified",
        )
        atomic(root / "coordinator.json", value)
        checkpoint(run, "launched")
        return value


def run_root(run, plan=None):
    run = plain(run)
    if plan is None:
        path = run / "preflight.json"
        if not path.exists():
            return run / "solo-max"  # Legacy synthetic/observation-only fixtures.
        plan = json.loads(plain(path).read_text())
    arms = set(plan.get("contracts", {}))
    if arms not in ({"solo-max"}, {"team-max"}):
        raise PermissionError("one standalone solo/team arm required")
    return run / next(iter(arms))


def status(run):
    # Deliberately no load/verify/SSH: observing must remain cheap and must not
    # require the source tree to work, or advance the experiment's state machine.
    root = run_root(run)
    result = dict(run=str(plain(run)), arm=root.name, observer_required=False)
    db = root / "runtime.sqlite"
    if db.exists():
        import sqlite3

        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
            states = {
                k: json.loads(v) for k, v in connection.execute("SELECT key,value FROM state")
            }
        for key in ("status", "started", "deadline", "failures", "reason", "thread_id"):
            if key in states:
                result[key] = states[key]
    else:
        result["status"] = "PREPARED_NOT_STARTED"
    for name, key in [
        ("result.json", "result"),
        ("coordinator.json", "coordinator"),
        ("native/subscription_usage.json", "subscription_usage"),
        ("native/subscription_quota.json", "subscription_quota"),
        ("native/subscription_error.json", "subscription_error"),
        ("native/subscription_team.json", "team"),
        ("native/workers.json", "workers"),
        ("endpoint/evaluation_state.json", "evaluation"),
        ("review_build_status.json", "review"),
        ("remote_cleanup_pending.json", "cleanup_pending"),
        ("network_preflight.json", "network_preflight"),
    ]:
        path = root / name
        if path.is_file() and not path.is_symlink():
            result[key] = json.loads(path.read_text())
    revision = root / "endpoint_bulk_v1"
    if (revision / "repair_request.json").is_file():
        result["original_evaluation"] = result.pop("evaluation", None)
        state = {"status": "REPAIR_PREPARING", "directory": str(revision)}
        for name in ("evaluation_state.json", "repair_failure.json"):
            path = plain(revision / name)
            if path.is_file():
                state.update(json.loads(path.read_text()))
        state["combined_scorecard_available"] = (revision / "combined_scorecard.json").is_file()
        result["evaluation"] = state
    return result


def checkpoint(run, phase):
    """Lifecycle checkpoints, NOT per-notification database writes."""
    run = plain(run)
    snapshot = status(run)
    atomic(
        run / "current.json",
        dict(
            phase=phase,
            captured_at=time.time(),
            observation="lifecycle_checkpoint_not_live_heartbeat",
            **snapshot,
        ),
    )
    if (run / "preflight.json").exists():
        from kinetic_agents.observability.report import publish

        publish(run, run_root(run), snapshot)


def results(run):
    from kinetic_agents.observability.report import publish

    return publish(run, run_root(run), status(run))


def stop(run):
    root, _, _ = load(run)
    if not (root / "runtime.sqlite").exists():
        return dict(status="NOT_STARTED", model_calls=0)
    store = Store(root / "runtime.sqlite")
    if store.get("status") not in TERMINAL | {"FINALIZING"}:
        store.advance("cancel")
        # The existing CPU owner observes this file and reaps its own children;
        # never signal a possibly recycled PID or cancel unowned Slurm jobs.
        atomic(root / "local_cpu.stop", dict(reason="explicit_user_stop", at=time.time()))
    return dict(
        status=store.get("status"),
        cleanup="owned supervisor collects/cancels own jobs",
        model_calls=0,
        budgets_reset=False,
    )


def review(run):
    from kinetic_agents.observability.review import publish

    root = run_root(run)
    start_cpu = time.process_time()
    result = publish(root, endpoint=(root / "endpoint/scorecard.json").exists())
    atomic(
        root / "manual_review_cpu" / ("review-" + str(time.time_ns()) + ".json"),
        dict(
            local_cpu_seconds=time.process_time() - start_cpu,
            model_calls=0,
            scientific_solves=0,
            purpose="human_requested_read_only_observation",
        ),
    )
    return result


def doctor(auth_home):
    """Actual official auth/catalog RPC, but no thread, model turn or SSH."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="cfx-astra-metadata-") as d:
        root = Path(d)
        (root / "work").mkdir()
        (root / "task").mkdir()
        identity = TeamIdentity("cfx-astra-metadata", TASK_SHA, MODEL, EFFORT, 1)
        service = ScientificTeamService(TeamStore(root / "team", identity))
        account = SubscriptionAccount(auth_home, root / "auth-host")
        client = None
        try:
            account.start()
            client = SubscriptionTeamClient(
                root / "work",
                root / "task",
                root / "native",
                MODEL,
                EFFORT,
                account,
                None,
                service,
            )
            client.request(
                "initialize",
                {
                    "clientInfo": {"name": "cfx_astra_metadata_only", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            client.send({"method": "initialized", "params": {}})
            client.prepare_account()
            quota = json.loads((root / "native/subscription_quota.json").read_text())
            return dict(
                status="SUBSCRIPTION_METADATA_PASS",
                model=MODEL,
                effort=EFFORT,
                max_members=1,
                native_version=cli_version(),
                model_turns=0,
                ssh_commands=0,
                historical_sessions_read=False,
                auth_files_copied=False,
                real_model_inference_tested=False,
                quota_metadata_available=quota.get("status") != "UNAVAILABLE",
            )
        finally:
            if client:
                client.close()
            account.close()


def main():
    parser = argparse.ArgumentParser(
        description="Internal execution owner; use kinetic-agents for submission."
    )
    parser.add_argument("operation", choices=("supervise", "worker"))
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    value = globals()[args.operation](args.run)
    if value is not None:
        print(json.dumps(value, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
