# gpu-runtime

The GPU device layer shared by GPU audio and rendering. It is its own CMake
project (`VellumGpuRuntime`, namespace `gpu_runtime::`) and depends on neither
Pulp nor Vellum, so an audio-only GPU plug-in can link it without a UI
framework and a renderer can link it without an audio framework. It is
versioned with Vellum and builds on its own:

```sh
cmake -S gpu-runtime -B build-gpu-runtime \
    -DGPU_RUNTIME_RENDERER_ARCHIVE=/path/to/skia-build-mac-universal-gpu-release.zip
cmake --build build-gpu-runtime
ctest --test-dir build-gpu-runtime
```

## Contents

| Piece | What it is |
|---|---|
| `VellumGpuRuntime::DeviceApi` | INTERFACE target. The device headers under `<gpu_runtime/...>`: `GpuSurface`, `GpuCompute`, the Dawn proc-table installer and bootstrap, GPU diagnostics, the host hooks, the startup report, render-time tracking and the benchmark perf counters. They expose opaque handles only and name no Dawn, WebGPU or Skia type. `GPU_RUNTIME_BENCHMARK=ON` adds the perf-counter hooks; the definition travels with the target. |
| `VellumGpuRuntime::Bootstrap` | The image's one Dawn proc-table coordinator (`dawn_bootstrap.hpp`). It keeps the ABI v1 registration renderers already ship with and adds a per-bundle registration that records the bundle and its gpu-runtime version: two bundles may share one coordinator only when both their Dawn revision and their gpu-runtime version agree. |
| `VellumGpuRuntime::Device` | The Dawn surface and compute implementation, the native bootstrap (the only code that calls `dawnProcSetProcs`), GPU diagnostics and the host hooks. It links the locked `libdawn_combined.a` and no Skia. Objects are compiled with hidden visibility, so a plug-in that links it statically exports none of it. `GPU_RUNTIME_HOST_OWNS_DAWN_PROCS=ON` removes the native default, so nothing is installed until the host bootstraps; Vellum builds it that way. |
| `VellumGpuRuntime::SkiaLogBridge` | The one piece that names Skia: it routes Skia's log handler into the diagnostic sink. A renderer that links Skia calls `register_skia_log_bridge(skia_log_bridge_installer())`. |
| `renderer-lock.json` | The Skia/Dawn pin: the skia-builder `chrome/mNNN` milestone, the builder and source commits, and per platform the release asset with its SHA-256 and the SHA-256 of every static library inside it. This is the only place those values are written. |
| `cmake/GpuRuntimeRendererLock.cmake` | `gpu_runtime_read_renderer_lock(PLATFORM <p> PREFIX <v>)` reads and validates the lock for a build. Vellum's `cmake/VellumSkiaDawn.cmake` uses it, and the installed package ships both files. |
| `cmake/GpuRuntimeDawn.cmake` | `gpu_runtime_find_dawn()` locates the consumer's copy of the locked asset (`GPU_RUNTIME_SKIA_DIR`, or `GPU_RUNTIME_RENDERER_ARCHIVE`) and checks `libdawn_combined.a` against the lock. gpu-runtime does not redistribute Dawn. |

The device layer depends on no framework, so the host supplies what a
framework used to: `gpu_host_hooks.hpp` takes a log sink (stderr without one),
a trace sink (events dropped without one) and process options that replace the
environment variables `GPU_RUNTIME_SOFTWARE_ADAPTER` and
`GPU_RUNTIME_LOG_BRIDGE`.

## Who reads the lock

- `cmake/VellumSkiaDawn.cmake` checks the downloaded archive and its libraries
  against it.
- `.github/workflows/gpu-macos.yml` downloads the asset it names.
- `scripts/check_renderer_lock.py` fails when the release-eligibility lock
  (`provenance/third-party-lock.json`), the builder records
  (`provenance/renderer-attestation.json`) or a document quoting the pin
  disagrees with it, or when any other file holds a locked digest.

A renderer bump edits `renderer-lock.json` first; the check then names every
record that still has to follow.

## Checks

- `gpu-runtime.device-api-consumer` builds and runs against the API target
  alone, with no include directory or library beyond it.
- `gpu-runtime.device-api-closure` refuses any header that includes or names
  something above the device API (Pulp, Vellum, Dawn, WebGPU, Skia) or opens a
  namespace outside `gpu_runtime`.
- `gpu-runtime.renderer-lock-reader` reads the lock back and proves that each
  kind of malformed lock stops configure.
- `gpu-runtime.bootstrap` drives the coordinator through its public API:
  malformed, throwing, failing and re-entrant registrations, then the
  per-bundle sharing rules and ABI v1 on the same coordinator.
- `gpu-runtime.device` links the device library alone: the host hooks, the
  diagnostic sink, the Skia bridge seam, the native bootstrap's identity check,
  the default proc-table path in both ownership modes, and a standalone
  `GpuCompute`.
- `gpu-runtime.install-consumer` (standalone builds) installs the package and
  builds a consumer that finds it by exact version and reads the shipped lock.
