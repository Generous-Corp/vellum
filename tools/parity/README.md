# Paired parity harness

`parity_harness.py` runs one versioned corpus through a **reference** and a
**candidate** implementation and writes one receipt that says, lane by lane and
case by case, whether the two produced the same observations. It is the
behavioral half of the proof ladder: identity first, then packaging and host
closure, then these differential lanes, with similarity scores advisory only.

| Lane | Compares | Vacuity guard |
|---|---|---|
| `identity` | exported tree vs origin closure, git blob by blob | empty manifest or empty origin fails |
| `structure` | canonical DesignIR JSON (declared volatile members removed) | at least 3 nodes (`min_structure_nodes`, never lower) |
| `raster` | native render pixels, exact by default | ink (pixels off the most common colour) and luminance-spread floors on both sides |
| `geometry` | laid-out tree: id, kind, rect, visibility, clipping, hit regions, text boxes (0.5 px) | at least one sized non-root node |
| `semantic` | text/selection observation | non-empty record list |
| `interaction` | replayed pointer/keyboard log plus post-interaction render | every planned step logged, at least one handled |
| `host-*` (`lanes/host.py`) | built plug-in bundles in a real host process: load + provenance, editor lifecycle, audio null, coexistence + runtime binding, validators, standalone | see "Host lane" below |

Implementations are JSON descriptions of commands (`implementations/`); the
harness never links or imports either implementation. Each side names its
`checkout` root; its commit is read from that checkout, never typed by the
operator. Its binaries are content-hashed, and its required `build`,
`provider` and `fonts` probes are recorded in the receipt beside the host
platform and the harness's own fingerprint. A lane is `pass`,
`fail`, `blocked` (a step failed or skipped, or an artifact is missing) or
`not_selected`. Only `pass` counts, and an empty corpus, a case with no lane
selected, or an absent implementation identity is never accepting. `parity`
mode refuses two sides that run byte-identical tools, whatever their labels;
`calibration` mode requires identical identities and reports
`scope: harness-calibration`, never parity.

The corpus knobs that could turn a real difference into a pass are bounded at
load time. `raster_threshold` is 0 unless it carries a
`raster_threshold_justification`, and it can never exceed 16.
`min_ink_pixels` is at least 1, and `scale` is explicit. `volatile` pointers
must appear in an allowlist: the default is `/imported_at`, and extensions are
justified corpus-level `volatile_allowlist` entries that can never erase a whole
observation. Geometry and semantic observations must carry their full schema
on both sides.

## Authority, provenance, regions, licensed sources

- **Authority.** Every implementation declares `authority`: `native-render`, `materialized-runtime` or
  `browser-source`. It is never defaulted. Two sides with different authorities get scope `cross-authority`, which
  is diagnostic and never `parity`.
- **Producing binary.** Each step records the digest of the binary it ran (and of the script, for an
  interpreter) at run time. Each observation records the step that produced it. An observation that a selected
  lane reads must come from a declared tool, or from a `passthrough_binaries` entry that carries a justification
  (for example `/bin/cp` copying an input DesignIR). Otherwise the receipt is refused.
- **Regions.** A case may declare named `regions` in design px. The raster lane scores each one separately:
  a localized difference is named, and a region that drew nothing fails even when the whole frame matches. Put
  a shared deterministic fixture in `stage` so both renderers read the same bytes.
- **Third-party sources.** `source_base: "external:<name>"` (resolved with `--external-root name=path`)
  requires `provenance`:
  - a permissive SPDX `license`;
  - the `repository`;
  - a 40-hex `commit`;
  - the source's `sha256`.

  The staged bytes must match the pin.

## Calibrate before trusting a comparison

```sh
python3 tools/parity/parity_harness.py calibrate \
  --corpus tools/parity/corpus/pulp-calibration.json \
  --source-root <origin checkout at the calibrated commit> \
  --implementation tools/parity/implementations/pulp-design-ir-observer.json \
  --tool importer=<pulp-import-design> --tool observer=<pulp-design-ir-observe> \
  --root checkout=<origin checkout> --root build=<its build dir> --out <dir>

python3 tools/parity/parity_harness.py identity-calibrate \
  --manifest tools/parity/corpus/canvas-identity-calibration.json \
  --origin-repo <origin checkout> --origin-commit <origin commit> --out <dir>
```

`calibrate` runs the corpus twice through the same implementation in separate
processes (the positive must accept), then every seeded negative control:
one-byte DesignIR/text/source mutations, a one-pixel change, a uniform render on
both sides, a missing render, a shifted node, an empty layout on both sides, a
changed viewport, a missing asset, a perturbed interaction state, a moved
pointer, a changed key, a planted adapter skip, an absent identity, a
self-comparison in parity mode, an empty corpus and a case with no lane
selected. It exits non-zero unless the positive accepts and every control is
rejected by the lane it targets. A control that applies to no case counts as a
failure: it means the corpus cannot exercise that defect.

`identity-calibrate` does the same for the identity lane with a one-byte
mutation, a removed file, an extra file and an empty manifest. Its export is a
`git archive` of the declared roots, a stand-in until the mirror export and its
machine-generated closure manifest exist.

## Check a real export

```sh
python3 tools/parity/parity_harness.py identity \
  --manifest <pulp.vellum.closure.v1 manifest> \
  --origin-repo <origin checkout> --origin-commit <origin commit> \
  --exported-repo <mirror repo> --exported-commit <mirror commit> --exported-prefix mirror/ \
  [--manifest <second unit manifest> ...] \
  --out <report.json>
```

The origin side reads exactly the manifest's `files`; the export side lists every
blob under the prefix, so a missing, changed, or extra file fails. This check is
independent of the exporter's own receipt verification.

## Run a comparison

```sh
python3 tools/parity/parity_harness.py run --mode parity \
  --corpus <corpus> --source-root <dir> \
  --reference <impl.json> --reference-sha <sha> --reference-tool name=path ... \
  --candidate <impl.json> --candidate-sha <sha> --candidate-tool name=path ... \
  --reference-root checkout=<dir> --candidate-root checkout=<dir> \
  --calibration <calibration.json> --out <dir>
```

Parity mode refuses to run without a calibration record whose corpus hash and
harness fingerprint, including any `--lane-module`, match this run.

## Adding a lane

A lane lives in its own module, `tools/parity/lanes/<name>.py`. The module calls
`register_lane(name, artifacts, comparator)` and `register_controls(...)`, and
is loaded with `--lane-module` on both `calibrate` and `run`. Implementation
steps can also set `env` (values expanded like argv), keep the full stderr as
an observation with `stderr_artifact`, and take per-case argument lists with
`@case_args:NAME` (from the case's `args`). Every new lane
ships with at least one control that must make it fail.

The receipt (`<dir>/receipt.json`) records the corpus hash, both identities
(repository, commit, content hash of every tool binary), every command with its
exit status, the hash of every input and observation, and the verdict.

## Host lane

`lanes/host.py` is the host-compatibility row, separate from screenshot parity.
It registers six lanes (`host-load`, `host-editor`, `host-audio`,
`host-coexist`, `host-validator`, `host-standalone`) and their seeded controls
with the core hook. Each side's implementation runs `lanes/host_runner.py`,
which drives that side's host probe (Pulp's `pulp-host-lane-probe`), its
validators and its standalone apps over built CLAP, VST3 and AU v2 bundles
(AU is registered in the probe process only; nothing is installed). Bundles are
roots named as in the corpus, and each bundle's main executable is also a tool,
so two sides that test the same plug-in bytes cannot claim parity.
`host-load` refuses a bundle whose build record names another commit than the
side's checkout HEAD, or a dirty tree. `host-coexist` refuses a plug-in that is
bound to another plug-in's copy of a shared runtime with different bytes (the
`host-coexist-runtime-version-skew` control seeds that). Rows this host cannot
run are `declared_gaps` in the corpus; per-plug-in exceptions (an animated
editor, a readiness-dependent first render) are `declared_exceptions`.

```sh
cd <pulp checkout>        # implementation tool paths are relative to it
python3 <vellum>/tools/parity/parity_harness.py calibrate \
  --lane-module <vellum>/tools/parity/lanes/host.py \
  --corpus <vellum>/tools/parity/corpus/pulp-host-calibration.json \
  --implementation <vellum>/tools/parity/implementations/pulp-host-in-tree.json \
  --source-root . --tool runner=<vellum>/tools/parity/lanes/host_runner.py \
  --root checkout=. --root spectral-lab.clap=build/CLAP/SpectralLab.clap ... \
  --root skew-runtime=<a libwgpu_native.dylib of a different build> --out <dir>
python3 <vellum>/tools/parity/test_host_lane.py   # fake tools; NOT CALIBRATED reachable
```
