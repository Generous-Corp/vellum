# Packaging lane (D6 rung 2)

`packaging_lane.py` compiles the origin repository's own test sources against the **installed** Vellum package and
compares their Catch2 results, test by test, with the origin's in-tree run of the same sources.

- **Reference:** the origin's test binaries (for example `pulp-test-group-core-runtime`), built at the exported origin
  commit in the configuration the closures record. Each binary's file tags (`-#`) decide which sources run; every
  tag must name exactly one test source at the origin commit.
- **Candidate:** for each group, a generated consumer project that calls `find_package(<package>)` and links only the
  group's package targets plus Catch2. It adds no compile definitions of its own.

The lane is `blocked` unless:
- rung 1 (identity) passes for the same closures and exported commit; and
- the installed package carries exactly one export receipt, byte-identical to the receipt at the exported commit, and
  that receipt names the origin commit.

It fails when:
- the reference build's configuration (build type, Skia) differs from the closures';
- any strong symbol is defined by more than one package library;
- a candidate image defines a symbol the package exports, carries its own Skia/Dawn provider, or links no package
  library;
- a group that statically links Skia shows no provider definition in its reference binary (the census would be blind);
- any test differs in population or outcome, fails on both sides, or nothing passed.

It also fails when:
- a test binary is killed by a signal, writes no JUnit, or exits with a status its JUnit does not explain (nonzero
  with no failed test, or zero with one). Each run's JUnit file is deleted before the run, so a binary that dies
  before `main` can never be read through an earlier file;
- an excluded source now compiles and links against the package (each is probed on its own), so an exclusion cannot
  outlive its reason;
- an `unstable_tests` name is in no reference population, or fails on the candidate;
- a candidate translation unit read an origin file outside `test/` whose blob the export does not carry identically
  at the same path (audited from the consumer's ninja dependency log).

A test that skips on both sides is listed, not counted. Every source left out (`exclude_sources`) and every origin
test that cannot repeat on the reference binary alone (`unstable_tests`) must carry a justification, and the receipt
names it, together with a `scope` line: a named subset (sources compared of the total) or the full population.
Receipts replace host paths with root names.

```sh
python3 tools/parity/packaging/packaging_lane.py calibrate \
  --spec tools/parity/packaging/specs/foundation-canvas-20261014-34d16417a460.json \
  --root origin=<pulp checkout at origin_commit> --root reference-build=<its Release build> \
  --root exported=<this checkout> --root prefix=<installed Vellum package> \
  --root catch2=<Catch2 source the origin build used> \
  --root divergent-prefix=<package built with a planted mirror change> --out <dir>
python3 tools/parity/packaging/packaging_lane.py run --spec ... --root ... --out <dir>
```

Committed specs name host locations as `<root>`; an unbound root is refused.

`calibrate` runs thirteen seeded controls, and each must make the comparison non-accepting:
- a failing test added to the candidate;
- a candidate whose static initializer aborts before `main`;
- an exclusion added for a source that builds;
- an `unstable_tests` name that is in no population;
- a source dropped from the candidate;
- a header removed from the package;
- an empty population;
- a missing reference binary;
- a duplicate-definition library;
- a reference configuration mismatch;
- a mirror commit with one mutated blob (identity, `blocked`);
- a package whose receipt names another origin (`blocked`);
- a package built from a mirror with one planted behavior change. Identity and the receipt binding describe the
  commit, so they still pass; the differential must catch it.

## Reproducing the inputs for `foundation-canvas-20261014-34d16417a460`

1. Reference: a Pulp worktree at `34d16417a460da7bc83e6df9f6319625423e4847` (default Release, Skia on), built with
   `pulp build --target pulp-test-group-core-runtime --target pulp-test-group-core-platform
   --target pulp-test-group-core-events --target pulp-test-group-core-canvas --target pulp-test-group-canvas-text`.
2. Package: configure this checkout with `-DCMAKE_BUILD_TYPE=Release -DVELLUM_ENABLE_MIRROR=ON
   -DVELLUM_REQUIRE_GPU=ON`, build, and `cmake --install` into the `prefix` root.
3. Divergent package: in a second checkout of the same commit, configure the same way (the receipt check runs at
   configure), then change `mirror/core/canvas/src/path_measure.cpp`'s segment length to
   `std::sqrt(dx * dx + dy * dy) * 1.01f`, build and install into the `divergent-prefix` root.

The spec's `exclude_sources` record two package defects, each a reason the full population cannot run yet:
- **F-A:** the export omits public headers no unit source includes (for example `pulp/runtime/result.hpp`,
  `pulp/events/events.hpp`, `pulp/canvas/image_convolution.hpp`);
- **F-B:** public canvas headers include Skia headers the package keeps private (D4), so the text and font suites
  cannot compile against the package.

Not yet bound, and therefore stated rather than assumed:
- the reference binary is checked against the build's configuration and the checkout HEAD, not proven built from
  `origin_commit`;
- test sources are read from the origin worktree (the dependency audit hashes non-test origin files against git;
  test sources themselves are not);
- per-translation-unit compile definitions are not compared (the candidate gets the package's interface
  definitions);
- the provider census matches `wgpuCreateInstance`, `SkCanvas::*` and `SkSurface::*` only, and is checked for
  blindness against the reference;
- the receipt binding covers the export receipt, not the package library bytes, which are recorded but not tied to a
  reproducible build of the mirror.
