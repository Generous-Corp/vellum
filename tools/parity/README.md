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
| `frames` (module `lanes/frames.py`) | every frame under a fixed host clock: dispatch decision, dt, readiness, focus, control and animation state, menus, parameters, layout and pixel digests, captures | every scheduled frame present, full record schema, pixels on every frame, every event applied in its frame, the observation moved |

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
  lane reads must come from a declared tool. Passthrough entries are digest-bound, copy-only tools and may copy
  staged inputs or earlier declared artifacts; they can never produce a selected observation. Copy arguments are
  resolved in the side directory, including directory trees, so spelling changes cannot launder provenance. The
  policy retains every digest produced at every earlier step: a selected output whose bytes match any staged input
  or strictly earlier output is treated as a copy, even when the source is overwritten later. This is a content
  test with limits: it catches byte-for-byte copies, while a copy followed by an edit generally evades a digest-only
  check unless the edited bytes happen to match another known digest. It cannot identify a copy from the other
  side's directory without a matching digest in this run. Byte-identical legitimate outputs are therefore refused
  as selected observations.
- **Regions.** A case may declare named `regions` in design px. The raster lane scores each one separately:
  a localized difference is named, and a region that drew nothing fails even when the whole frame matches. Put
  a shared deterministic fixture in `stage` so both renderers read the same bytes.
- **Third-party sources.** `source_base: "external:<name>"` (resolved with `--external-root name=path`)
  requires `provenance`:
  - a permissive SPDX `license`;
  - the `repository`;
  - a 40-hex `commit`;
  - the source's `sha256`;
  - for staged companions, a `files` map covering every staged relative path with its sha256; and
  - an external root whose checked-out `HEAD` equals `commit`.

  The staged bytes must match the pin.

## Containment and delegation (`parity_policy.py`)

- **Paths:** every corpus path (`source` and each `stage` entry) must stay inside its root. Absolute paths, `..`
  and symlinks (along the path, or inside a staged directory) are refused before anything is copied. A rerun
  control that writes its own file passes it as `extra_stage` to `run_side`, not as a corpus path.
- **Regions:** a region must lie wholly inside both rendered images. It is never shortened to fit.
- **Delegation:** an observation produced through a command string (`sh -c`, `bash -c`, `python -c`, `node -e`,
  …) is refused, even if that shell is a declared passthrough. The shell's digest says nothing about the program
  that wrote the file. Run the producer directly, as a declared tool or script.
- **Limit:** a declared tool or script that itself starts an undeclared program is not traced. The declared
  script is responsible for what it runs.

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

## Frames lane

```sh
python3 tools/parity/parity_harness.py calibrate --lane-module tools/parity/lanes/frames.py \
  --corpus tools/parity/corpus/pulp-frames-calibration.json \
  --source-root <origin checkout> \
  --implementation tools/parity/implementations/pulp-ui-frames-observer.json \
  --tool frames=<pulp-ui-frames-observe> \
  --root checkout=<origin checkout> --root build=<its build dir> --out <dir>
```

Both sides replay the case's staged schedule (`pulp-ui-frame-schedule-v1`:
host timestamps, parameters, initial editor count, timed hover, click, drag,
key, context-press, host parameter write, resize and editor attach/detach
events, and the frames to capture). The comparison is exact and frame by frame;
the lane result names the first divergent frame and whether the final frames
agree, so a receipt shows when timing was the only difference. Each side must
have read the schedule it was staged with, and the two environments (platform,
architecture, backend, scale, size) must agree.

Its controls: a change landing one frame late with the final frame untouched,
a perturbed dt, a schema key dropped on both sides, a frozen observation on
both sides, a truncated run, blank pixels on both sides, an event dropped on
both sides, a capture pixel, and candidate re-runs at 120 Hz, with a dropped
vsync, with an event one frame late, with an immediate instead of a view-first
document load, with a different host parameter write, without the second
editor, with a different resize, and (for cases that declare `timers`) with script timers left on the wall clock
(`timer-clock-ignored`). A re-run control counts only when what a
viewer would see differed at some frame, not when only the schedule's own
timestamps or hash did.

## GPU lanes (`lanes/gpu.py`)

`gpu-raster`, `gpu-receipt` and `provider-census` compare a render through Skia
Graphite on a Dawn device, not the Skia CPU raster the `raster` lane reads. Use
them with `implementations/pulp-design-ir-observer-gpu.json` and
`corpus/pulp-gpu-calibration.json`:

```sh
# The second-dawn-inserted control needs a real second Dawn; build it from the
# archive the implementation links.
python3 tools/parity/lanes/gpu.py build-second-dawn \
  --dawn-archive <skia-build>/mac-gpu/lib/Release/libdawn_combined.a --out <dir>/libsecond_dawn.dylib
export VELLUM_PARITY_SECOND_DAWN_DYLIB=<dir>/libsecond_dawn.dylib

python3 tools/parity/parity_harness.py calibrate \
  --lane-module tools/parity/lanes/gpu.py \
  --corpus tools/parity/corpus/pulp-gpu-calibration.json \
  --source-root <checkout> --root checkout=<checkout> --root build=<build> \
  --implementation tools/parity/implementations/pulp-design-ir-observer-gpu.json \
  --tool importer=<pulp-import-design> --tool observer=<pulp-design-ir-observe> --out <dir>
```

The render receipt is the renderer's own account, so it is never the only
witness. `provider-census` reads the images the loader actually mapped
(`DYLD_PRINT_LIBRARIES`, captured by the step wrapper) with `nm`: exactly one
image may carry Dawn's implementation and exactly one Skia, a GPU driver must
have been loaded, and a further WebGPU C API exporter fails unless the case
names it with a justification. A renderer that falls back to the CPU and still
reports `gpu` is caught by `gpu-raster` (Graphite and CPU raster anti-alias
differently) and by the census (no GPU driver). The census is macOS-only;
elsewhere the lane is blocked, not passed.

Seeded controls: one-pixel, blank-both and missing GPU renders; a missing
receipt, a changed Dawn revision and a changed adapter; a second Dawn image in
the loader record, no GPU driver on both sides, a missing census; and reruns
with the observer's `--gpu-fault` (destroyed device, undrawn frame, reported
and unreported CPU fallback) and with a real second Dawn inserted into the
process. Without `VELLUM_PARITY_SECOND_DAWN_DYLIB` that last control applies to
no case, and calibration reports NOT CALIBRATED.

## Artifact lanes

`lanes/artifact.py` checks the static shape of a built artifact without running
it, for build modes no host on this machine can execute (a universal image's
x86_64 slice, an iOS-simulator extension, an Android library). Each side runs
`lanes/artifact_runner.py`, which records every file, the architectures and
build platform of every Mach-O, the machine of every ELF image, Info.plist keys,
codesign results, and, per architecture of the main image, symbol counts for the
families named in `corpus/fixtures/artifact/scan.json`. A case selects only the
lanes that apply to its artifact:

- `artifact-slices`: every Mach-O carries exactly the case's architectures and
  platform; every ELF image has the case's machine.
- `artifact-runtimes`: each named bundled runtime is present, has the case's
  architectures, and is loaded by every slice of the main image.
- `artifact-bundle`: the Info.plist reads and matches, the main image exists, and
  every required path is present.
- `artifact-signature`: either the sealed bundle verifies, or each named slice of
  the main image verifies on its own. A development build carries only the
  linker's signature.
- `artifact-linkfloor`: in each slice there is no forbidden symbol family and no
  forbidden load command, every required family is present, and each
  `require_load_once` load command appears exactly once.

The controls fault a copy of the artifact:

- a wrong-architecture slice;
- a thinned bundled runtime;
- a missing runtime or resource;
- a flipped byte under the signature;
- a differently linked image in place of this one.

```sh
python3 <vellum>/tools/parity/parity_harness.py calibrate \
  --lane-module <vellum>/tools/parity/lanes/artifact.py \
  --corpus <vellum>/tools/parity/corpus/pulp-artifact-macos-universal.json \
  --implementation <vellum>/tools/parity/implementations/pulp-artifact-macos.json \
  --source-root <checkout> --tool runner=<vellum>/tools/parity/lanes/artifact_runner.py \
  --tool host-runner=<vellum>/tools/parity/lanes/host_runner.py --root checkout=<checkout> \
  --root gain.clap=<build>/CLAP/<audio-only>.clap ... --root gpu.component=<build>/AU/<gpu>.component --out <dir>
python3 <vellum>/tools/parity/test_artifact_lane.py   # universal fixture bundles built with clang
```
