# Kimi expert-V2 Solo / Team — launched

Observation time: 2026-09-16 00:23:43 UTC (host clock).

The user reauthorized the previously cancelled pair after recharging the API
account. This is a fresh experiment; neither Astra account nor its results were
modified. No rubric model was invoked.

## Runs and limits

| Arm | Run directory under `runs/` | Supervisor PID | Observed state |
|---|---|---|---|
| Solo | `expert-v2-kimi-solo-20260916T002108.637960Z-d26b7970` | 1479489 | ACTIVE |
| Team | `expert-v2-kimi-team-20260916T002109.891728Z-d9881267` | 1479785 | ACTIVE |

Each arm: 512 allocated search core-hours, 24-hour window, plus a separate
64-core-hour endpoint account. Total CPU authorization: 1,152 core-hours.
There is no USD or model-call cap, following the user's same-as-Astra decision.
The official API is metered; the supplier invoice is authoritative. No payment
or automatic credit purchase is performed by the launcher.

Both use Node `@moonshot-ai/kimi-code` 0.28.1, `kimi-k3`, `max`, and an explicit
1,048,576-token context declaration. Team allows at most two additional
researchers, also `kimi-k3/max`, sharing the arm account. The legacy Python Kimi
CLI is not the selected executable. Its compatibility adapter remains intact.

Entry-point SHA-256:
`46a0095fa08385027e2e2d02d3c3ee274ecc2094f136dc745910bd72273f7763`.

Both configurations point to the user-designated host-only AgentCFD official
dotenv file. Only the trusted gateway holds the upstream key. No key was
printed, copied into task input, or included in the transcript.

## Same scientific task

Input: `tasks/usc_ii_expert_v2/TASK.md`, shared with the completed Astra V2 pair.
Task SHA-256:
`dee27db8a8c1332d8e704fc55697eb52eee8d88acac4bc9c4ca9acd33284a2a5`.

Start from the supplied USC-II parent, not old candidates or memory. The Agent
organizes its own cases and methods; expert database/literature hints are the
existing shared task text, not added per-arm scientific guidance. The fixed
610 historically exposed endpoint cases remain evaluator-side and are not
supplied as a search pool. Endpoint feedback cannot resume this search.

Remote submissions use `ssh sca2070` then `sbatch`, `cfx` job names, `-N 1 -n 64`,
under `/public3/home/sca2070/WORK/Caifeixue/AgenticRL_research_runs/kinetic-agents`.
The scheduler command and pre-existing runtime image passed a read-only check.
No remote scientific job had yet been observed at this early snapshot.

## Acceptance evidence

- Latest full local regression: **273 passed, 13 skipped**, 30.45 seconds.
- Actual new Node client → simulated API → scientific MCP, Solo/Team:
  **2 passed, 4 deselected**, 15.18 seconds; no supplier calls in these tests.
- Combined old/new client regression earlier: **4 passed, 2 deselected**,
  38.52 seconds. The old-client result is not substituted for Node acceptance.
- The native tests verify outbound `max`, MCP execution and public capture,
  same-session resume, a scoped researcher, and rejection of an attempted
  untracked native `Agent` call. Native `Agent`/`AgentSwarm` schemas remain
  visible but denied; host-budgeted `research_spawn` is the team route.
- The unchanged filesystem sandbox rejects host-private reads and task writes;
  both native Node launches passed the sandbox bootstrap checks. This is not
  a claim of formal adversarial security or prevention of every network abuse.
- Startup repairs were made before formal runs: incompatible `--prompt/--auto`
  combination; first-launch migration of persisted `thinking.effort=max` to
  `high`. Using native model `default_effort=max` avoids that migration.
  The gateway checks actual effort before supplier dispatch.
- No synthetic output cap was added. Observed native requests choose
  `max_tokens=131072`; this is a per-response ceiling, not actual token usage.

Real API snapshot: Solo 2 completed / 0 failed requests; Team 1 completed,
1 outstanding / 0 failed. Both have read the task/parent and successfully
called `research_budget`. All observed requests used `kimi-k3/max`.
These facts establish real connection/tool startup, not scientific success.

## Observation and outputs

The detached non-model supervisor owns each run; the chat observer is optional.
Within each run, use `solo-max/` or `team-max/` respectively:

- `transcript.md`, `transcript.jsonl`: live captured public model/tool I/O.
- `native/api_requests.jsonl`: supplier dispatch/usage/error metadata.
- `native/cli-home/`: the native run-specific context/session storage, private
  working state rather than a public reasoning export.
- `work/`: evolving artifacts; not final scored mechanisms.
- `result.json`, `final_artifacts/`: terminal result and locked submission.
- `endpoint/`: independent bounded 610 evaluation after submission.

Use `run.py status --run <run-directory>` or
`run.py transcript --run <run-directory>`; neither calls a model.
This is a whole-system cross-model baseline. One Solo/Team pair does not prove
RSI or establish a general causal benefit from delegation.
