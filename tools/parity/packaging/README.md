# Packaging lane (D6 rung 2)

`packaging_lane.py` compiles the origin repository's own test sources against the **installed** Vellum package and
compares their Catch2 results, test by test, with the origin's in-tree run of the same sources.

- **Reference:** the origin's test binary (for example `pulp-test-group-canvas-text`), built at the exported origin
  commit in the configuration the closure records. The binary's file tags (`-#`) decide which sources run.
- **Candidate:** a generated consumer project that calls `find_package(<package>)` and links only
  `<package target>` plus Catch2. It adds no compile definitions of its own.

The lane is `blocked` unless rung 1 (identity) passes for the same closures and exported commit. It fails when the
reference build's configuration (build type, Skia) differs from the closure's, and when any strong symbol is defined
by more than one package archive. A test that skips on both sides is listed, not counted. Every closure path or
source left out of the comparison must carry a justification, and the receipt names it.

```sh
python3 tools/parity/packaging/packaging_lane.py run --spec spec.json --out <dir>
python3 tools/parity/packaging/packaging_lane.py calibrate --spec spec.json --out <dir>
```

`spec.json` keys:
- Identity: `closures` (closure manifests), `origin_repo`, `origin_commit`, `exported_repo`, `exported_commit`,
  `exported_prefix`.
- Reference: `reference_build` (its CMakeCache) and `reference_binary`.
- Candidate: `candidate_prefix` (an install prefix), `candidate_package`, `candidate_target`, `catch2_source`
  (a local Catch2 source tree) and `jobs`.
- Optional and always justified: `closure_external_paths` and `exclude_sources`.

`calibrate` runs eight seeded controls, and each must make the comparison non-accepting:
- a failing test added to the candidate;
- a source dropped from the candidate;
- a header removed from the package;
- an empty population;
- a missing reference binary;
- a duplicate-definition archive;
- a reference configuration mismatch;
- identity not passing.
