# Source ownership

Vellum is an independent-product experiment. Pulp does not consume it. The
filtered projection used to preserve lineage has been retired from Vellum's
active tip; it remains reachable in Git history and is described by immutable
records under `provenance/`.

The machine-readable current boundary is
[`provenance/ownership-map.yaml`](../provenance/ownership-map.yaml), and the
source scan policy is
[`provenance/active-source-boundary.json`](../provenance/active-source-boundary.json).
Vellum-owned code is explicitly classified
`framework-reimplemented-no-transfer`. Matching Pulp code has not transferred,
is not frozen, and is not maintained as a synchronized copy here.

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

## Independent ownership

During independent validation, changes to Vellum-owned modules originate in
Vellum. Pulp continues to own and evolve its implementation independently. The
observatory cursor can record later Pulp changes for evaluation, but it is not
a synchronization mechanism and does not create shared ownership.

If Vellum fails product validation, no transfer or reversal is necessary: the
active implementations were never shared. If Vellum succeeds, Pulp adoption is
a separate consumer migration. It should replace selected Pulp implementation
with a versioned Vellum dependency and explicit Pulp-only adapters, not merge
two long-lived editable copies.

## Later Pulp adoption gate

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

No authority-transfer handshake is active at this checkpoint.
