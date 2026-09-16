# Security and private data

This is a research prototype. Do not deploy untrusted agents in a privileged
shared environment without a separate security assessment. Adapter availability
and synthetic isolation tests are not a multi-tenant security guarantee.

Research assets contain agent-written and downloaded code, HTML, archives and
data. Treat these as untrusted evidence. The restore tool verifies and writes
files; it does not execute restored programs or unpack nested research payloads.
Do not execute them on a privileged host merely because their hashes match.
Archive integrity is not proof of program safety or scientific correctness.

Never post API keys, account tokens, SSH material, proxy credentials, native
sessions, private benchmark data or unredacted real transcripts in public issues.
Use synthetic reproductions. Contact the maintainer through an appropriate
private GitHub channel for sensitive reports; do not assume a public issue is
private. If credentials are exposed, revoke/rotate them at their issuer instead
of only deleting a local file or commit.

The runner is designed to keep credentials and evaluator data on the host,
attribute worker activity, reject unregistered identity changes and prevent
duplicate ambiguous remote submissions. These safeguards have limits documented
in the architecture and deployment notes; no security audit certification is claimed.
