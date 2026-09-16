# Offline research rubric

The six dimensions in `configs/rubric.yaml` are Taste, Methodology, Novelty,
Solid, Robust and DecisionQuality. Each is 0–4 or NA; there is no weighted sum.
Question/anchor prompts are English. Human review may be Chinese.

`rubric-prepare` captures an ended run's task, locked artifacts, real endpoint
scores, per-case results and parent reference. `rubric-check` validates an
explicitly supplied JSON response and produces a review report. Neither command
calls a model. Imported judgments are not authenticated model outputs.

The packet/query interfaces check provenance, bounded reads and citations;
they cannot prove that an interpretation follows from its evidence or that a
method is globally novel. DecisionQuality distinguishes information available
at decision time from later endpoint evidence. Missing evidence is NA, not an
invented score; known contrary evidence must not be hidden as NA.

A production judge transport is not supplied by this release. The example
Codex account/model settings express a possible future identity, not approval
to consume quota or a claim that paid scoring has run. No scientific ranking
or previously proposed 60/40 objective is changed by this module.
