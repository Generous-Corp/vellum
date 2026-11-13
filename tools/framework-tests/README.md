# Framework tests

Behavior tests of the units under `framework/`. They build in-tree when the
mirror package is on (`-DVELLUM_ENABLE_MIRROR=ON -DVELLUM_BUILD_TESTS=ON`),
link the same `Vellum::Mirror*` unit libraries the SDK installs, and run in
the macOS GPU lane's `ctest`.

```sh
ctest --test-dir build-gpu -L framework --output-on-failure
```

Add a test with `vellum_add_framework_test(<name> SOURCES <name>_test.cpp
UNITS Vellum::Mirror<Unit>)` in `CMakeLists.txt`. It registers as
`vellum.framework.<name>`. `framework_test.hpp` is the runner: `FRAMEWORK_TEST`
declares a case, `CHECK` records a failure and continues.

The provenance verifier lets these files name the units (`vellum::view`,
`<vellum/view/...>`), which other native trees may not, and holds them to
being consumers: flat files named `*_test.cpp`, `*_test.mm`, `*.hpp`,
`CMakeLists.txt` or `README.md`, none of which opens a `vellum` namespace.
Whole-file copies of `framework/` sources are refused here as anywhere else,
and so is any file whose exact bytes are a Pulp-origin blob: a recorded one
always, and any blob of the Pulp checkout the provenance lane reads. Nothing
here ships: an `install()` in this directory, or one elsewhere that names it,
is refused, and the SDK builder configures with tests off.
