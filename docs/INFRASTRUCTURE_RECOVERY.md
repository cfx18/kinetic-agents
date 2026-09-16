# Explicit infrastructure recovery

For the diagnosed Kimi submission-state race, after an explicit restart request:

```bash
python run.py recover --run runs/<existing-kimi-run>
python run.py status --run runs/<existing-kimi-run>
python run.py transcript --run runs/<existing-kimi-run>
```

This is not `submit` or a fresh budget. The qualified recovery path requires a
failed, unsubmitted run, a stopped owner, a verifiable original native session,
unexpired original deadline and reconciled remote jobs. It rejects a second
recovery attempt, scored/submitted runs and unresolved remote effects. Do not
manually reset terminal state or rerun an ambiguous `sbatch` command.

The original `source-release/`, preflight commitment, data, native history and
API logs remain. `recovery/slurm-race-v1/` holds the patched source release,
authorization/identity/cost receipt, original SQLite snapshot and displaced
lifecycle views. `recovery-active.json` binds execution to that exact revision.
Only the reviewed infrastructure files may differ. Neither the scientist's
workflow nor the benchmark is modified by recovery.

Prior local search CPU is carried forward; local execution and evaluator
overhead limits do not reset. The original last API request may have an unknown
outcome: its audit entry remains unknown, never a free or invented completion.
The original failed run and the repaired continuation must be distinguished in
comparisons. A new owner PID is not sufficient acceptance: verify an original
native session resume, a real response and successful scoped tool use.

Node Kimi 0.28.1 may emit its session hint only at clean exit. For an interrupted
first turn, recovery accepts only one unique actor-local native session index,
matching working directory and native state/history files. It never chooses an
arbitrary latest session, imports another home, or reads private reasoning to
manufacture context.

Normal `SUBMITTING` is distinct from genuine `SUBMIT_UNCERTAIN`. A live dispatch
can be observed without killing the run. A failed response or an in-flight intent
left by a crashed dispatcher still fails closed; it is not automatically sent
twice. Slurm cancellation actor metadata is retained as `raw_state` while the
canonical state is `CANCELLED`, including a job cancelled before CPU allocation.
