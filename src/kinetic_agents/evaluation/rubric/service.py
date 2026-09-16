"""Read-only reviewer tools and structured receipts; no model transport launch."""

from datetime import datetime, timezone
from collections import Counter
import json
from pathlib import Path
from typing import Protocol
import uuid

from kinetic_agents.observability.transcript import LiveTranscript
from .packet import encoded, load_document, load_packet, plain, write_new, bounded_read
from .schema import markdown, validate_review


class JudgeTransport(Protocol):
    """Future Codex adapter must attest identity, bound costs and isolate files.

    An adapter receives only a prompt and these tools, never the source run,
    credentials, frozen benchmark or other projects. A schema is NOT a sandbox.
    No production implementation or inference is enabled in this draft.
    """

    def review(self, prompt: str, evidence: "EvidenceService") -> dict: ...


def assessment_directory(packet):
    return packet["_directory"] / "assessments" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])


class EvidenceService:
    def __init__(self, directory, *, assessment=None):
        self.packet = load_packet(directory)
        self.output = plain(assessment) if assessment is not None else assessment_directory(self.packet)
        if self.output.exists():
            raise FileExistsError("a reviewer session cannot overwrite previous evidence")
        self.output.mkdir(parents=True, mode=0o700)
        self.capture = LiveTranscript(self.output)
        self.read_ranges = {}
        self.submitted = False
        self.capture.record("rubric_session_prepared", dict(packet_id=self.packet["packet_id"],
                            model_calls=0, native_qualified=False, live_execution_enabled=False))

    def _record(self, tool, arguments, result):
        self.capture.record("rubric_tool_io", dict(tool=tool, arguments=arguments, result=result))
        return result

    def catalog(self, *, offset=0, limit=40, kind=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 80:
            raise ValueError("invalid catalog page")
        if kind is not None and kind not in {row["kind"] for row in self.packet["documents"].values()}:
            raise ValueError("unknown evidence kind")
        inventory = [(key, row) for key, row in self.packet["documents"].items()
                     if kind is None or row["kind"] == kind]
        return self._record("catalog", dict(offset=offset, limit=limit, kind=kind), dict(packet_id=self.packet["packet_id"],
            documents={key: {k: v for k, v in row.items() if k != "file"}
                       for key, row in inventory[offset:offset + limit]},
            total_documents=len(self.packet["documents"]), matching_documents=len(inventory),
            kinds=dict(Counter(row["kind"] for row in self.packet["documents"].values())),
            next_offset=offset + limit if offset + limit < len(inventory) else None,
            missing=self.packet["missing"], omission_count=len(self.packet["omissions"]),
            omissions=self.packet["omissions"][:40], omissions_truncated=len(self.packet["omissions"]) > 40,
            limitations=self.packet["limitations"]))

    def search(self, keywords, *, offset=0, limit=20):
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= 8 or any(
            not isinstance(k, str) or not k.strip() or len(k) > 80 for k in keywords
        ):
            raise ValueError("provide 1..8 literal search keywords, not a regex")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("invalid search page")
        found, matched = [], 0
        for ident in self.packet["documents"]:
            for number, line in enumerate(load_document(self.packet, ident).splitlines(), 1):
                if any(k.casefold() in line.casefold() for k in keywords):
                    if offset <= matched < offset + limit:
                        found.append(dict(document_id=ident, line=number, excerpt=line[:400], truncated=len(line) > 400))
                    matched += 1
        return self._record("search", dict(keywords=keywords, offset=offset, limit=limit),
                            dict(matches=found, total_matches=matched,
                                 next_offset=offset + limit if offset + limit < matched else None))

    def read(self, document_id, *, start_line=1, line_count=40):
        if type(start_line) is not int or start_line < 1 or type(line_count) is not int or not 1 <= line_count <= 80:
            raise ValueError("read accepts 1..80 lines at a positive start")
        lines = load_document(self.packet, document_id).splitlines()
        if start_line > len(lines):
            raise ValueError("start_line is beyond this document")
        selected = lines[start_line - 1:start_line - 1 + line_count]
        if len("\n".join(selected).encode()) > 64000:
            raise ValueError("read exceeds 64KB; request fewer lines, no silent truncation")
        end = start_line + len(selected) - 1
        self.read_ranges.setdefault(document_id, []).append((start_line, end))
        return self._record("read", dict(document_id=document_id, start_line=start_line, line_count=line_count),
                            dict(document_id=document_id, start_line=start_line, end_line=end,
                                 lines=selected, total_lines=len(lines),
                                 kind=self.packet["documents"][document_id]["kind"]))

    def submit(self, value):
        if self.submitted:
            raise PermissionError("one submitted review per session; no score-shopping overwrite")
        required = {"original_task", "independent_endpoint_scores", "independent_endpoint_per_case",
                    "parent_reference_scores_original_endpoint", "parent_reference_per_case_original_endpoint"}
        if self.packet["rubric"].get("version") == "research-rubric.v2":
            unread = [row["label"] for ident, row in self.packet["documents"].items()
                      if row["label"] in required and ident not in self.read_ranges]
            if unread:
                raise ValueError("required task/evaluation evidence was not retrieved: " + ", ".join(unread))
        validate_review(value, self.packet, read_ranges=self.read_ranges)
        # A local tool test does not establish that a requested model authored it.
        write_new(self.output / "review.json", encoded(value))
        write_new(self.output / "REVIEW_ZH.md", markdown(value).encode())
        receipt = dict(status="STRUCTURE_AND_READ_REFERENCES_VALID_NOT_SCIENTIFICALLY_VERIFIED",
                       packet_id=self.packet["packet_id"], model_provenance="unverified",
                       human_review_required=True, numerical_scores_modified=False,
                       semantic_citation_support_verified=False,
                       origin="offline_service_or_unqualified_adapter", live_execution_enabled=False)
        write_new(self.output / "validation.json", encoded(receipt))
        self.submitted = True
        return self._record("submit", value, receipt)

    def close(self):
        self.capture.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def check_response(directory, response):
    """Validate an explicitly supplied JSON, not pretend to invoke Astra."""
    packet = load_packet(directory)
    value = json.loads(bounded_read(response))
    validate_review(value, packet)
    output = assessment_directory(packet)
    output.mkdir(parents=True, mode=0o700)
    write_new(output / "review.json", encoded(value))
    write_new(output / "REVIEW_ZH.md", markdown(value).encode())
    result = dict(status="OFFLINE_SCHEMA_AND_REFERENCES_CHECKED", output=str(output),
                  model_calls=0, model_provenance="unverified_import", human_review_required=True,
                  semantic_citation_support_verified=False, numerical_scores_modified=False)
    write_new(output / "validation.json", encoded(result))
    return result
