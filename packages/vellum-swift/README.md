# VellumSwift

A SwiftPM package that hosts a Vellum application from Swift through the
installed host boundary C ABI (`bindings/c/`). It contains:

- `CVellumHostC`, a system-library target that finds the installed
  `vellum-host-c` through pkg-config;
- `VellumHost`, the Swift wrapper: `VellumApplication`, `VellumSurface`, the
  `VellumHostView` AppKit view and the `VellumView` SwiftUI view;
- `vellum-swift-proof`, an executable that renders one offscreen frame, checks
  it against the content floor, exercises pointer, text and accessibility, and
  tears down.

The package wraps the host boundary only. It has no API over the retained scene.

## Building against an installed SDK

The package builds from an installed SDK prefix alone, with no Vellum source
tree. `lib/pkgconfig/vellum-host-c.pc` supplies the include and library paths;
the executable also needs a runtime search path to the SDK's `lib`.

```sh
sdk="$prefix/lib/vellum/sdk"
PKG_CONFIG_PATH="$sdk/lib/pkgconfig" swift build -c release \
  -Xlinker -rpath -Xlinker "$sdk/lib"
"$(swift build -c release --show-bin-path)/vellum-swift-proof" \
  --bundle Fixtures/host-proof-app.js
```

## Test

`vellum.swift-host-proof` runs `scripts/run_sterile_proof.py`. By default a
`vellum.swift-host-proof-prefix` fixture first installs the build tree into
`<build>/swift-host-proof/prefix` with `cmake --install`, the way the CMake
install consumers do. `VELLUM_SWIFT_PROOF_SDK_PREFIX` (a CMake cache variable or
an environment variable) points the proof at another installed SDK instead, such
as one made by `scripts/build_sdk_artifact.py` and `scripts/install.sh`. The
runner refuses a work directory that is not a dedicated child of `--build-dir`
or that is or contains the package or source tree, and only then clears it. It
stages a copy of the package, builds it against that prefix, checks that
the build and the binary reference only the installed prefix, runs the proof and
writes `swift-host-proof-evidence.json` in its work directory.

The proof renders and captures offscreen. When a window-server session exists it
also hosts `VellumHostView` in a borderless window and `VellumView` in an
`NSHostingView`, and drives a click, a backing-scale change, a resize and the
accessibility children through them. Outside CI the runner first checks that
the machine is idle (`HIDIdleTime` of at least 180 s and a 1-minute load under 4)
and skips only the window-opening part, with a message, when it is in use.

- With `VELLUM_REQUIRE_GPU=ON` (`--require-prefix`), a missing prefix or a missing
  `swift` fails the test; otherwise each skips with a message.
- The toolchain is pinned to Xcode 27.2 (27B52) with Swift 6.4.2. The proof
  builds and runs on any toolchain and records `pinned_toolchain_matched`; a
  mismatch fails only with `VELLUM_SWIFT_PROOF_REQUIRE_PINNED_TOOLCHAIN` (a CMake
  option or an environment variable set to `1` or `true`).
- With `--require-prefix` the native hosts must pass locally; the only accepted
  local skip is the runner's own desk-machine guard. The runner clears any
  inherited `VELLUM_SWIFT_PROOF_SKIP_NATIVE` and sets it only from that guard.
  The windows are borderless and never ordered front.
- Before the native part, the runner probes the window server. It records
  whether `launchctl print gui/<uid>` succeeds and whether
  `CGSessionCopyCurrentDictionary` returns a session, both in the log and as
  `window_server_probe` in the evidence. In CI (`CI` or `GITHUB_ACTIONS` set to
  `true` or `1`), a missing window-server session does not fail the proof. It
  is recorded as `skipped-no-window-server`, never as `passed`, with a loud
  `SKIP` line and a `$GITHUB_STEP_SUMMARY` entry.
- `vellum.swift-host-proof-native` reads the evidence and reports Skipped in
  ctest's summary whenever the native part did not run, so a skip stays visible
  even though passing tests print nothing.

`vellum.swift-host-proof-unit` covers the runner's skip, failure and sterility
rules.
