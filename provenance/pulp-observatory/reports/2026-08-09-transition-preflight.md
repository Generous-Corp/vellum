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
of `.github/vellum-change-events/` files. The complete 56-commit classification
is listed below, so every result can be checked from the frozen repositories
without relying on the disposable path.

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

### Candidate identity, grouping, and bounds

The 56 exact-path/no-event observations reduce to five behavior-review groups,
25 retained Pulp integrations, and five merge envelopes. All 51 non-merge
observations have unique stable patch IDs, and none equals any of the 49 patch
IDs already committed in the Vellum observatory. Therefore there is no
patch-identical change to apply automatically.

| Disposition | Count | Exact Pulp commits |
| --- | ---: | --- |
| Rendering/text review group | 15 | `1c250350`, `309ef9a0`, `401f39b3`, `4539d86b`, `75e3bdf3`, `7620cd1e`, `929941df`, `99adf702`, `9b6fd0ab`, `a6b2dfc0`, `bd125a55`, `ca8ed254`, `e006d085`, `e7728265`, `ed3c1d03` |
| UI/input/lifetime review group | 6 | `6bbd2abb`, `6e17787a`, `8344c6f0`, `8d7c6eac`, `a4485d5b`, `d42b3675` |
| DesignIR attributed/mixed typography review group | 2 | `26176987`, `66095906` |
| Capture review group | 2 | `083ae5ac`, `ff2d3b39` |
| macOS application-shell command review group | 1 | `f606dcd5` |
| Retained Pulp product/platform/tooling integration | 25 | `0c5d5cdd`, `15ac8fd6`, `1e214139`, `2cf5e7ae`, `48d5a025`, `5256c64e`, `6dc37872`, `708b8e5a`, `7dd61fec`, `86491a90`, `8d825d50`, `90b6833c`, `a61eb70b`, `ac6f1e7e`, `b137291d`, `b30444fb`, `b443f225`, `bcf508e7`, `c623ce67`, `c8f638d9`, `ca0b1356`, `d563ec70`, `e8996848`, `f9768392`, `fd0b3974` |
| Merge envelopes, resolved through constituent commits | 5 | `024d44c6`, `5048a9e7`, `59c0917a`, `659577a3`, `94c8311d` |

The 26 observations are review inputs, not a shared or selected set. The
behavior-level comparison below resolves eight to an existing independent
Vellum contract or a Pulp-only concern, and excludes the cross-platform
timestamp/adaptor observation `75e3bdf3`. The preliminary convergence set is
therefore 17 observations feeding eight behavior slices: text
family/weight/runs/fallback, bounded text geometry, gradients/effects/shadows,
path fill rules, image assets, GPU frame outcome, cacheable layers, and
application commands. Those are acceptance slices, not patches; their expected
implementation range is eight to twelve Vellum-native changes because the text
and effects slices may need separate schema, materializer, renderer, and asset
work. Allow 6–10 working days for convergence and release hardening rather than
treating the 17 observations as a day or commit count.

The retained set includes Pulp's inspector, audio/control widgets, real-time
telemetry, browser-capture oracle, Yoga adapter, plug-in host/editor lifecycle,
Windows compatibility, musical typing, and Pulp-only docs. Those do not become
Vellum work merely because a mixed commit touched a transferred path.

Existing `.github/vellum-change-events/` records are the ownership evidence for
the 134 event-linked observations. They remain Pulp-owned or integration-owned
until an immutable Vellum authority record and a two-repository reproduction
say otherwise. The broad-map 265 are reported separately because shared
contract labels alone do not establish source ownership.

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
| Text and geometry: `1c250350`, `401f39b3`, `7620cd1e`, `ed3c1d03` | `pulp-test-canvas-fonts` and `pulp-test-canvas` prove fallback baseline, weight-sensitive metrics, geometry rejection, and packaged family weights | `vellum.rendered-tree-materializer` passes, but `SceneNode` exposes only text/font-size; the renderer hard-codes normal Helvetica Neue and direct `drawString`; materialization has a lower font-size clamp but no safe upper bound | **Affected as missing/unsafe capability.** Select one schema-to-renderer text slice with explicit fallback, family, weight, runs, and bounded geometry fixtures. |
| Effects, paths, and layers: `309ef9a0`, `4539d86b`, `9b6fd0ab`, `a6b2dfc0`, `bd125a55`, `ca8ed254`, `e006d085`, `e7728265` | CSS gradient geometry/render, canvas shadow, canvas capability, and Skia surface fixtures prove crop, repeat, angle/unit, spread/radius, fill-rule, and retained-layer behavior | `SceneNode`, paint commands, and the native materializer expose none of gradient, shadow/backdrop, path/fill-rule, or cacheable-layer inputs; `vellum.paint-command` passes only the bounded fill/rounded-rect/text contract | **Affected as missing capability.** Select effects/path/layer slices; each needs a public field, materializer rejection/acceptance fixture, and pixel proof rather than a copied Pulp patch. |
| Image upload: `929941df` | `pulp-test-headless-surface` proves file, SVG, and SVG-embedded raster pixels reach Graphite | Vellum has no image/asset scene kind and its Graphite recorder installs no image provider | **Not the same current defect, but a required missing export.** Select image assets plus Graphite upload/cache proof before claiming renderer parity. |
| GPU submission: `99adf702`, `75e3bdf3` | Pulp frame-outcome/lifecycle fixtures distinguish an inserted/presented frame from offscreen success and prove the Dawn feature/toggle policy | Vellum fails closed on acquire/wrap but ignores `insertRecording` status, ignores submit outcome, and calls `Present`; it does not request `TimestampQuery`, supports only Metal, and has no plug-in surface lifecycle | **Partly affected.** Select only generic exact frame-outcome/submission proof from `99adf702`. Exclude `75e3bdf3` and all plug-in/adaptor lifecycle as Pulp platform integration. |
| Gesture handoff: `6bbd2abb`, `6e17787a`, `8344c6f0`, `a4485d5b` | Gesture-recognizer, pointer-delivery, and macOS platform-harness fixtures prove candidate-versus-claim handoff and balanced close | Vellum has stable-id press/touch dispatch but no recognizer, arbiter, drag handoff, or plug-in host API; its Phase 3 fixture proves only its independent direct-dispatch contract | **Not affected.** Retain in Pulp; do not create a Vellum gesture system from these observations. |
| Button hit target: `8d7c6eac` | `pulp-test-buttons` proves decorative children do not steal the button hit | Vellum materialization records the press interaction on the button bounds, generates a text child with no interaction, and the runtime/Phase 3 fixtures dispatch a text-bearing button successfully | **Existing independent equivalent.** No port; retain the Vellum runtime and native scenario fixtures as the counterpart. |
| Runtime retirement: `d42b3675` | Pointer-focus, view-pool, scripted-UI, widget-removal-lifetime, and platform fixtures prove Pulp view/bridge/accessibility retirement | Vellum runtime tests prove failed render rollback, keyed reorder state, intentional replacement, reentrancy rejection, and incompatible-state rejection; Vellum has no Pulp accessibility provider, drag/drop session, plug-in router, or widget bridge | **Equivalent only for Vellum's narrower state lifetime; otherwise Pulp-only.** No port from this observation. |
| Mixed typography: `26176987`, `66095906` | Pulp DesignIR/import/materializer/text fixtures prove per-run font, color, tracking, decoration, and geometry through native output | `@vellum/design-ir` preserves the data (27/27), but native materialization concatenates text runs and emits only one font size/color | **Affected schema-to-runtime gap.** Select attributed-run materialization and renderer fixtures; retain Pulp provider/browser capture services. |
| Capture: `083ae5ac`, `ff2d3b39` | Browser-capture guard and offscreen RT contract distinguish unavailable backends, empty output, and allocation suspension | `vellum.capture-stats` rejects blank/sparse output; GPU capture requires a successfully submitted scene, fails on empty pixels/PNG, and unsupported artifacts expose capture as unavailable. Vellum has no audio-thread/no-allocation contract | **Existing fail-closed equivalent for `083ae5ac`; not affected by `ff2d3b39`.** Keep Vellum capture evidence and retain Pulp's RT contract. |
| Application command: `f606dcd5` | `pulp-test-app-menu-macos` proves app-menu placement, separator/Quit ordering, key equivalent, and action dispatch | Vellum has a native app shell but no public command/menu export and therefore no fixture capable of expressing the behavior | **Affected as missing app-shell capability.** Select a platform-neutral command model plus macOS placement/dispatch proof; do not copy musical-typing product code. |

This resolves every observation in the five review groups to selected,
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
against the eight preliminary convergence slices above. Do not port from the
455-event count, do not apply the 17 selected observations as patches, and do
not begin Pulp/Forge consumption in Phase 2–5. Phase 1 is coherent: the
authority map separates framework slices from Pulp-only integration, the
candidate uncertainty is bounded, Forge has an itemized dependency
disposition, and every platform has an explicit retain/fail-closed path.

The Phase 1 gate is satisfied for planning purposes with this report. The
Vellum→Pulp consumption decision is intentionally deferred until the
independent Vellum release is tested, packaged, immutable, and documented.
