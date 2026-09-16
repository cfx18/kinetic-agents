"""Strict YAML run contracts. No environment evaluation, credentials or launch."""

from datetime import datetime
from datetime import timezone
from copy import deepcopy
import os
from pathlib import Path
import re
import uuid

import yaml

from kinetic_agents.native.subscription import MODEL
from kinetic_agents.native.subscription import EFFORT
from kinetic_agents.native.subscription import plain


class ConfigLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("YAML aliases are not supported in a run contract")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("YAML keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def fields(value, expected, location):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(
            f'{location}: required keys are {", ".join(sorted(expected))}; unknown keys are rejected'
        )


def read_yaml(path):
    path = plain(path)
    if path.stat().st_size > 64 * 1024:
        raise ValueError("YAML configuration exceeds 64 KiB")
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=ConfigLoader)
    except yaml.YAMLError as exc:
        # Parser messages can echo input secrets: never forward their text.
        raise ValueError("invalid safe YAML run configuration") from None
    return value


def load_config(path):
    path = plain(path)
    overlay = read_yaml(path)
    configurable = "harness" in overlay or "backend" in overlay
    fields(
        overlay,
        ("base", "profile", "model", "output_prefix")
        + (("harness", "backend") if configurable else ()),
        "profile",
    )
    if (
        not isinstance(overlay["base"], str)
        or not overlay["base"]
        or Path(overlay["base"]).name != overlay["base"]
    ):
        raise ValueError("base must name a common YAML beside the profile")
    value = read_yaml(path.parent / overlay["base"])
    fields(
        value,
        (
            "version",
            "account",
            "resources",
            "inputs",
            "output",
            "transcript",
            "deployment",
        ) + (("network",) if "network" in value else ()),
        "common",
    )
    fields(value["output"], ("root",), "common.output")
    value = {
        **value,
        "profile": overlay["profile"],
        "model": overlay["model"],
        "output": {**value["output"], "prefix": overlay["output_prefix"]},
    }
    from kinetic_agents.execution.deployment import validate_deployment

    validate_deployment(value["deployment"])
    if (
        type(value["version"]) is not int
        or value["version"] != 1
        or value["profile"]
        not in (
            ("solo", "team")
            if configurable
            else ("astra_subscription_solo", "astra_subscription_team")
        )
    ):
        raise ValueError(
            "supported profiles: version 1 / astra_subscription_solo or astra_subscription_team"
        )
    mixed = value["profile"] in ("astra_subscription_team", "team")
    kimi = configurable and overlay["harness"].get("name") in ("kimi_code", "kimi_code_node")
    fields(
        value["model"],
        ("name", "reasoning_effort", "max_agents")
        + (("researcher_model", "researcher_effort") if mixed else ())
        + (("context_tokens",) if kimi else ()),
        "model",
    )
    if not configurable and (
        (value["model"]["name"], value["model"]["reasoning_effort"]) != (MODEL, EFFORT)
        or type(value["model"]["max_agents"]) is not int
        or value["model"]["max_agents"] != (3 if mixed else 1)
    ):
        raise ValueError("qualified principal: gpt-6-astra / xhigh; solo 1 or team 3 total agents")
    if (
        not configurable
        and mixed
        and (
            value["model"]["researcher_model"] != "gpt-5.5"
            or value["model"]["researcher_effort"] not in ("medium", "high")
        )
    ):
        raise ValueError("qualified researchers: gpt-5.5 / medium or high")
    if configurable:
        from kinetic_agents.harnesses.catalog import validate
        from kinetic_agents.connections import validate_backend

        value["harness"] = validate(overlay["harness"], value["model"])
        value["backend"] = validate_backend(overlay["backend"], value["harness"]["name"])
        if value["model"]["max_agents"] != (3 if mixed else 1):
            raise ValueError("solo/team profile and max_agents disagree")
        if kimi and (
            type(value["model"]["context_tokens"]) is not int
            or value["model"]["context_tokens"] < 64000
        ):
            raise ValueError("Kimi requires the model's explicit context_tokens (at least 64000)")
    fields(value["account"], ("auth_home",), "account")
    fields(
        value["resources"],
        ("search_cpu_hours", "wall_hours", "evaluation_cpu_hours"),
        "resources",
    )
    from kinetic_agents.runner import resource_contract

    resource_contract(
        value["resources"]["search_cpu_hours"],
        value["resources"]["wall_hours"],
        value["resources"]["evaluation_cpu_hours"],
    )
    fields(value["inputs"], ("task", "accepted", "evaluation_base"), "inputs")
    fields(value["output"], ("root", "prefix"), "output")
    if not isinstance(value["output"]["prefix"], str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value["output"]["prefix"]
    ):
        raise ValueError("output.prefix must be a short plain directory prefix")
    fields(value["transcript"], ("enabled",), "transcript")
    if value["transcript"]["enabled"] is not True:
        raise ValueError("this observable run profile requires live transcript.enabled: true")
    # YAML contains no credential values. auth_home names an already logged-in
    # host directory, not an auth file to snapshot or expose to the scientist.
    resolved = deepcopy(value)
    if "network" in value:
        from kinetic_agents.network import validate_network

        validate_network(value["network"])
        raw = value["network"]["env_file"]
        candidate = Path(raw) if Path(raw).is_absolute() else path.parent / raw
        resolved["network"]["env_file"] = str(plain(os.path.abspath(candidate)))
    if configurable and value["backend"]["auth"] == "api":
        raw = value["backend"]["env_file"]
        if not isinstance(raw, str) or not raw or "$" in raw or raw.startswith("~"):
            raise ValueError("backend.env_file must be a literal host path")
        candidate = Path(raw) if Path(raw).is_absolute() else path.parent / raw
        resolved["backend"]["env_file"] = str(plain(os.path.abspath(candidate)))
    for section, names in [
        ("account", ("auth_home",)),
        ("inputs", ("task", "accepted", "evaluation_base")),
        ("output", ("root",)),
    ]:
        for name in names:
            raw = value[section][name]
            if (
                not isinstance(raw, str)
                or not raw
                or "\x00" in raw
                or "$" in raw
                or raw.startswith("~")
            ):
                raise ValueError(f"{section}.{name}: use a literal path, not shell/env expansion")
            candidate = Path(raw) if Path(raw).is_absolute() else path.parent / raw
            resolved[section][name] = str(plain(os.path.abspath(candidate)))
    output = Path(resolved["output"]["root"])
    auth = Path(resolved["account"]["auth_home"])
    if (
        output == Path("/")
        or output == auth
        or output.is_relative_to(auth)
        or auth.is_relative_to(output)
    ):
        raise PermissionError("output root and account directory must be disjoint")
    task = Path(resolved["inputs"]["task"])
    if task.is_relative_to(output) or output.is_relative_to(task):
        raise PermissionError("task input must be separate from generated runs")
    if configurable and value["backend"]["auth"] == "api":
        env_path = Path(resolved["backend"]["env_file"])
        if env_path.is_relative_to(output) or env_path.is_relative_to(task):
            raise PermissionError(".env must remain outside the task and run directories")
    if "network" in resolved:
        env_path = Path(resolved["network"]["env_file"])
        if env_path.is_relative_to(output) or env_path.is_relative_to(task):
            raise PermissionError("network.env_file must remain outside task and run directories")
    return value, resolved


def new_run_path(config):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return (
        Path(config["output"]["root"])
        / f"{config['output']['prefix']}-{stamp}-{uuid.uuid4().hex[:8]}"
    )


def prepare_config(path):
    from kinetic_agents.runner import prepare

    requested, resolved = load_config(path)
    run = new_run_path(resolved)
    resources = resolved["resources"]
    result = prepare(
        run,
        cpu_hours=resources["search_cpu_hours"],
        wall_hours=resources["wall_hours"],
        evaluation_hours=resources["evaluation_cpu_hours"],
        auth_home=resolved["account"]["auth_home"],
        researcher_model=resolved["model"].get("researcher_model"),
        researcher_effort=resolved["model"].get("researcher_effort"),
        deployment=resolved["deployment"],
        network=resolved.get("network"),
        **resolved["inputs"],
        config_snapshot={"requested": requested, "resolved": resolved},
        **(
            {
                "execution": {
                    "harness": resolved["harness"],
                    "backend": resolved["backend"],
                    "model": resolved["model"],
                }
            }
            if "harness" in resolved
            else {}
        ),
    )
    return result
