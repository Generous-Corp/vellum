# Vellum transition preflight — 2026-08-09

Status: decision report for Phase 1. This report records a disposable
merge-aware reconciliation and the dependency/export boundary. It does not
apply source patches or authorize Pulp consumption.

Correction (Phase 3): the initial path helper combined `-m` with
`--first-parent`; Git emitted one diff per merge parent despite the latter
option. Recomputing every observation as the exact `parent[0]..commit` diff
removes merge-parent propagation from the active-path and event-lineage counts
below. The selected eight non-merge convergence inputs are unchanged.

## Frozen inputs

| Input | Coordinate |
| --- | --- |
| Pulp | `fd3525a5e0f646d04d3e3655996908356dfde851` |
| Vellum | `2c52da01cd4b84c2f47bccad83e81901e7aaa22b` |
| Forge | `94234fd6f41ca9ed0f6c202655c9136cb3eaa633` |
| Pulp observatory cursor | `c131a2a448f591bcb6f99cf23fb914dd2dd45e6f` (read-only; not advanced) |
| Vellum observatory cursor | `2d1d95e96c901e3ccf70f08a00e8bcf55716efdd` (read-only; not advanced) |
| Reconciliation target | `2026-08-09T03:40:00Z`; disposable output `/private/tmp/vellum-phase1-reconcile-20260809.json` |

Reproduce the generated observation set from clean checkouts at those SHAs:

```sh
python3 tools/provenance/observatory.py reconcile \
  --pulp-repo /path/to/clean-pulp \
  --vellum-repo /path/to/clean-vellum \
  --pulp-target fd3525a5e0f646d04d3e3655996908356dfde851 \
  --vellum-target 2c52da01cd4b84c2f47bccad83e81901e7aaa22b \
  --now 2026-08-09T03:40:00Z \
  --output /path/to/disposable-reconciliation.json
```

Classification used exact paths from Pulp's
`.github/vellum-ownership.json`, first-parent diffs for merge commits, stable
Git patch IDs for non-merges, commit/PR lineage, and the presence and rationale
of `.github/vellum-change-events/` files. The complete corrected 23-commit
unresolved classification and the 32-commit PR/event correction are listed
below, so every result can be checked from the frozen repositories without
relying on the disposable path.

## Reconciliation result

The disposable observatory found 455 new pending observations. The generated
health report is intentionally `fail` because its pending-event budget is
exceeded; it has no overdue events, coverage gaps, activation blockers, or
release blockers. This is input to classification, not a migration decision.

Merge commits were classified using their first-parent changed paths. The
following are observation counts, not port counts:

| Classification | Observations | Interpretation |
| --- | ---: | --- |
| Any exact active transferred path | 165 | Corrected first-parent surface intersection; merge-parent propagation is excluded |
| Exact active path covered by an existing Pulp change-event file | 142 | 110 observations carry the event in the same first-parent diff; 32 more share the event's PR/merge lineage. Existing Pulp rationale must be retained; no automatic backport |
| Exact active path without a Pulp change-event in its PR/merge lineage | 23 | Unresolved candidate pool; requires reproduction and ownership review |
| No exact active transferred path | 290 | Broad-map related tooling, tests, docs, merge-parent propagation, integration, or unrelated work |
| Observations with an upstream PR reference in the commit subject | 80 | Lineage clues only; PR content still needs source inspection |

The largest classes are importer 130, correctness 96, schema 63, platform 50,
rendering 41, security 44, build 31, and test 0 in the pending Pulp-to-framework
set as classified here. The importer-heavy tail is expected to contain Pulp
capture/exporter and browser/tooling work; it is not evidence that Vellum should
absorb those product surfaces.

### Candidate identity, grouping, and bounds

The first pass associated a change event only when its file was added in the
same individual commit as an observation. That is not the freeze contract's
unit: the gate compares the PR's proposed merge and permits the source changes
and their immutable event to be separate commits in that PR. Joining both
sides through GitHub's commit-to-PR lineage removes 32 false candidates,
including `f606dcd5`, `309ef9a0`, `7620cd1e`, and `99adf702`, whose PRs contain
the matching `pulp-only` event records.

| Pulp PR | Existing event IDs | Observations removed from the no-event pool |
| --- | --- | --- |
| `#6557` | `20260725-gesture-claim-vs-candidacy` | `6e17787a`, `8d7c6eac`, `a4485d5b` |
| `#6559` | `20260726-windows-editor-retained-rendering` | `75e3bdf3`, `ac6f1e7e`, `e7728265` |
| `#6563` | `20260725-windows-plugin-editor-present-mode` | `48d5a025` |
| `#6565` | `20260725-offscreen-capture-no-alloc-scope` | `ff2d3b39` |
| `#6583` | `20260726-mac-gesture-claim-host-parity` | `6bbd2abb`, `8344c6f0`, `fd0b3974` |
| `#6637` | `20260726-windows-editor-input-dispatch` | `6dc37872`, `a61eb70b`, `bcf508e7` |
| `#6670` | `20260727-standalone-musical-typing` | `ca0b1356`, `f9768392` |
| `#6682` | `20260728-visible-frame-outcome-and-damage` | `99adf702` |
| `#6717` | `20260727-menu-command-application-menu` | `f606dcd5` |
| `#6790` | `20260729-text-geometry-finite-guard` | `7620cd1e` |
| `#6811` | `20260729-yoga-non-finite-measurement` | `7dd61fec` |
| `#6821` | `20260729-backdrop-filter-crop`, `20260729-design-accent-widget-tokens` | `15ac8fd6`, `309ef9a0` |
| `#6825` | `20260729-browser-solved-html-source` | `8d825d50` |
| `#7005` | `20260801-native-design-render-styles` | `024d44c6`, `659577a3`, `708b8e5a`, `90b6833c`, `929941df`, `a6b2dfc0`, `bd125a55`, `ca8ed254`, `e8996848` |

The corrected 23 exact-path/no-event observations reduce to four
behavior-review groups, 11 retained Pulp integrations, and two merge envelopes.
All 21 non-merge observations have unique stable patch IDs, and none equals any
of the 49 patch IDs already committed in the Vellum observatory. Therefore
there is no patch-identical change to apply automatically.

| Disposition | Count | Exact Pulp commits |
| --- | ---: | --- |
| Rendering/text review group | 6 | `1c250350`, `401f39b3`, `4539d86b`, `9b6fd0ab`, `e006d085`, `ed3c1d03` |
| UI/input/lifetime review group | 1 | `d42b3675` |
| DesignIR attributed/mixed typography review group | 2 | `26176987`, `66095906` |
| Capture review group | 1 | `083ae5ac` |
| Retained Pulp product/platform/tooling integration | 11 | `0c5d5cdd`, `1e214139`, `2cf5e7ae`, `5256c64e`, `86491a90`, `b137291d`, `b30444fb`, `b443f225`, `c623ce67`, `c8f638d9`, `d563ec70` |
| Merge envelopes, resolved through constituent commits | 2 | `59c0917a`, `ca414993` |

The ten observations in the four behavior-review groups are review inputs, not
a shared or selected set. The behavior-level comparison below resolves two to
an existing independent Vellum contract or a Pulp-only concern. The preliminary
convergence set is therefore eight observations feeding four behavior slices:
fallback/weight-aware packaged text, repeating and box-resolved gradients,
outset-shadow geometry, and attributed-run materialization. Those are
acceptance slices, not patches; their expected implementation range is four to
eight Vellum-native changes because schema, materializer, renderer, asset, and
fixture work may land separately. Allow 4–7 working days for convergence and
release hardening rather than treating the eight observations as a day or
commit count.

The retained set includes Pulp's inspector, audio/control widgets, real-time
telemetry, browser-capture oracle, Yoga adapter, plug-in host/editor lifecycle,
Windows compatibility, musical typing, and Pulp-only docs. Those do not become
Vellum work merely because a mixed commit touched a transferred path.

Existing `.github/vellum-change-events/` records are PR-level disposition and
rationale evidence for the 142 event-covered observations; they do not transfer
the active slices' source authority back from Vellum. Their `pulp-only` claims
exclude automatic backport, but ledger catch-up must retain and validate each
claim through counterpart reproduction. Only an immutable authority-transition
record can change ownership. The broad-map 290 are reported separately because
shared contract labels alone do not establish source ownership.

### Two-repository reproduction

The Pulp side is proven at the frozen target by the focused source tests named
in the candidate commits and by the green macOS/Linux/Windows run
`31291650879` whose tested head was merged as
`fd3525a5e0f646d04d3e3655996908356dfde851`. A fresh Release/GPU build then ran
`pulp-test-canvas-fonts`, `pulp-test-css-gradient-render`,
`pulp-test-css-gradient-geometry`, `pulp-test-canvas`,
`pulp-test-canvas-capabilities`, `pulp-test-skia-surface`,
`pulp-test-headless-surface`, `pulp-test-gesture-recognizer`,
`pulp-test-buttons`, `pulp-test-app-menu-macos`, and
`pulp-test-offscreen-capture-rt-contract`: 3,288 assertions in 213 cases
passed.

At Vellum `2c52da01`, the independent test surface passed:

- `@vellum/ui`: 59/59;
- `@vellum/design-ir`: 27/27;
- CLI unit suite: 165 run, 164 passed, one intentional skip;
- authoring Phase 3 contracts: 5/5;
- GPU-off Release CMake build and CTest: 15/15, including installed consumer,
  SDK artifact, capture stats, paint command, authoring, materializer,
  provenance, and downstream-registry tests.

The group-level rerun added the tests needed to distinguish shared behavior
from similarly named but Pulp-only code. On Pulp, the gesture, button,
pointer-delivery, pointer-focus, view-pool, scripted-UI,
widget-removal-lifetime, macOS platform-harness, offscreen-capture, and app-menu
executables passed 1,104 assertions across 120 passing cases; two platform
harness cases made their existing explicit skips. On Vellum, the focused
`runtime.test.js` passed 30/30, `@vellum/design-ir` passed 27/27, and the
capture-stats, authoring, Phase 3, service-bridge, and rendered-tree-materializer
CTest subset passed 5/5.

The renderer/DesignIR comparison additionally ran Pulp's canvas-shadow,
plugin-frame-renderer, live GPU frame-outcome, DesignIR native-materializer,
and text-shaper executables: 1,006 assertions in 149 cases passed. These are the
fixtures cited in the rendering and mixed-typography rows below.

The comparison uses an existing executable fixture where both repositories
have the behavior. Where Vellum has no corresponding public input, the negative
contract is its checked-in `SceneNode` plus `rendered_tree_materializer.mm`:
the scene has only group/rectangle/text/custom kinds and bounds, fill, one
corner radius, text, and font size, while the materializer ignores the absent
style rather than implementing it. Absence is an explicit capability result,
not a claim that an unrelated passing test exercised the Pulp behavior.

| Review input | Pulp behavior fixture | Vellum fixture or public contract | Affected result and routing outcome |
| --- | --- | --- | --- |
| Text: `1c250350`, `401f39b3`, `ed3c1d03` | `pulp-test-canvas-fonts` proves fallback runs sit on the requested baseline, measurement uses the painted weight, and packaged families retain their non-regular weights | `vellum.rendered-tree-materializer` passes, but `SceneNode` exposes only text/font-size; the renderer hard-codes normal Helvetica Neue and direct `drawString` | **Affected as missing capability.** Select one schema-to-renderer text slice with explicit family, weight, packaged faces, fallback, baseline, and metric fixtures. |
| Gradients: `4539d86b`, `e006d085` | CSS gradient geometry/render fixtures prove repeating-linear syntax and resolve angle/radii against the painted box | `SceneNode`, paint commands, and the native materializer expose no gradient input; `vellum.paint-command` passes only bounded fill/rounded-rect/text | **Affected as missing capability.** Select a gradient intent/materialization/render slice with repeat and non-square box fixtures. |
| Shadow geometry: `9b6fd0ab` | Canvas shadow fixtures prove an outset shadow grows the corner radius by the full spread | `SceneNode`, paint commands, and the native materializer expose no shadow input | **Affected as missing capability.** Select a bounded shadow schema/render slice with diagonal pixel proof. |
| Runtime retirement: `d42b3675` | Pointer-focus, view-pool, scripted-UI, widget-removal-lifetime, and platform fixtures prove Pulp view/bridge/accessibility retirement | Vellum runtime tests prove failed render rollback, keyed reorder state, intentional replacement, reentrancy rejection, and incompatible-state rejection; Vellum has no Pulp accessibility provider, drag/drop session, plug-in router, or widget bridge | **Equivalent only for Vellum's narrower state lifetime; otherwise Pulp-only.** No port from this observation. |
| Mixed typography: `26176987`, `66095906` | Pulp DesignIR/import/materializer/text fixtures prove per-run font, color, tracking, decoration, and geometry through native output | `@vellum/design-ir` preserves the data (27/27), but native materialization concatenates text runs and emits only one font size/color | **Affected schema-to-runtime gap.** Select attributed-run materialization and renderer fixtures; retain Pulp provider/browser capture services. |
| Capture availability: `083ae5ac` | Browser-capture guards distinguish unsupported builds from failed pixel probes | `vellum.capture-stats` rejects blank/sparse output; GPU capture requires a successfully submitted scene, fails on empty pixels/PNG, and unsupported artifacts expose capture as unavailable | **Existing fail-closed equivalent.** Keep Vellum capture evidence; no port from this observation. |

This resolves every observation in the four review groups to selected,
independently equivalent, or Pulp-only. It satisfies the two-repository
reproduction requirement without claiming API/source identity and without
using broad suite success as evidence for an absent capability.

## Dependency and export matrix

| Consumer surface | Current Pulp/Forge dependency | Vellum at frozen SHA | Disposition and platform proof |
| --- | --- | --- | --- |
| Audio/DSP/product runtime | Forge pins Pulp `b3cd9cf5248dd057059162a7fc92d5690764d35f` and requires `pulp::format`, `pulp::host`, `pulp::signal`, `pulp::state`, effect-catalog headers, and `share/pulp/forge-catalog.json` | No counterpart by design | Retain Pulp on every platform; permanently outside this Vellum boundary |
| Retained UI and scripting | Forge requires `pulp::view`, `pulp::view-script`, `pulp::canvas`; Pulp exports their C++ headers and runtime assets | `Vellum::Runtime`, `Vellum::Authoring`, `Vellum::Graphics`, and `@vellum/ui` exist, but are not API-compatible substitutes | Proposed export/adapter work only after release; Pulp remains default on macOS/Linux/Windows and all mobile/Intel targets |
| DesignIR/schema/compiler | Forge conditionally consumes Pulp C++ `design_codegen.hpp`, `scripted_ui.hpp`, `IRNode`, codegen, bindings, and materialization | `@vellum/design-ir` and its schemas/CLI are existing JS exports; no C++ Forge-facing compiler/materializer export exists | Blocker for Forge adoption. Keep Pulp implementation; byte/payload fixture equivalence is required before any adapter |
| Import CLI and provider intake | Pulp owns `pulp import-design`, Figma exporter, browser/HTML intake, `.pulp.zip`, and helper lookup; Forge #99 is unresolved importer product work | Vellum CLI accepts bounded plugin JSON/`.pulp.zip` and supports import/reimport, but does not replace Pulp capture/provider services | Existing Vellum export only for bounded file import. Retain Pulp provider/capture services; do not mix #99/#100 into convergence |
| MCP/agent tooling | Pulp and Forge own their command/MCP schemas and agent surfaces | No Vellum MCP export established | Intentionally retained Pulp/Forge service on every platform |
| Browser capture and web | Forge/Pulp use Pulp browser capture, screenshot harness, generated assets, and web-compat runtime | Vellum has a Wasm/browser runtime proof with Canvas2D presentation, not a Pulp browser-capture exporter or native WebGPU claim | Retain Pulp capture. Vellum web proof remains separately packaged and fail-closed |
| Native renderer/GPU assets | Forge optionally requires `pulp::render`; Pulp packages cross-platform Skia/Dawn/WGPU assets | Installed `vellum-gpu` links byte-locked Skia Graphite + Dawn/Metal input | Existing Vellum export only for macOS 15+ arm64. Retain Pulp on Linux, Windows, Intel macOS, iOS, Android, and other unproved targets |
| Package/install/runtime lookup | Forge uses `find_package(Pulp CONFIG REQUIRED)`, validates `build_info.hpp`, and requires the installed SDK/catalog; Pulp packaging supplies shared signing/install recipes | Vellum CMake exports, CLI, runtime assets, `install.sh`, `install_core.py`, checksums/trust manifests, SDK tarball, and web payload exist in immutable `v0.1.6` | Future use must pin a signed immutable release and exact artifact hash. No source checkout, floating tag, or developer absolute path is acceptable |
| Fixtures/screenshots/clean machine | Forge has canonical DesignIR, screenshot, interaction, export/package, and product fixtures tied to its Pulp SDK | Vellum has native smoke, install-consumer, package, capture, release-asset, and palette-board evidence | Evidence remains separate until an adoption PR exists. Every adopted target needs install, runtime lookup, fixture, screenshot, interaction, and package proof |

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

Proceed with receiver repair and ledger catch-up, then evaluate Decision A
against the four preliminary convergence slices above. Do not port from the
455-event count, do not apply the eight selected observations as patches, and
do not begin Pulp/Forge consumption in Phase 2–5. Phase 1 is coherent: the
authority map separates framework slices from Pulp-only integration, the
candidate uncertainty is bounded, Forge has an itemized dependency
disposition, and every platform has an explicit retain/fail-closed path.

The Phase 1 gate is satisfied for planning purposes with this report. The
Vellum→Pulp consumption decision is intentionally deferred until the
independent Vellum release is tested, packaged, immutable, and documented.
