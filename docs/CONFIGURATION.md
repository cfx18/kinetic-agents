# Configuration and deployment

Shared resources, task paths, account and deployment live in `common.yaml` or
`expert-v2-common.yaml`. A Solo/Team profile chooses a harness, model and output
prefix, and cannot silently override the shared scientific inputs.

| Profiles | Harness | Connection |
| --- | --- | --- |
| `solo.yaml`, `team.yaml` | Codex | Explicit subscription login directory |
| `codex-api-*.yaml` | Codex | Responses API |
| `claude-*.yaml` | Claude Code | Anthropic Messages API; not production-qualified on the original host |
| `kimi-*.yaml` | Python Kimi Code | Explicit compatible API protocol |
| `baseline-v1-kimi-*.yaml`, `expert-v2-kimi-*.yaml` | Node Kimi Code | Explicit compatible API protocol |
| `baseline-v1-deepseek-*.yaml`, `expert-v2-deepseek-*.yaml` | Codex | Explicit Responses API |

The Node and Python Kimi adapters are distinct. Set the Node profiles' executable
to your verified Node CLI entry point; do not point them at the Python CLI.
The release placeholder is not an installed runtime or a download instruction.

## Private files

All API examples use the local `.env` template; no neighboring research project
is used as a credential source. `base_url_env` and `api_key_env` name variables,
not literal secrets. The host parser reads KEY=value without shell expansion.
The selected backend determines which fields are used; unset fields are not
permission to fall back to another account or provider.

`.env.network` is separate host routing configuration. It can contain proxy
credentials, must be private and must not enter run snapshots or public logs.
Empty proxy values represent no proxy; change them for your own host. Native
connectivity acceptance is still required. No publication step authorizes a paid
request or creates an account.

## Retained site and task restrictions

This source snapshot retains the study's fail-closed checks:

- Registered task hashes and the USC-II parent identity.
- Evaluator pool shape and numerical source pins.
- `sca2070` SSH alias; owned `/public3/home/sca2070/WORK/Caifeixue` namespace.
- `cfx` job names, one node and 64 allocated tasks; site partition policy.
- Explicit immutable runtime image / evaluator Python and qualification receipt.

The common YAML has placeholder resource locations inside that allowed namespace.
The archive includes historical scientific inputs and qualification evidence,
but not the runtime image, credentials or SSH configuration. An archived PASS
is not authority for a new host; actual research launch should fail until a real
deployment is qualified. Do not create a fake receipt to bypass this gate.

Porting to another institution requires a reviewed site-policy change and
negative isolation tests. This initial release makes the limitation visible;
it does not loosen safety checks merely to make examples launch elsewhere.

## Costs and lifecycle

CPU/time limits are explicit ceilings. There is currently no model-dollar hard
cap in these profiles; API charges can exceed an intended dollar budget even
while CPU usage is low. Search and terminal evaluation have separate accounts.
Worker costs, failures and retries belong to the same run, not free extra work.

`validate` only reads configuration. `prepare` binds inputs and source versions
without starting research. `start` starts that exact prepared run; `submit`
creates another fresh run and starts it. Edit shared tasks only under a new
registered version; do not alter inputs referenced by active experiments.
