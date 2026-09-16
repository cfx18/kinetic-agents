"""Explicit decision justifications, using the existing versioned team notebook.

No hidden reasoning collection, new database, scientific advice, or evaluator
access. This surface is for NEW source releases, never a hot patch to old runs.
"""

import json

from kinetic_agents.team.contracts import identifier

STAGES = (
    "task_definition",
    "method",
    "case_selection",
    "candidate",
    "validation",
    "delegation",
    "tooling",
    "infrastructure",
    "memory_skill",
    "stopping",
)
SCHEMA = "research-decision.v1"
QUERY_SCHEMA = "research-query.v1"


def record_query(service, actor, args, request_id):
    """Agent-authored retrieval receipt; actual tool arguments/returns stay authoritative."""
    required = {
        "query_id",
        "expected_version",
        "purpose",
        "method",
        "request",
        "returned_summary",
        "important_findings",
        "importance_status",
        "decision_effect",
        "source_addresses",
        "references",
    }
    if set(args) != required or args["importance_status"] not in ("identified", "none", "unknown"):
        raise ValueError("explicit query, return, importance and use fields required")
    identifier(args["query_id"])
    if not args["query_id"].startswith("query_"):
        raise ValueError("query_ ID namespace required")
    for key in (
        "purpose",
        "method",
        "request",
        "returned_summary",
        "important_findings",
        "decision_effect",
    ):
        if not isinstance(args[key], str) or not args[key].strip() or len(args[key]) > 2000:
            raise ValueError("bounded query statements required; unknown must be explicit")
    if (
        not isinstance(args["source_addresses"], list)
        or not 1 <= len(args["source_addresses"]) <= 12
        or any(
            not isinstance(s, str) or not s.strip() or len(s) > 1000
            for s in args["source_addresses"]
        )
    ):
        raise ValueError(
            "actual retrieval addresses/call IDs required; addresses are not trusted references"
        )
    content = {k: args[k] for k in required - {"query_id", "expected_version", "references"}}
    content.update(
        schema=QUERY_SCHEMA,
        epistemic_status="agent_statement_not_verified",
        source_addresses_verified=False,
    )
    return service.note(
        actor,
        document_id=args["query_id"],
        expected_version=args["expected_version"],
        kind="strategy",
        title="query: " + args["purpose"][:100],
        content=json.dumps(content, ensure_ascii=False, sort_keys=True),
        references=args["references"],
        status="draft",
        request_id=request_id,
    )


def record(service, actor, args, request_id):
    required = {
        "decision_id",
        "expected_version",
        "stage",
        "decision",
        "reason_summary",
        "alternatives",
        "references",
        "outcome",
        "revisit_when",
    }
    if set(args) != required or args["stage"] not in STAGES:
        raise ValueError("explicit decision fields and registered stage required")
    identifier(args["decision_id"])
    if not args["decision_id"].startswith("decision_"):
        raise ValueError("decision_ ID namespace required")
    for field in ("decision", "reason_summary", "outcome", "revisit_when"):
        value = args[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(
                "bounded brief decision statements required; use unknown when unrecorded"
            )
    if (
        not isinstance(args["alternatives"], list)
        or len(args["alternatives"]) > 8
        or any(not isinstance(s, str) or len(s) > 500 for s in args["alternatives"])
    ):
        raise ValueError("at most eight short alternatives")
    content = {k: args[k] for k in required - {"decision_id", "expected_version", "references"}}
    content.update(schema=SCHEMA, epistemic_status="agent_statement_not_verified")
    return service.note(
        actor,
        document_id=args["decision_id"],
        expected_version=args["expected_version"],
        kind="strategy",
        title=args["stage"] + ": " + args["decision"][:100],
        content=json.dumps(content, ensure_ascii=False, sort_keys=True),
        references=args["references"],
        status="draft",
        request_id=request_id,
    )


def read(service, actor, args):
    """Reuse indexed, bounded notebook retrieval, NOT rebuild an entire report."""
    if set(args) - {"section", "after", "limit", "record_id", "version"}:
        raise ValueError("no paths, run IDs or evaluator selectors accepted")
    kinds = {
        "decisions": "document",
        "queries": "document",
        "tasks": "task",
        "questions": "question",
        "memory": "memory",
        "skills": "skill",
        "evidence": "evidence",
    }
    section = args.get("section", "decisions")
    if section not in kinds:
        raise ValueError("search notebook sections only")
    kind = kinds[section]
    if "record_id" in args:
        if set(args) & {"after", "limit"}:
            raise ValueError("choose a record version OR a catalog page")
        return {
            "scope": "this_run_search_only",
            "source": "team_notebook",
            "record": service.read(
                actor, kind=kind, record_id=args["record_id"], record_version=args.get("version")
            ),
        }
    if "version" in args:
        raise ValueError("version requires record_id")
    result = service.list(
        actor, kind=kind, after=args.get("after", ""), limit=args.get("limit", 12)
    )
    return {
        "scope": "this_run_search_only",
        "source": "team_notebook",
        "catalog": result,
        "note": "Documents include ordinary notes, query_ and decision_ records; read selected versions. "
        "Statements and retrieved objects are not verified causal explanations.",
    }
