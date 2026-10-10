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
