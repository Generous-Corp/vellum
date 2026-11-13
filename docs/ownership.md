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
Vellum's independently implemented boundary and, for the units transferred from
Pulp, in `framework/`. The historical Pulp source was
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
in a Vellum-native root.

## Framework source transferred from Pulp

[Decision 0010](../decisions/0010-mirror-extracted-pulp-framework-source.md)
admitted the `mirror/` root, which received validated audio-free Pulp
framework closures by automation from an exact Pulp commit, each file equal to
a recorded Pulp-origin blob. Its amendment ends that scope for the exported
units: one commit renames them from `pulp` to `vellum` and moves them to the
native root `framework/`, keeping the export's layout.

From that commit on:

- **The units in `framework/` are Vellum-native and editable.** Generic work on
  them starts in Vellum and is reviewed like any other Vellum source.
- **Pulp's frozen copies change only by `framework-backport`** from a named,
  immutable Vellum commit, with destination tests.
- **Pulp's copies of the UI units are deleted** when Pulp consumes Vellum. Until
  then they are Pulp's build inputs and keep the `pulp` spelling. The
  foundation, state-core, dataplane and gpu-device-api units stay in Pulp,
  which audio-only builds still need; Pulp's copies of those become ordinary
  Pulp code again, and Vellum's renamed copies evolve independently.

[`provenance/framework/transfer.json`](../provenance/framework/transfer.json)
records the transfer: each Pulp path and blob at the export receipt's commit,
paired with its `framework/` path and blob. The required provenance lane proves
the record against a Pulp checkout, as a pure rename of the transfer commit's
tree. `framework/` has its own content rules: the mirror-edge rules (no audio,
plug-in, host, or product edges), no `pulp::`, `namespace pulp` or `<pulp/`
spelling, no symlinks, and no second copy of its files elsewhere in Vellum. The
units' kept `PULP_*` macros, `pulp-*` target names and plug-in-host prose are a
counted debt in
[`provenance/framework-name-debt.json`](../provenance/framework-name-debt.json)
that may only fall. An edit may change any file's bytes. The file set changes
only through the append-only
[`provenance/framework/ports.json`](../provenance/framework/ports.json): each
row adds or removes one `framework/` path and names the Pulp path and commits
the content came from, or `native` for a file Vellum wrote. The provenance lane
proves the rows append-only and the Pulp commits real against a Pulp checkout,
and the mirror build compiles each added source into the target its row names.
A file with neither a transfer record nor a ledger row fails both. The
JavaScript preludes are generated from Vellum's own sources once any of them
is edited; the build-info header and embedded data stay as Pulp produced them.
`framework/external/` is third-party source, and its bytes are pinned: each
file must hold its transfer-record blob. A third-party update changes the file
and appends an entry (`path`, `from_blob`, `to_blob`, `reason`, `dependency`,
`version`) to
[`provenance/framework/external-updates.json`](../provenance/framework/external-updates.json)
in its own reviewed pull request. The list is append-only, and each entry's
`from_blob` must be the file's current expected blob. An updated file is no longer exempt as
third-party code: the collision census reads it as native, and it may not
declare or name the `vellum` namespace or carry Pulp, plug-in or audio-SDK
names. Native roots outside `framework/` keep the full rules and may
not define or copy the units' namespaces.

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
