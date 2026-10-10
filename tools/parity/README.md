# Paired parity harness

`parity_harness.py` runs one versioned corpus through a **reference** and a
**candidate** implementation and writes one receipt that says, lane by lane and
case by case, whether the two produced the same observations. It is the
behavioral half of the proof ladder: identity first, then packaging and host
closure, then these differential lanes, with similarity scores advisory only.

| Lane | Compares | Vacuity guard |
|---|---|---|
| `identity` | exported tree vs origin closure, git blob by blob | empty manifest or empty origin fails |
| `structure` | canonical DesignIR JSON (declared volatile members removed) | root plus at least one child |
| `raster` | native render pixels, exact by default | ink coverage floor on both sides |
| `geometry` | laid-out tree: id, kind, rect, visibility, clipping, hit regions, text boxes (0.5 px) | at least one sized non-root node |
| `semantic` | text/selection observation | non-empty record list |
| `interaction` | replayed pointer/keyboard log plus post-interaction render | every planned step logged, at least one handled |

Implementations are JSON descriptions of commands (`implementations/`); the
harness never links or imports either implementation. A lane is `pass`,
`fail`, `blocked` (a step failed or skipped, or an artifact is missing) or
`not_selected`. Only `pass` counts, and an empty corpus, a case with no lane
selected, or an absent implementation identity is never accepting. `parity`
mode refuses identical reference/candidate identities; `calibration` mode
requires them and reports `scope: harness-calibration`, never parity.

## Calibrate before trusting a comparison

```sh
python3 tools/parity/parity_harness.py calibrate \
  --corpus tools/parity/corpus/pulp-calibration.json \
  --source-root <origin checkout at the calibrated commit> \
  --implementation tools/parity/implementations/pulp-design-ir-observer.json \
  --tool importer=<pulp-import-design> --tool observer=<pulp-design-ir-observe> \
  --source-sha <origin commit> --out <dir>

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
  --out <dir>
```

The receipt (`<dir>/receipt.json`) records the corpus hash, both identities
(repository, commit, content hash of every tool binary), every command with its
exit status, the hash of every input and observation, and the verdict.
