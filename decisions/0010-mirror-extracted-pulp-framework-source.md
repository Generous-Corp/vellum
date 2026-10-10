# Decision 0010: mirror extracted Pulp framework source into Vellum

- Date: 2026-10-10
- Status: accepted on merge; supersedes the active-tip clauses of Decisions
  0002 and 0003 and re-records two rejected alternatives of Decision 0001
- Source: no Pulp source is mirrored by this decision. The first export
  records its exact `Generous-Corp/pulp` commit in `provenance/mirror/`.

Vellum's independently written framework is a small fraction of the generic UI
surface that Pulp ships and keeps editing. Re-authoring that surface clean-room
would leave screenshot similarity as the strongest available oracle and would
keep two implementations under maintenance for the whole migration. Both
repositories are MIT-licensed under the same copyright holder; the clean-room
rule of Decisions 0001 and 0003 exists to prevent two editable owners and the
leakage of product assumptions, and this decision keeps both guarantees by
construction instead of by reimplementation.

## Extraction unit and path

Pulp first carves its generic framework into audio-free units inside Pulp. The
unit Vellum receives is a machine-generated closure manifest, not a CMake target
label: every source, header, shader, font, and generated input, the CMake
modules and compile definitions it needs, its public include roots, and its
complete transitive link edges. A clean-configure boundary gate validates each
closure before it may be exported.

Automation exports a validated closure from an exact Pulp commit to the
protected ref `refs/heads/mirror/pulp-framework`, and it lands on `main` only by
merge commit (Decision 0008). Mirrored files live under the `mirror/` root.
`provenance/mirror/` holds the append-only records of each export: the
Pulp-SHA-to-mirror-SHA commit map, the path-rename map, the export receipt, and
`blob-origins.json`, which binds every mirrored blob to the Pulp commit and path
it came from. The blob-to-origin tuple is the primary key of the extraction
ledger. Records are derived from export receipts, never written by hand. A later
export appends records and merges again; it never rewrites `main`.

## One editable owner, one direction

- Pulp remains the only editable source owner of every mirrored unit until that
  unit's ownership flip. Files under `mirror/` are read-only in Vellum: a file
  passes only while its bytes equal a recorded Pulp-origin blob, and every
  record must resolve to that blob at the cited Pulp commit and path. A manual
  edit therefore cannot pass, and Vellum carries no reverse flow into Pulp.
- Promotion readiness is a Vellum release built from the mirror at an exact
  Pulp commit, with all evidence lanes green and an owner-signed promotion
  record. From then on Vellum is the authoritative release of that unit while
  Pulp remains its authoritative source.
- The flip is a later, separately approved Pulp change that consumes the exact
  Vellum package and freezes Pulp's copy of that unit in the same change. Only
  then does Vellum become the editable owner, the export for that unit stops,
  and Pulp's 2026-10-04 temporary ownership exception ends for it. There is no
  synchronization in either direction after the flip.

## Source scopes

Vellum-native roots, as listed in `provenance/active-source-boundary.json`, keep
the Decision 0003 contamination rules unchanged: no `pulp::` namespace, no
`PULP_*` identifiers, no Pulp package or target names, no audio or plug-in SDK
references, and no byte copy of any Pulp-origin blob, whether from the
historical seed or from the mirror. Vellum-native code consumes mirrored units
only through their exported `Vellum::` identities and never defines `pulp::`
symbols, so a final image that later consumes a mirrored unit carries exactly
one definition of each.

Under `mirror/`, the `pulp::` namespace and `PULP_*` identifiers are permitted
because mirrored bytes stay identical to their origin; renaming them would
destroy the identity oracle before it has done its work. Product edges remain
forbidden there: no mirrored file may include or name Pulp's audio, DSP, MIDI,
music, plug-in format, plug-in host, GPU-audio, or product-content subsystems,
include a plug-in SDK, or link an audio framework. The per-file scan enforces
that now; the closure gate adds the link-level proof when the first closure is
exported. A mirrored graphics unit never links a Pulp-private target, and every
final linked image has exactly one Skia/Dawn provider.

Where a Vellum-native implementation deliberately diverges from a mirrored one
(the Dawn bootstrap, Chromium capture hardening, the `vellum-design-ir` schema),
the divergence is recorded per unit. A mirrored unit's first oracle is byte and
tree identity with its Pulp origin; a divergent unit's oracle is behavioral
differential evidence with negative controls. Similarity metrics are advisory
for both.

## Verifier changes

`tools/provenance/verify_extraction.py` enforces these rules and
`provenance/active-source-boundary.json` declares them:

- `active_files_matching_historical_seed_blobs: 0` becomes
  `native_files_matching_pulp_origin_blobs: 0` and
  `mirror_files_without_matching_origin_record: 0`. With `--pulp-root`, the
  verifier also proves each origin record against the Pulp history it cites.
- `forbidden_namespace_or_product_references: 0` becomes
  `native_forbidden_namespace_or_product_references: 0` and
  `mirror_audio_plugin_host_or_product_edges: 0`.
- `retired_paths_at_active_tip: 0` and `open_extraction_debts: 0` are unchanged.

What this decision does not yet enforce, and therefore what still blocks the
first export from landing:

- tree equality between `mirror/` and the Pulp closure at the recorded commit
  after the rename map, which needs the closure manifest and export receipt
  formats;
- the clean-configure link-closure gate for mirrored CMake;
- SDK payload scanning: `scripts/verify_sdk_artifact.py` still rejects any
  `pulp`-named payload, so no mirrored unit can ship in a Vellum SDK until that
  scan is scoped the same way.

## Relationship to earlier decisions

Decision 0001's rejection of "a subtree, mirror, or bidirectional
synchronization mechanism" stands for its stated reason, two editable owners;
this mirror has one owner, one direction, and automation-only writes. Its
rejection of "making Pulp consume Vellum as the initial proof" also stands:
Pulp's opt-in, release-ineligible `Vellum::Gpu` seam predates this decision and
is not evidence of independent product value.

Decision 0002 required any GPU implementation to arrive as Vellum-owned source
with Vellum public identities. Mirrored units arrive as Pulp-origin source with
exported `Vellum::` package identities; their internal namespace is renamed only
after the flip, in one mechanical commit per unit with forwarding headers.

Decision 0003's retirement of the filtered seed is unchanged: no retired prefix
returns, and the historical seed stays byte-locked. Its rule that the active
tip holds no copy of a historical blob is narrowed to native roots, and its
contamination scan is scoped as described above.

This decision does not change Vellum's authority records, the observatory, or
Pulp's change-event contract. Reconciling the per-slice authority activated on
2026-07-24 with mirrored-unit ownership is a separate, owner-acknowledged
record.

Rejected alternatives:

- clean-room reimplementation of the whole generic surface, which has no oracle
  stronger than screenshot similarity and keeps two implementations maintained;
- a squash or snapshot copy, which loses the commit map the identity oracle
  needs;
- renaming `pulp::` to a Vellum namespace during extraction, which destroys byte
  identity before it has served as the oracle;
- a bidirectional flow, or a Vellum-to-Pulp backport before the flip, which
  creates two editable owners.
