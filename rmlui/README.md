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

## Detecting the API

`vellum/rmlui/native.hpp` defines `VELLUM_RMLUI_NATIVE_API_VERSION` and a set
of feature bits, so a consumer that builds against several releases tests a
macro instead of compiling a probe for each method:

```cpp
#if defined(VELLUM_RMLUI_NATIVE_API_VERSION) && \
    (VELLUM_RMLUI_NATIVE_FEATURES & VELLUM_RMLUI_NATIVE_FEATURE_WARM)
    native.warm();
#endif
```

The version is 1 for the first release with the macros and grows by one when a
release adds an interface or changes one a consumer can detect. The bits are
`VELLUM_RMLUI_NATIVE_FEATURE_DENSITY_RATIO` (`set_density_ratio`),
`VELLUM_RMLUI_NATIVE_FEATURE_WARM` (`warm`) and
`VELLUM_RMLUI_NATIVE_FEATURE_RENDERER_TRIM` (`trim` on the Metal and CPU
renderers). A release older than the macros defines none of them.

## Giving GPU memory back

The Metal renderer keeps render targets between frames: a texture for each
layer, a stencil, the filter targets and the mask image, all sized to the
frame target. `MetalRenderInterface::trim()` releases them and returns the
bytes they held; `cached_target_bytes()` reports what is held. The next frame
allocates again what it uses and draws the same pixels, so the first frame
after a trim pays the allocation. A host that hides an editor calls it to hand
the memory back.

Call it between frames. During a frame (between `begin_frame()` and
`end_frame()`) it releases nothing and returns 0. It never waits for the GPU:
the previous frame's command buffer may still be running, which is safe
because a command buffer made with `-[MTLCommandQueue commandBuffer]` retains
the resources it uses. A caller whose buffers use unretained references waits
for the last one first.

`trim()` leaves RmlUi's own state alone. Compiled geometry, textures, filters
and shaders are RmlUi's handles, released through `Rml::ReleaseCompiledGeometry()`
and `Rml::ReleaseTextures()`; pipelines and samplers belong to the device and
stay. `CpuRenderInterface::trim()` does the same for the CPU renderer's layer
buffers, clip mask and command storage.

## One RmlUi per program

`VellumRmlUi::Core` and `VellumRmlUiNative` (Native, and Metal, which links
Native's RmlUi) each carry their own copy of RmlUi. One program must not link
both: two copies of RmlUi's global state in one process do not work together.
Link Core and the components built on it, or Native and its render
interfaces, never both.

CMake enforces this. Native's targets and Core name their copy in
`INTERFACE_VELLUM_RMLUI_RUNTIME`, so generating a target whose link closure
contains both fails with an error naming that property. Core is tagged where
it is built, so the check holds whichever of the two packages a project finds
first; a VellumRmlUi package exported before Core carried the tag is given it
when VellumRmlUiNative is found after it.

A project that needs both, such as a test comparing the two renderers, builds
them into separate executables.
