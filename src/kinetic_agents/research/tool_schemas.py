"""Extracted reusable implementation; historical launchers intentionally excluded."""

SPECS = [
    {
        "type": "function",
        "name": "research_budget",
        "description": "Read this run's remaining model/CPU/time allowance; no scientific advice.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "finish_research",
        "description": "Register your final files and end the scientific search. Files remain subject to independent validation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mechanisms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                    "description": "Relative paths to existing mechanism files in the current workspace.",
                },
                "report": {
                    "type": "string",
                    "description": "Relative path to an existing report file, e.g. REPORT.md. NOT the report text.",
                },
                "outcome": {"type": "string", "enum": ["submitted", "incomplete"]},
            },
            "required": ["mechanisms", "report", "outcome"],
            "additionalProperties": False,
        },
    },
]
