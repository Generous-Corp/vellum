# `@vellum/design-ir`

Experimental, dependency-free canonical DesignIR and safe reimport primitives for Vellum.

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

The package also installs `vellum-backend`, the filesystem adapter used by the
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

## Current limits

- This is an overlay-aware reimport engine, not a general source-code three-way merger.
- The filesystem backend accepts adapter source-model or canonical DesignIR
  JSON. Provider-specific archive decoding and aggregate multi-source
  composition remain separate layers.
- Heuristic candidate scoring is intentionally conservative and never mutates aliases.
- The compact FNV fingerprint is deterministic identity evidence, not a cryptographic content hash.
