# Contributing

Keep scientific objectives, mutable policies and immutable evaluation rules
separate. Put changes in the responsible layer; do not embed one run's results
or account credentials into a general agent class.

Default local regression (no provider requests or remote Slurm jobs):

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -q
```

Native interface checks use real installed CLIs with synthetic local services
and are explicitly opt-in via `RUN_NATIVE_TEAM_QUALIFICATION=1` or
`RUN_NATIVE_WEB_QUALIFICATION=1`. Inspect their fixture, sandbox and network
requirements before enabling them. Skipped acceptance is not passed acceptance.
`RUN_LOCAL_INPUT_QUALIFICATION=1` additionally checks separately supplied parent
files; the default source suite does not require those private files.

Numerical changes must preserve old results and receive new source/evaluator
identities. Do not change errors, failures, physical invariants or frozen code
only to make an old test pass. Recovery must preserve original deadlines,
accounting, session identity and failed records.

Do not commit `.env`, private account homes, `local/` inputs, `runs/`, mechanisms
or transcripts. A test should use synthetic data unless explicitly marked as
an optional local-input or native acceptance test. Build and inspect archives
before release; never zip an unfiltered live workspace for publication.
