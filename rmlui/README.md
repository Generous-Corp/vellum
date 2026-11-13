# VellumRmlUi

An optional module that draws RmlUi documents in a Vellum editor. It builds
from `rmlui/` inside a Vellum build (`VELLUM_ENABLE_RMLUI=ON`) or on its own
(`cmake -S rmlui`), and installs as one or two CMake packages.

## Packages

**VellumRmlUi** ships in the Vellum SDK. `VellumRmlUi::Core` is a shared
library with RmlUi linked privately: none of RmlUi's headers or symbols reach
a consumer. `SoftRenderer`, `DevAssets` and `VellumRenderer` (which draws
through Vellum's GPU renderer) build on it.

**VellumRmlUiNative** is the open entry point, for a host that drives RmlUi
through its own API and its own render interface. `VellumRmlUi::Native` links
RmlUi publicly, and `VellumRmlUi::Metal` is an `Rml::RenderInterface` on Metal.
The package carries the pinned RmlUi (headers and static archives, as
`RmlUi::Core` and `RmlUi::Debugger`), FreeType's archive when fonts are on, and
their notices under `share/doc/VellumRmlUiNative/`. It is not part of the
Vellum SDK. Each release publishes it separately as
`vellum-rmlui-sdk-<version>-darwin-arm64.tar.gz`.

Build it with `VELLUM_RMLUI_NATIVE=ON`, `VELLUM_RMLUI_METAL=ON` and
`VELLUM_RMLUI_NATIVE_PACKAGE=ON`, or use `scripts/build_rmlui_sdk_artifact.py`,
which builds the release archive reproducibly. Without
`VELLUM_RMLUI_NATIVE_PACKAGE`, Native installs into the VellumRmlUi package
directory instead, as a component of that package.

## Using the RmlUi SDK

Install the Vellum SDK and the RmlUi SDK into separate prefixes, then put both
on `CMAKE_PREFIX_PATH`:

```cmake
find_package(VellumRmlUiNative 0.1 REQUIRED COMPONENTS Native Metal)
target_link_libraries(editor PRIVATE VellumRmlUi::Native VellumRmlUi::Metal)
```

Any VellumRmlUi component named in the same call (`Core`, `SoftRenderer`,
`VellumRenderer`) is found from the Vellum SDK's VellumRmlUi package.

## One RmlUi per program

`VellumRmlUi::Core` and `VellumRmlUiNative` (Native, and Metal, which links
Native's RmlUi) each carry their own copy of RmlUi. One program must not link
both: two copies of RmlUi's global state in one process do not work together.
Link Core and the components built on it, or Native and its render
interfaces, never both.

CMake enforces this. Native's targets and Core name their copy in
`INTERFACE_VELLUM_RMLUI_RUNTIME`, so generating a target whose link closure
contains both fails with an error naming that property. Core is tagged when
the VellumRmlUiNative package is found, so the check covers a VellumRmlUi
package found before or through it, not one a project finds afterwards.

A project that needs both, such as a test comparing the two renderers, builds
them into separate executables.
