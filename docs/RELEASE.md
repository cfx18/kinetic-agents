# Private code and results-only release

This snapshot combines the independent Kinetic Agents code with its scientific
inputs, run summaries, submitted mechanisms, dated observations, results,
figures and reports. It does not include the entire legacy AgenticRL workspace.

## One result package; raw logs retained locally

Git contains code, task/data inputs, submitted mechanisms, independent per-case
results, reports and figures. Failed/cancelled/incomplete runs remain indexed.
This edition supplies no raw model/tool logs, process databases, search
intermediates or full-run Release assets. Originals are retained locally and
are not replaced with synthetic records. See `ARCHIVE_SCOPE.md`.

The result index records file hashes, copy/generation provenance and scope.
Hashes establish consistency of this export,
not retroactive tamper-proof history. Original experiment source and task
identities remain intact; release-oriented documentation is separate.

## Not included

Credentials, native account stores, SSH material, account cookies, proxy
passwords, unrelated conversations/projects, temporary locks and rebuildable
dependency caches are excluded. Historical final evaluator-source packages are
retained to identify scoring code. Dependencies and full search workspaces are
not supplied, so exact Agent replay and a complete process/cost audit are not
claimed. Native session or research databases are not exported in this edition.

No export can recover unrecorded tool output, private service internals or
truncated context. Running experiments are labelled as nonterminal snapshots;
files copied over a time interval are not an atomic checkpoint of the whole run.
Historical reports retain their original evidence cutoff even if later run
metadata show additional completed evaluation.

## License and deployment boundaries

MIT covers the original project code only. Data, mechanisms, downloaded papers,
websites and third-party dependencies retain their source terms; Private does
not relicense them. A Public release requires a new rights/privacy review.
Evaluator answers and previous runs must not be exposed to new search actors.

The code retains its site-specific Slurm and numerical identity checks. Sharing
an archive does not qualify a new cluster, renew an old qualification receipt,
approve model spending or substantiate RSI claims.

`MANIFEST.in` intentionally excludes research data from Python wheel/sdist
packages. This differs from this Git results archive, which includes them.
CI is manual (`workflow_dispatch`) in the initial private archive, so uploading
the repository does not automatically start research or a CI workflow.
