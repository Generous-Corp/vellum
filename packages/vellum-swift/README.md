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

`vellum.swift-host-proof` runs `scripts/run_sterile_proof.py`. It requires the
pinned toolchain, Xcode 27.2 (27B52) with Swift 6.4.2, and fails on any other. It stages a
copy of the package in its work directory, builds it against the SDK named by
`VELLUM_SWIFT_PROOF_SDK_PREFIX` (a CMake cache variable or an environment
variable), checks that the build and the binary reference only the installed
prefix, runs the proof and writes `swift-host-proof-evidence.json` in its work
directory. Without a prefix the test is skipped with a message.
`vellum.swift-host-proof-unit` covers the runner's skip and sterility rules.
