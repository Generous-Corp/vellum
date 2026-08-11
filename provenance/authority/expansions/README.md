# Later authority expansions

The initial `native-design-kernel-v1` activation is immutable. Later authority
expansions use a separate append-only protocol and never rewrite its record,
transfer plan, event, or reconciliation coordinates.

An expansion has two deliberately separate handshakes:

1. **Watch handshake.** Vellum merges a proposal at an exact commit. Pulp then
   merges an acceptance that binds that commit and the proposal file digest and
   installs fail-closed watch coverage. Vellum finally merges an acknowledgement
   that binds the exact Pulp acceptance commit and file digest. This handshake
   identifies candidate capability families and catches drift. It transfers no
   source authority and authorizes no source implementation.
2. **Exact-boundary handshake.** After a reviewed compatibility matrix names
   concrete paths and retained seams, Vellum proposes an exact amendment, Pulp
   accepts it in its ownership projection, and Vellum acknowledges the landed
   acceptance. Only a fully acknowledged exact amendment can authorize source
   work for its named paths.

The proposal file is immutable in state `proposed`; it never advances in place.
Acceptance, acknowledgement, and exact-boundary transitions are separate,
append-only counterpart artifacts with their own validators and commit/digest
bindings.

Every counterpart edge binds both a full Git commit and the SHA-256 digest of
the counterpart artifact. This proposal's selectors and deliberate retained
boundary overlaps are pinned exactly. A missing commit, digest mismatch,
selector drift, malformed state transition, or attempt to claim authority from
a watch artifact fails closed. The later exact-boundary validator must also
reject every concrete path overlap not named by its approved-overlap rows.

The current proposal is
[`full-design-import-render-v1/proposal.json`](full-design-import-render-v1/proposal.json).
Its broad selectors are watch scope only. They are intentionally not an exact
path list, do not alter `transfer-plan.v2.json`, and do not change the active
ownership state. The compatibility matrix and exact-boundary amendment are
mandatory before Chromium, renderer, importer, or visual-harness source work
begins under this expansion.

This proposal is deliberately not a source-change authorization mechanism.
Broad Vellum target roots remain ordinary active-authority roots, and this
artifact neither freezes them nor attempts to classify diffs as expansion work.
Pulp installs enforceable drift coverage when it accepts the watch proposal
through its trusted freeze path. Expansion source work remains a project stop
until the exact-path handshake lands; that later concrete path set is what can
support deterministic source routing without freezing unrelated Vellum work or
trusting contributor-authored dispositions.

The proposal also distinguishes the canonical authority repository
`Generous-Corp/vellum` from the temporary private work repository
`danielraffel/vellum`. The watch handshake may proceed in the latter to avoid
the current artifact-billing block, but an exact-boundary amendment must resolve
and record the permanent authority location rather than silently changing it.
