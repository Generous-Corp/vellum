# `@vellum/design-ir`

Experimental, dependency-free canonical DesignIR and safe reimport primitives for Vellum.

The `decodeFigmaPluginExport()` entry point accepts the pinned single-root,
audio-free Pulp Figma plugin envelope (format `2026.05-figma-plugin-v1`, parser
`0.1.0`, compatibility schema `0.3`), preserves provider identities and source
evidence, and rejects audio-widget fields before normalization. The `frame` and
`text` node kinds map directly into the native host, but unsupported fields on
those nodes are still preserved and diagnosed. Current Pulp `ellipse` output
lowers to a rounded view; images, vectors, legacy compatibility shapes, and
unmaterialized fields carry explicit diagnostics and remain inspectable in
extensions. Pulp's bare 64-hex asset hashes normalize to canonical
`sha256:<hex>`. The package does not call Figma, accept arbitrary provider
files, or parse ZIP containers itself. The installed Python dispatcher
validates and stages supported `.pulp.zip` inputs, then supplies this backend
with the scene/assets tree and an archive SHA-256 receipt.

The package owns only generated design data and the algorithm that composes it with developer-owned overlays. It deliberately keeps behavior out of generated files:

- immutable source snapshots are decoded by an adapter;
- `normalizeImport()` writes versioned, inspectable DesignIR with namespaced stable identities, tokens, assets, diagnostics, and a loss report;
- bindings, reviewed aliases, semantic token names, theme overrides, and structured property overrides live in a separate authored overlay;
- `reimportDesign()` compares revisions and applies that overlay without modifying either input;
- exact identities and reviewed aliases resolve automatically; heuristic candidates require review;
- a removed identity referenced by authored work, an alias cycle, or a new conversion loss blocks acceptance.

There are no runtime, filesystem, compiler, renderer, provider, or platform dependencies. Node filesystem access exists only in the small CLI.

## Library

```js
import {
  normalizeImport,
  reimportDesign,
  stableStringify,
} from '@vellum/design-ir';

const revisionA = normalizeImport(adapterSourceA);
const revisionB = normalizeImport(adapterSourceB);
const result = reimportDesign(revisionA, revisionB, authoredOverlay);

if (!result.accepted) {
  console.error(stableStringify(result.report));
  process.exit(2);
}
```

The source model passed to `normalizeImport()` is intentionally adapter-neutral. Each node has a `kind`, optional provider `sourceId` or explicit `semanticId`, generic normalized `properties`, and `children`. Adapter-specific fields are preserved under the `dev.vellum.import.unrecognized.v1` extension and diagnosed instead of silently discarded.

## CLI

```sh
vellum-design-ir normalize --input source.json --output designir.json
vellum-design-ir inspect --input designir.json
vellum-design-ir reimport \
  --previous revision-a.designir.json \
  --next revision-b.designir.json \
  --overlay authored.overlay.json \
  --output result.json
```

All machine output uses recursively sorted JSON object keys and a trailing newline. Reimport exits `0` when accepted, `2` when a valid report contains unresolved authored conflicts, and `1` for invalid input.

The package also installs `vellum-import-backend`, the filesystem adapter used by the
public Python CLI. It snapshots source bytes and assets, writes normalized IR,
tokens, generated component definitions, typed IDs, diagnostics, and the import
lock, and performs overlay-preserving reimport transactions. See
[`docs/cli/import-reimport.md`](../../docs/cli/import-reimport.md).

## Ownership contract

| Data | Owner | Reimport may rewrite it? |
| --- | --- | --- |
| DesignIR, imported primitive tokens, assets, diagnostics | Tool | Yes, deterministically |
| Authored bindings and action modules | Developer | No |
| Reviewed identity aliases | Developer | No |
| Semantic token names and theme overrides | Developer | No |
| Materialized overlay result | Build output | Regenerated, never edited |

Overrides may address only `properties.*`. They cannot rewrite `id`, identity metadata, children, or the canonical generated tree. Application behavior remains in developer-owned modules referenced by bindings; generated implementation files never become an authoring surface.

## Validation

```sh
npm test
npm run test:sterile
npm run check
```

The sterile-consumer check packs the package, installs the tarball offline into an empty npm project, imports the public API, and normalizes the checked-in fixture without a Vellum or Pulp checkout.

The package includes three public JSON Schemas:

- `@vellum/design-ir/schema/design-ir-v1`
- `@vellum/design-ir/schema/authored-overlay-v1`
- `@vellum/design-ir/schema/reimport-report-v1`

## Compositing

A node can carry a `compositing` object that mirrors the renderer's scene node:
`opacity` from 0 to 1, a `blendMode`, `isolate`, a `mask` mode (`alpha` or
`luminance`) that makes the node a mask for its siblings, and `maskRef`, the
identity of the sibling mask that applies to it. Blend modes are named as the
renderer spells them (`multiply`, `color_dodge`, `destination_in`, and so on).
The first eleven, `normal` through `destination_out`, are Tier 1 and required of
every renderer; `BLEND_MODES_TIER_2` lists the rest, which a renderer may lack
and then draws as `normal` with a loss diagnostic. `blendModeTier(mode)` returns
the tier.

The object adds schema version 2. `normalizeImport` emits version 2 only when a
node uses `compositing`, and only the values that differ from their defaults, so
other documents stay at version 1. A version 1 document that carries the object
is refused, as is an unknown field, an out-of-range opacity, an unknown blend
mode, a root with a mask, or a mask reference that does not name a sibling mask.

## Audio/control import contract

The package exports `validateAudioControlMap()` and the public schema
`@vellum/design-ir/schema/audio-control-map-v1`. Importers may attach this
renderer-neutral document to a product handoff. It describes stable control IDs,
parameter ranges, optional LFO ranges, and host-owned audio input kinds
(`main`, `sidechain`, `physical-input`, `project-track`, and `project-bus`).
Vellum owns materialization and host catalog resolution; product code owns DSP
behavior. The contract does not enumerate DAW tracks itself and rejects duplicate
IDs or invalid ranges before rendering.

## Current limits

- This is an overlay-aware reimport engine, not a general source-code three-way merger.
- The filesystem backend accepts adapter source-model or canonical DesignIR
  JSON. Provider-specific archive decoding and aggregate multi-source
  composition remain separate layers.
- Heuristic candidate scoring is intentionally conservative and never mutates aliases.
- The compact FNV fingerprint is deterministic identity evidence, not a cryptographic content hash.
