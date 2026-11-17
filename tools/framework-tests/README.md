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
`vellum.framework.<name>`. Configure fails when a `*_test.cpp` or `*_test.mm`
file here is named by no `vellum_add_framework_test()`, so a dropped
registration turns the lane red instead of silently running nothing. `framework_test.hpp` is the runner: `FRAMEWORK_TEST`
declares a case, `CHECK` records a failure and continues.

The provenance verifier lets these files name the units (`vellum::view`,
`<vellum/view/...>`), which other native trees may not, and holds them to
being consumers: flat files named `*_test.cpp`, `*_test.mm`, `*.hpp`,
`CMakeLists.txt` or `README.md`, none of which opens a `vellum` namespace or defines a function out of line
under a qualified name.
Whole-file copies of `framework/` sources are refused here as anywhere else,
and so is any file whose exact bytes are a Pulp-origin blob: a recorded one
always, and any blob of the Pulp checkout the provenance lane reads. Nothing
here ships: an `install()` in this directory, or one elsewhere that names it,
is refused, and the SDK builder configures with tests off.

## Linux host tests

`linux_sdl_window_x11_test.cpp` (the SDL window host on an X server: create,
resize, a server-generated click) and `linux_atspi_tree_test.cpp` (the AT-SPI
tree over a D-Bus session) register only on Linux, under the label
`linux-host`. Run them with
`xvfb-run -a dbus-run-session -- ctest -L linux-host --output-on-failure`; a
missing display, session bus or XTest extension is a skip (exit 77). Each has a
control: the window test's `--control` run leaves out the resize and the click
and must see neither, and the AT-SPI test must fail to read a path the provider
never exported and must stop answering once the provider is shut down.

`linux_backends_test.cpp` runs the Avahi and libnotify backends against fake
libraries, so it needs no display, bus or daemon, and registers on Linux under
the same label. `linux_pure_helpers_test.cpp` covers the platform-independent
helpers behind the Linux backends (AT-SPI character offsets, XDND reply
validation, memory-pressure classification) and runs on every platform.
