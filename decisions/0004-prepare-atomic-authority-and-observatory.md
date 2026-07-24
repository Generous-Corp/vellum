# Decision 0004: prepare a two-identity authority handoff and observatory

- Date: 2026-07-22
- Status: accepted; activated 2026-07-24

The preserved filtered seed and the active Vellum implementation are different
identities and must remain different. The seed commit proves exact Pulp
path/blob history. A later immutable authority-start commit proves the active,
audio-free Vellum implementation. The seed must be an ancestor of that commit,
but the active tree must not restore the retired Pulp paths or their exact
source blobs.

An activation record therefore carries both identities. Each authority group
binds the exact Pulp source projection from the cut manifest to the exact Vellum
implementation projection at the authority-start commit. Its lineage mode is
`history-seed-ancestor-active-reimplementation`; it does not claim that the
renamed Vellum files are byte-for-byte transferred source.

Activation remains a coordinated external operation. It requires a protected
immutable Vellum authority ref, successful trusted checks on that exact commit,
one landed Pulp ownership transition, successful Pulp freeze checks on that
exact landed commit, and an observatory reconciliation acknowledgement. Until
those facts exist, all committed authority state remains `prepared` and Pulp's
candidate slices remain authoritative and unfrozen.

The observatory records changes in both directions and never applies patches.
Observations and later resolutions are append-only. A cursor advances only when
every mapped commit is represented by an event. The workflow is intentionally
not a mirror, subtree, source synchronizer, or second editable framework copy.

The required facts were later satisfied by Vellum record
`a106a02816a0cde53daac83f36a6630d664f6637`, landed Pulp activation
`28d74338ff57e91bb5690308ec9502ebf2fcf09d`, and the durable Vellum
reconciliation. The conditional preparation rules above remain historical
constraints on that completed activation and any future authority transition.
