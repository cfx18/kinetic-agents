# Architecture and ownership

The native CLI owns model reasoning, coding and native context management. The
project owns research contracts, scoped tools, execution, evidence and accounting.

| Layer | Modules | Responsibility |
| --- | --- | --- |
| Entry and configuration | `cli.py`, `config.py`, `runner.py` | Validate shared tasks, prepare a versioned run and start its owner |
| Harness and connections | `harnesses/`, `native/`, `connections.py`, `network.py` | Explicit provider/protocol/model binding and native sessions |
| Research state | `research/`, `team/` | Actor-scoped tasks, evidence, issues, notes and versioned memory/skills |
| Execution | `core/`, `execution/` | Runtime state, budgets, jobs, settlement and controlled recovery |
| Observability | `observability/` | Captured I/O, public rationale, action/evidence review and results |
| Evaluation | `evaluation/` | Locked submissions, fixed scientific checks and independent score accounts |

## Control flow

```text
YAML + registered shared task
  -> prepare / frozen identities
  -> start / asynchronous owner
  -> principal and optional worker native sessions
  -> scoped research tools and attributed compute
  -> locked final submission
  -> remote settlement and independent evaluation
  -> results and review
```

`status`, `transcript`, `results` and human observation are not prerequisites for
the run to advance. Ordinary streaming notifications do not need a fresh heavy
budget/ledger transaction per notification. Paid actions and scientific state
changes still require checks and durable accounting.

## Separate state objects

- `runtime.sqlite`: execution state, resources and tool trajectories.
- `team/team.sqlite`: actor-attributed tasks, questions, evidence and versioned
  memory/skill objects.
- `work/` and archives: actual research artifacts; structured entries reference
  them rather than replacing raw evidence with summaries.
- `transcript.jsonl` / `.md`: locally captured public events in capture order.
- `native/`: provider-native session identities and recovery metadata.

Memory and skill objects support proposal, checks, activation, usage and
revocation. A successful format/source check is not scientific validation of
the strategy; reuse and benefit require later experiments. This release does
not claim every proposed Bayesian/retrieval policy has been implemented.

## Boundaries

Only the current task and work area should reach a research actor. Credentials,
other runs and evaluator inputs remain host-owned. The current deployment is a
qualified-site research implementation, not a demonstrated hostile multi-tenant
security boundary. Claude/Kimi networking and trusted CLI dependencies require
additional review before making stronger isolation claims.

Frozen numerical adapters under `evaluation/_frozen/mechrl/` are this project's
historical adapters. The historical optimizer source stored as
`evaluation/_frozen/pins/usc_three_mode.py.txt` is a provenance pin, not an active
optimizer. Changing numerical code requires a new evaluation identity.
