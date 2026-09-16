"""Normalize live public Claude/Kimi transcripts for the existing Chinese review."""

import json
import hashlib

from kinetic_agents.observability.review import (
    plain,
    digest,
    scrub,
    encoded,
    MAX_LINE,
    MAX_BYTES,
)
from kinetic_agents.observability.steps import request_observation, output_observation


def snapshot(s):
    result = dict(
        actions=[],
        statements=[],
        sessions=[],
        ignored_reasoning_records=0,
        unhandled_call_types={},
    )
    path = plain(s.root / "transcript.jsonl")
    if not path.exists():
        s.gaps.append({"source": "transcript.jsonl", "reason": "missing"})
        return result
    calls, used, hasher = {}, 0, hashlib.sha256()
    with path.open("rb") as stream:
        for number in range(1, 100001):
            raw = stream.readline(MAX_LINE + 1)
            if not raw:
                break
            used += len(raw)
            if len(raw) > MAX_LINE or used > MAX_BYTES:
                s.gaps.append({"source": "transcript.jsonl", "reason": "bounded_review_limit"})
                break
            hasher.update(raw)
            try:
                row = json.loads(raw)
            except ValueError:
                s.gaps.append(
                    {
                        "source": f"transcript.jsonl:{number}",
                        "reason": "partial_or_malformed_line",
                    }
                )
                continue
            p = row.get("payload", {})
            actor = row.get("actor_id") or p.get("threadId")
            ref = dict(
                source="transcript.jsonl",
                line=number,
                at=row.get("observed_at"),
                actor=actor,
            )

            def action(call_id, name, args):
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {"unparsed_arguments": args}
                key = (actor, p.get("turnId"), call_id)
                value = dict(
                    **ref,
                    id=f"{actor}:{p.get('turnId')}:{call_id}",
                    call_id=call_id,
                    tool=name,
                    arguments_sha256=digest(args),
                    command_excerpt=scrub(str(args.get("command", args.get("cmd", "")))[:1200]),
                    request_excerpt=encoded(scrub(args))[:2000],
                    request_excerpt_may_be_truncated=len(encoded(args)) > 2000,
                    request_observation=request_observation(name, scrub(args)),
                    outputs=[],
                    argument_keys=sorted(args),
                    outcome="not_observed",
                    rationale=None,
                    epistemic_status="observed_action_not_explanation",
                )
                calls[key] = value
                result["actions"].append(value)

            def output(call_id, value):
                found = calls.get((actor, p.get("turnId"), call_id))
                if found:
                    found.update(
                        outcome="output_recorded_not_scientific_success",
                        output_source=ref,
                        output_sha256=digest(value),
                    )
                    found["outputs"].append(
                        {
                            **ref,
                            "sha256": digest(value),
                            "observation": output_observation(value),
                        }
                    )

            kind = row.get("kind")
            if kind == "actor_registered":
                result["sessions"].append(dict(id=actor, parent_thread_id=p.get("parent")))
            elif kind == "tool_request":
                action(p["callId"], p["tool"], p["arguments"])
            elif kind == "tool_result":
                output(p["callId"], p.get("result"))
            elif kind == "native_event":
                event = p.get("event", {})
                message = event.get("message", event)
                if message.get("role") == "assistant" and isinstance(message.get("content"), str):
                    result["statements"].append(
                        {
                            **ref,
                            "text": message["content"],
                            "epistemic_status": "agent_statement_not_verified_fact",
                        }
                    )
                if event.get("type") == "stream_event":
                    continue  # Canonical completed messages below; avoid counting deltas twice.
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {})
                    if "research" not in function.get("name", ""):
                        action(
                            call["id"],
                            function.get("name", "unknown"),
                            function.get("arguments", {}),
                        )
                if message.get("role") == "tool":
                    output(message.get("tool_call_id"), message.get("content"))
                for part in (
                    message.get("content", []) if isinstance(message.get("content"), list) else []
                ):
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "tool_use" and "mcp__research__" not in part.get(
                        "name", ""
                    ):
                        action(
                            part["id"],
                            part.get("name", "unknown"),
                            part.get("input", {}),
                        )
                    elif part.get("type") == "tool_result":
                        output(part.get("tool_use_id"), part.get("content"))
                    elif part.get("type") == "text" and message.get("role") == "assistant":
                        result["statements"].append(
                            {
                                **ref,
                                "text": part.get("text", ""),
                                "epistemic_status": "agent_statement_not_verified_fact",
                            }
                        )
    s.sources.append(
        {
            "path": "transcript.jsonl",
            "sha256": hasher.hexdigest(),
            "bytes": used,
            "scope": "read prefix",
        }
    )
    return result
