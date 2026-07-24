# Source ownership

Vellum is an independent-product experiment. Pulp does not consume it. The
filtered projection used to preserve lineage has been retired from Vellum's
active tip; it remains reachable in Git history and is described by immutable
records under `provenance/`.

The machine-readable current boundary is
[`provenance/ownership-map.yaml`](../provenance/ownership-map.yaml), and the
source scan policy is
[`provenance/active-source-boundary.json`](../provenance/active-source-boundary.json).
Source authority for the selected mapped framework slices is active in
Vellum's independently implemented boundary. The historical Pulp source was
not restored or copied into the active Vellum tree. Pulp's matching transferred
paths remain in Pulp under its freeze and change-event contract. Vellum-only
surfaces with no transferred counterpart, including the CLI, remain explicitly
classified `framework-reimplemented-no-transfer`.

## What was retired

The active tip contains none of these historical projection prefixes:

- `core/`
- `external/fonts/`
- `external/nanosvg/`
- `packages/pulp-import-ir/`
- `tools/figma-plugin/`

The normal Git deletion is recoverable, preserves authorship, and leaves the
filtered seed commit and byte-locked cut manifest intact. The provenance
verifier checks both facts: historical blobs still match the manifest at the
seed commit, while no retired path or exact historical source blob is present
in the active framework surface.

## Active ownership and change routing

Generic changes to transferred framework slices originate in Vellum. Changes
to matching Pulp paths require a declared Pulp disposition: `pulp-only`,
`framework-backport` of a named immutable Vellum commit, or a time-bounded
`emergency-exception`. Pulp continues to own its audio, plug-in, host, legacy
integration, and other explicitly Pulp-only surfaces.

The observatory records later changes in both repositories for evaluation and
can identify a candidate fix that should move from Pulp to Vellum or from
Vellum to Pulp. It is not a synchronization mechanism, never applies patches,
and does not create shared ownership.

Validated independent applications remain separate consumers. The
[downstream-consumer registry](../provenance/downstream-consumers.v1.json)
pins each proof to a full consumer commit, immutable Vellum release tuple, and
evidence digest. Its framework-first protocol requires reusable defects to be
fixed and released in Vellum before the consumer updates its pin and reruns the
evidence ladder; an application-side workaround requires an explicit,
time-bounded exception record.

If Vellum is abandoned, authority does not silently return to Pulp. A reviewed
ownership-reversal change must update both repositories and define the
resulting ownership and support boundary. Pulp dependency adoption remains a
separate consumer migration. It should replace selected Pulp implementation
with a versioned Vellum dependency and explicit Pulp-only adapters, not merge
two long-lived editable copies.

## Pulp dependency-adoption gate

Before Pulp can consume Vellum:

1. Vellum must pass its independent application, import/reimport, native GPU,
   test, and packaging evidence gates.
2. The exact Pulp-to-Vellum API and adapter boundary must be reviewed.
3. Changes observed in Pulp since extraction must be classified as Vellum
   fixes, Pulp-only behavior, or intentionally obsolete behavior.
   The pinned [Pulp tooling-disposition observation](provenance/pulp-tooling-disposition.md)
   is the machine-readable baseline for commands, flags, skills, MCP tools,
   and plugin registrations; no entry may disappear implicitly.
4. Pulp must migrate in a bounded change to an immutable Vellum version.
5. Both repositories must record the dependency and ownership transition.

Source authority was activated on 2026-07-24 by Vellum record
`a106a02816a0cde53daac83f36a6630d664f6637` and landed Pulp commit
`28d74338ff57e91bb5690308ec9502ebf2fcf09d`. The completed fail-closed
protocol and its recovery procedure are documented in
[`provenance/authority/README.md`](../provenance/authority/README.md), and the
two-way, non-synchronizing change ledger is documented in
[`provenance/pulp-observatory/README.md`](../provenance/pulp-observatory/README.md).
The protocol distinguishes the exact historical seed from a later evolved
authority-start commit; it never requires the retired Pulp source projection to
be restored at Vellum's active tip.
