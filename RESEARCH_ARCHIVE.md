# Research archive navigation

This is the private `cfx18/kinetic-agents` research snapshot. Code, task inputs,
mechanisms, observations, reports and final score evidence are browsable in Git.
This is the results-only edition. Raw logs and full-run Release assets are not
uploaded. A clone contains the actual selected materials; no additional large
run download is required. See [ARCHIVE_SCOPE.md](ARCHIVE_SCOPE.md).

## Start here

- `research/report-20260916/README.md`: Chinese theory/results report, figures,
  tables and analysis scripts. Its original dated 10-endpoint cutoff is retained.
- `runs/README.md`: all run IDs, task/model/team settings, lifecycle and endpoint
  status, and links to submitted mechanisms/score evidence. Cancelled and
  unsuccessful runs are not omitted.
- `data/benchmark/evaluation_base.json`: the 610 historically exposed evaluator
  conditions, original observations, uncertainty/units, split and solver policy.
- `tasks/usc_ii/` and `tasks/usc_ii_expert_v2/`: unchanged basic/expert task text
  and USC-II parent mechanism.
- `research/project-notes/`: original dated methods, infrastructure and evidence
  audits. These are historical documents, not current launch instructions.
- `results/runs.json`: all run identities and historical lifecycle states.
- `results/files.json`: exact files supplied, with hashes and copy/generation
  provenance. This is an export-consistency index, not a signed research log.
- `results/endpoint_summary.json`: offline-verified links to existing endpoint
  results, kept separate from the original report's 10-endpoint cutoff.

## Verify the supplied results without rerunning an experiment

After cloning the reviewed private repository, run the standard-library-only
checker below. It checks file identity, submitted mechanism hashes, case-pool
identity and score aggregation using the unchanged project scorer.

```bash
python3 scripts/verify_results_archive.py
```

This makes no network/model/solver calls and does not write to research files.
It does not claim to replay the Agent, regenerate unprovided logs, or prove
authenticity merely from self-contained hashes. Numerical reruns need a separately
qualified solver environment and compute authorization.

Archived absolute paths identify the original host, not paths that must exist
on the reader's computer. Original scientific evidence is not globally rewritten
to hide provenance or simulate portability. Missing remote-only/unrecorded data
cannot be reconstructed by a download script.

## Read the scientific boundary first

The archive exposes evaluator answers, prior candidate mechanisms and process
findings. Do not mount it into a new search agent's clean workspace. The 610-case
pool has historical exposure and is not a pristine blind test. A final endpoint
status is not proof that every candidate covered all 610 cases or that RSI worked.

Original reports retain their evidence dates. Run capture times are separate;
nonterminal per-file snapshots are not complete restart checkpoints. Independent
evaluation and search costs remain separate. See `research/DATA_RIGHTS.md` before
sharing any material outside the private repository.
