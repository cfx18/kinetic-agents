"""Extracted reusable implementation; historical launchers intentionally excluded."""

import hashlib, json

MODEL = "gpt-6-astra"


def encoded(value):
    return (
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        + "\n"
    ).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()
