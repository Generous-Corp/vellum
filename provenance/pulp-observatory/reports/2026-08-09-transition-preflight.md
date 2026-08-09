# Vellum transition preflight — 2026-08-09

Status: decision report for Phase 1. This report records a disposable
merge-aware reconciliation and the dependency/export boundary. It does not
apply source patches or authorize Pulp consumption.

## Frozen inputs

| Input | Coordinate |
| --- | --- |
| Pulp | `fd3525a5e0f646d04d3e3655996908356dfde851` |
| Vellum | `2c52da01cd4b84c2f47bccad83e81901e7aaa22b` |
| Forge | `94234fd6f41ca9ed0f6c202655c9136cb3eaa633` |
| Pulp observatory cursor | `c131a2a448f591bcb6f99cf23fb914dd2dd45e6f` (read-only; not advanced) |
| Vellum observatory cursor | `2d1d95e96c901e3ccf70f08a00e8bcf55716efdd` (read-only; not advanced) |
| Reconciliation target | `2026-08-09T03:40:00Z`; disposable output `/private/tmp/vellum-phase1-reconcile-20260809.json` |

## Reconciliation result

The disposable observatory found 455 new pending observations. The generated
health report is intentionally `fail` because its pending-event budget is
exceeded; it has no overdue events, coverage gaps, activation blockers, or
release blockers. This is input to classification, not a migration decision.

Merge commits were classified using their first-parent changed paths. The
following are observation counts, not port counts:

| Classification | Observations | Interpretation |
| --- | ---: | --- |
| Any exact active transferred path | 190 | Upper-bound surface intersection; merge and mixed commits are included |
| Exact active path with an existing Pulp change-event file | 134 | Existing Pulp rationale must be retained; no automatic backport |
| Exact active path without a Pulp change-event file | 56 | Unresolved candidate pool; requires reproduction and ownership review |
| No exact active transferred path | 265 | Broad-map related tooling, tests, docs, integration, or unrelated work |
| Observations with an upstream PR reference in the commit subject | 80 | Lineage clues only; PR content still needs source inspection |

The largest classes are importer 130, correctness 96, schema 63, platform 50,
rendering 41, security 44, build 31, and test 0 in the pending Pulp-to-framework
set as classified here. The importer-heavy tail is expected to contain Pulp
capture/exporter and browser/tooling work; it is not evidence that Vellum should
absorb those product surfaces.

### Candidate bounds and grouping

The initial candidate set is 56 exact-path/no-event observations. A defensible
working range is 0–56 observation-level candidates until each group is
reproduced in both repositories. The lower bound is possible because an exact
path can still be a Pulp-specific adaptation, a merge artifact, or a fix whose
framework analogue is already present. The upper bound is deliberately
conservative and must not be treated as 56 patches.

The groups to reproduce and resolve are:

1. canvas/text/gradient/render behavior in `canvas-kernel` and
   `render-skia-dawn`;
2. retained input, view lifecycle, accessibility, and window-host behavior in
   `retained-ui-kernel` and `macos-shell`;
3. screenshot/capture primitives in `capture-primitives`;
4. design-token/anchor/schema behavior in `design-schema-compiler`;
5. Pulp-owned importer, browser capture, MCP, CLI, package, and plugin-host
   integration, which remains outside the framework candidate set unless a
   named Vellum export is proven.

Existing `.github/vellum-change-events/` records are the ownership evidence for
the 134 event-linked observations. They remain Pulp-owned or integration-owned
until an immutable Vellum authority record and a two-repository reproduction
say otherwise. The broad-map 265 are reported separately because shared
contract labels alone do not establish source ownership.

## Dependency and export matrix

| Consumer surface | Vellum at frozen SHA | Pulp/Forge disposition | Target/artifact disposition |
| --- | --- | --- | --- |
| Foundation/runtime and retained UI | Installed CMake targets `vellum-foundation`, `vellum-runtime`, `vellum-component-abi`; JS/TS/JSX authoring package `vellum-ui` | Candidate framework receiver only after the Vellum release gate; Forge remains an application consumer | Vellum’s first installed native proof is macOS 15+ arm64; Pulp’s Linux/Windows/other macOS targets retain their current implementations and tests |
| DesignIR/schema compiler | `vellum-design-ir` package and imported DesignIR support are present; exact package/export contract must be pinned before use | Pulp’s importer/exporter and schema compatibility remain Pulp-owned until a named export is available; Forge’s design generation remains Forge-owned | No cross-target claim; schema fixtures must be byte/payload compatible and clean-machine tested |
| Import CLI | Vellum CLI supports bounded `.pulp.zip`/plugin JSON import and create/build/run flows | Pulp importer tooling is not replaced by a CLI name alone; Forge #99 remains unresolved product work and is not folded into this phase | Release asset must include the CLI and its lookup data; unsupported sources fail closed |
| MCP/agent tooling | No Vellum MCP export was established by the frozen tree inspection | Retain Pulp/Forge MCP and agent tooling; define a Vellum export only if the API is intentionally published | Must be versioned with the SDK if later adopted |
| Browser capture | Vellum has an experimental Wasm/browser runtime proof, not a demonstrated Pulp browser-capture exporter | Pulp browser capture and screenshot evidence remain Pulp-owned | Browser lane is not a native GPU/WebGPU claim; preserve its explicit Canvas2D limitation |
| Renderer and GPU assets | Optional installed `vellum-gpu` uses byte-locked Skia Graphite + Dawn/Metal input; current lock is macOS arm64 and macOS 15 minimum | Do not substitute this for Pulp’s cross-platform Skia/Dawn assets; later consumption requires an explicit backend/asset contract | No Linux or Windows Vellum renderer artifact at this coordinate |
| Package/install/runtime lookup | Release `v0.1.6` publishes `install.sh`, `install_core.py`, checksum/trust/validation manifests, native SDK tarball, and web payload metadata; CMake install exports are present | A future Pulp consumer must use exact release assets and runtime lookup rules, not a source checkout or floating tag | Current release is immutable-enabled but owner enforcement is false; exact signed annotated tag/artifact verification remains required |
| Fixtures/screenshots/clean machine | Native smoke, runtime/install-consumer tests, package tests, release asset checks, and palette-board validation record exist | Pulp and Forge evidence remains separate until a downstream pin is intentionally updated | Every adopted target needs a fixture, install validation, runtime lookup proof, and screenshot/behavior evidence |

## Independent usability and release boundary

Vellum already has a published `v0.1.6` artifact set and a downstream
validation record whose eight evidence-ladder entries are marked passed. That is
strong evidence of independent usability for the bounded macOS arm64 product
lane, but it is not permission to start Pulp consumption. The repository still
describes itself as private, experimental, and exact-pin only; the immutable
release preflight records that immutable-releases are enabled but not enforced
by the owner. The next phase must therefore verify the current release asset
and package contracts at the frozen Vellum authority, then produce the
required tested, packaged, immutable release before the Vellum→Pulp discussion
point.

## Phase 1 recommendation and gate

Proceed with receiver repair and release hardening only for the unresolved
framework-shaped groups above. Do not port from the 455-event count, do not
adopt the 56 upper-bound candidates wholesale, and do not begin Pulp/Forge
consumption in Phase 2–5. Phase 1 is coherent: the authority map separates
framework slices from Pulp-only integration, the candidate uncertainty is
bounded, and every platform has an explicit retain/fail-closed disposition.

The Phase 1 gate is satisfied for planning purposes with this report. The
Vellum→Pulp consumption decision is intentionally deferred until the
independent Vellum release is tested, packaged, immutable, and documented.
