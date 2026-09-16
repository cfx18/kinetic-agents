"""Unified YAML submission and cheap, model-free run observation."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

import kinetic_agents.runner as runner
from kinetic_agents.config import load_config
from kinetic_agents.config import prepare_config
from kinetic_agents.native.subscription import plain


def dispatch(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "validate",
            "doctor",
            "prepare",
            "submit",
            "start",
            "recover",
            "status",
            "results",
            "logs",
            "transcript",
            "review",
            "stop",
            "rubric-prepare",
            "rubric-check",
        ),
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--response", type=Path, help="Offline rubric JSON; never invokes a model")
    parser.add_argument("--endpoint", choices=("endpoint/scorecard.json", "endpoint_bulk_v1/scorecard.json"))
    parser.add_argument("--agent", help="Filter transcript to one native actor ID (not a path)")
    args = parser.parse_args(argv)
    if args.agent is not None and args.operation != "transcript":
        parser.error("--agent is only supported by transcript")
    if args.response is not None and args.operation != "rubric-check":
        parser.error("--response is only supported by rubric-check")
    if args.endpoint is not None and args.operation != "rubric-prepare":
        parser.error("--endpoint is only supported by rubric-prepare")
    if args.operation == "rubric-prepare":
        if args.run is None or args.config is None:
            parser.error("rubric-prepare needs --run and --config; no model calls")
        from kinetic_agents.evaluation.rubric.packet import prepare_run
        value = prepare_run(args.run, args.config, endpoint=args.endpoint or "endpoint/scorecard.json")
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    if args.operation == "rubric-check":
        if args.run is None or args.response is None or args.config is not None:
            parser.error("rubric-check needs --run <packet-directory> and --response <JSON>")
        from kinetic_agents.evaluation.rubric.service import check_response
        print(json.dumps(check_response(args.run, args.response), ensure_ascii=False, indent=2))
        return 0
    if args.operation in ("validate", "doctor", "prepare", "submit"):
        if args.config is None or args.run is not None:
            parser.error(
                "validate/prepare/submit require --config, not --run; each new run gets a unique timestamp"
            )
        if args.operation in ("validate", "doctor"):
            _, resolved = load_config(args.config)
            value = dict(
                status="CONFIG_VALID_NOT_STARTED",
                config=resolved,
                model_calls=0,
                scientific_solves=0,
            )
            if args.operation == "doctor":
                from kinetic_agents.harnesses.catalog import inspect
                from kinetic_agents.connections import bind_backend

                value["harness"] = inspect(
                    resolved.get("harness", {"name": "codex", "executable": "codex"})
                )
                value["backend"] = bind_backend(resolved.get("backend", {"auth": "subscription"}))
                value["status"] = "LOCAL_INTERFACE_PRESENT_NOT_MODEL_QUALIFIED"
                if "network" in resolved:
                    from kinetic_agents.network import preflight

                    _, value["network_preflight"] = preflight(resolved["network"], value["backend"])
        else:
            value = prepare_config(args.config)
            if args.operation == "submit":
                print(json.dumps(value, ensure_ascii=False), flush=True)
                value = runner.start(Path(value["run"]))
    else:
        if args.run is None or args.config is not None:
            parser.error(
                "existing runs require --run, not --config; their saved configuration is authoritative"
            )
        if args.operation in ("logs", "transcript"):
            name = "transcript.md" if args.operation == "transcript" else "coordinator.log"
            root = runner.run_root(args.run)
            if args.agent:
                from kinetic_agents.team.contracts import identifier

                root = plain(root / "agents" / identifier(args.agent))
            return subprocess.call(["tail", "-n", "60", "-F", str(root / name)])
        if args.operation == "recover":
            from kinetic_agents.recovery import recover

            value = recover(args.run)
        else:
            value = getattr(runner, args.operation)(args.run)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    try:
        return dispatch(argv)
    except (
        PermissionError,
        FileExistsError,
        ValueError,
        FileNotFoundError,
        TimeoutError,
        ConnectionError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "NOT_LAUNCHED_OR_STOPPED",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
