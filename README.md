# Kinetic Agents

Observable research runs on native agent harnesses, with shared task inputs,
single-agent or team execution, and independent scientific evaluation.

**Status: research prototype / private results-only archive.** This is the runner used
in a chemical-kinetics study, not a new model reasoning loop and not a proven
recursive self-improvement algorithm. The current scientific contract is pinned
to USC-II. Remote execution still contains site-specific Slurm restrictions;
this release is not a plug-and-play deployment for an arbitrary cluster.

## What is included

- The original project code plus task inputs, parent/submitted mechanisms,
  experimental observations, reports, figures and dated evaluation evidence.
- Real final results, including failure statuses and cancelled/incomplete run
  entries. Raw transcripts, search intermediates and full-run Release assets
  are **not distributed** in this edition; originals remain in local custody.
  No synthetic logs replace them. See [archive scope](ARCHIVE_SCOPE.md) and
  [research navigation](RESEARCH_ARCHIVE.md).
- Native harness adapters for Codex, Claude Code, Python Kimi Code and Node Kimi
  Code, with explicit model, protocol and account selection.
- One shared task package per task version; Solo and Team do not maintain
  separate copies of the scientific instructions.
- Timestamped run directories, frozen configuration/source identities,
  asynchronous job ownership, resource ledgers and explicit recovery.
- Actor-scoped tools, team coordination and versioned memory/skill records.
- Live captured I/O transcripts, per-agent records and human-readable review.
- Locked mechanism submissions and an independent evaluator with separate
  search/evaluation accounting.
- An **offline** six-dimension research-rubric module. It does not implement a
  production LLM judge or establish a numerical research-quality leaderboard.

Claude Code has an adapter but failed the recorded outer-sandbox acceptance on
the development host. Do not treat adapter presence as qualified production
support. CLI versions and inference-effort names are provider-specific; passing
configuration tests does not establish model access or equivalent compute.

## Repository layout

```text
configs/                    Shared settings and Solo/Team model profiles
tasks/usc_ii/TASK.md         Registered basic scientific task
tasks/usc_ii_expert_v2/      Registered expert-guidance variant
src/kinetic_agents/
  core/                     State, contracts, persistence and accounting
  harnesses/, native/       Native sessions, model connections and tool routing
  research/, team/          Research tools and versioned knowledge objects
  execution/                Jobs, Slurm submission, settlement and recovery
  evaluation/               Locked submissions, numerical evaluation and rubric
  observability/            Live transcripts, results and review reports
tests/                      Synthetic regression and explicit opt-in acceptance
docs/                       Architecture, deployment and release boundaries
data/benchmark/             Historically exposed evaluator inputs and provenance
research/                   Dated reports, observations, figures and analysis
tasks/usc_ii_t*/            20 task packages with paired run configs; no GT/grader
runs/                       Per-run navigation, summaries, submitted artifacts
results/                    Run index and result-package file manifest
scripts/verify_results_archive.py  Offline byte/endpoint consistency checks
local/                      Host-only credentials/qualification (not distributed)
```

Additional query/input task packages are listed in [tasks](tasks/README.md).
They have **no established ground truth (GT)** and no accompanying grader; they
are not completed experiments or validated training/evaluation datasets.

The repository is Private. Its research data are not a new MIT grant for
third-party mechanisms, experiments, papers or downloaded software. Do not make
it Public without a separate data-rights and privacy review. Do not mount this
archive, its prior runs or evaluator answers into a new search agent's workspace.

## Install and run offline checks

Python 3.12 and Linux are the supported development environment. Install native
CLIs separately; this repository does not redistribute them.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[evaluation,test]'
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -q
.venv/bin/python run.py validate --config configs/solo.yaml
.venv/bin/python run.py validate --config configs/team.yaml
```

The default regression suite uses synthetic inputs. Real CLI, network and local
parent-file checks are explicitly opt-in and are reported separately. Passing
the default suite does not prove that a paid model, cluster or benchmark is ready.

## Configure an actual research deployment

Read [configuration and deployment](docs/CONFIGURATION.md) before launching.

1. Read the archived task/parent and evaluator provenance. Their presence does
   not establish rights to redistribute them publicly or suitability for a new
   task. The 610-case pool is historically exposed, not a pristine blind test.
2. Supply newly verified qualification and evaluator manifests under `local/`.
   Keep evaluator inputs outside the search actor's scope. A historical PASS
   string is not a substitute for actually qualifying the runtime and isolation.
3. Inspect the retained site policy in `execution/slurm.py`, `execution/jobs.py`
   and `evaluation/service.py`. It uses the `sca2070` SSH alias, an owned path
   namespace, `cfx` job names and 64-core allocations. Porting this is an explicit
   engineering change requiring tests, not a silent replacement of path strings.
4. Set the shared deployment/account/network settings and the chosen model
   profile. The example model IDs document study configurations, not a promise
   of current availability for your account.
5. For an API profile, copy `.env.example` to `.env` and fill only the selected
   provider fields. For host networking, use `network.env.example` as the
   template for `.env.network`. Set private files to mode `0600`; do not source
   them or put secrets in YAML/task text.
6. Run configuration, native-interface and isolation acceptance before a real
   launch. No credentials are supplied by this repository.

Only on a qualified host, after explicitly accepting resource/usage costs:

```bash
.venv/bin/python run.py doctor --config configs/solo.yaml
.venv/bin/python run.py prepare --config configs/solo.yaml
# Inspect the returned directory, then start that exact prepared run:
.venv/bin/python run.py start --run /absolute/path/to/prepared-run

# Alternative: prepare and start a NEW timestamped run in one command.
.venv/bin/python run.py submit --config configs/team.yaml
```

Do not run `submit` to start a directory already produced by `prepare`: it creates
a new run. Configuration validation alone does not launch a model or submit jobs.
`doctor` includes bounded host/network checks, not paid model inference.

**Cost warning:** current example profiles have no model-dollar or model-call
cap. Their CPU/time limits do not cap API charges. The shared examples retain
512 search core-hours / 24 hours and 64 separately accounted evaluation core-hours
from the study. These are ceilings, not recommendations for a first smoke test.
Change and review limits before launching; never treat publication as permission
to spend money or use a shared cluster.

## Observe a run

```bash
.venv/bin/python run.py status --run /absolute/path/to/run
.venv/bin/python run.py transcript --run /absolute/path/to/run
.venv/bin/python run.py results --run /absolute/path/to/run
.venv/bin/python run.py review --run /absolute/path/to/run
```

Captured public I/O is appended to `transcript.md` / `transcript.jsonl` during
execution and separated by actor. `RUN_REPORT.md` and `overview.json` are entry
points to configuration, results and review. Observation commands are a side
channel; the run owner, not an open chat, drives execution and settlement.

Transcripts are **not** private chain-of-thought, hidden service prompts or a
complete HTTP dump. Truncated/unrecorded information cannot be reconstructed.
These are runtime features, not evidence supplied with this results-only
edition. Its historical transcripts and native account stores are not included.
The optional legacy restore_release.py utility needs separately held full-run
assets; there are no such assets or download instructions for this edition.

## Scientific and reproducibility boundaries

- Search data are chosen by the research agent. Final evaluation is a separate
  account and must not silently feed back into the ended search.
- The study evaluator expects 610 historically exposed conditions (491 + 119).
  Archived inputs and scores allow inspection and offline reanalysis; rerunning
  scientific solves still requires a qualified runtime and separately approved
  resources. The Python wheel/sdist contains code, not the research archive.
- Report input incompatibility, unresolved observables and numerical failure;
  a successful-subset mean is not a full-pool mean.
- Memory/skill versioning is an interface capability, not evidence of RSI gains.
- A team using different worker models is a system comparison, not a pure
  collaboration ablation. A single trajectory is not a statistical repeat study.
- Frozen numerical adapter bytes are retained to preserve scientific identity.
  Do not silently change them while keeping an old evaluation identity.

See [architecture](docs/ARCHITECTURE.md), [release boundaries](docs/RELEASE.md),
[rubric](docs/RUBRIC.md) and [recovery](docs/INFRASTRUCTURE_RECOVERY.md).

## License and contributions

Original project code is distributed under [MIT](LICENSE). Third-party CLIs,
libraries, mechanisms and experimental datasets retain their own terms; see
[third-party notices](THIRD_PARTY_NOTICES.md). No external datasets or model
credentials are relicensed by the MIT file.

See [CONTRIBUTING.md](CONTRIBUTING.md) for tests and change discipline, and
[SECURITY.md](SECURITY.md) before sharing logs or security reports.
