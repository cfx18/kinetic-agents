"""Versioned public contracts. Research claims are not evaluator receipts."""

from dataclasses import asdict
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re


class Conflict(ValueError):
    """Stale version or conflicting retry: re-read, do not blindly retry."""


class TaskStatus(StrEnum):
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting_for_principal"
    REVIEW = "awaiting_review"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"


def encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("expected an opaque identifier, not a path")
    return value


def text(value):
    if not isinstance(value, str) or not value.strip() or len(encode(value).encode()) > 12000:
        raise ValueError(
            "text must contain 1..12000 encoded bytes; store large evidence as artifacts"
        )
    return value


def version(value):
    if type(value) is not int or value < 0:
        raise ValueError("nonnegative integer version required")
    return value


@dataclass(frozen=True)
class TeamIdentity:
    run_id: str
    task_sha256: str
    model: str
    effort: str
    max_members: int = 4
    schema: str = "research-team.v1"
    researcher_model: str | None = None
    researcher_effort: str | None = None

    def __post_init__(self):
        identifier(self.run_id)
        text(self.model)
        if self.effort not in {
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
            "ultra",
            "thinking",
            "off",
        }:
            raise ValueError("explicit supported reasoning setting required")
        if not re.fullmatch("[0-9a-f]{64}", self.task_sha256):
            raise ValueError("task digest required")
        if type(self.max_members) is not int or not 1 <= self.max_members <= 32:
            raise ValueError("team capacity must be 1..32 including the principal; 1 is solo")
        if self.schema != "research-team.v1":
            raise ValueError("unsupported team schema; migrate explicitly")
        if (self.researcher_model is None) != (self.researcher_effort is None):
            raise ValueError("researcher model and effort must be specified together")
        if self.researcher_model is not None:
            text(self.researcher_model)
            if self.max_members == 1 or self.researcher_effort not in {
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
                "ultra",
                "minimal",
                "thinking",
                "off",
            }:
                raise ValueError("explicit researcher settings require a team")

    def as_dict(self):
        value = asdict(self)
        # Keep legacy identity encodings byte-compatible for homogeneous runs.
        if self.researcher_model is None:
            value.pop("researcher_model")
            value.pop("researcher_effort")
        return value
