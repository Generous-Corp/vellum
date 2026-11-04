# gpu-runtime

The GPU device layer shared by GPU audio and rendering. It is its own CMake
project (`VellumGpuRuntime`, namespace `gpu_runtime::`) and depends on neither
Pulp nor Vellum, so an audio-only GPU plug-in can link it without a UI
framework and a renderer can link it without an audio framework. It is
versioned with Vellum and builds on its own:

```sh
cmake -S gpu-runtime -B build-gpu-runtime
cmake --build build-gpu-runtime
ctest --test-dir build-gpu-runtime
```

## Contents

| Piece | What it is |
|---|---|
| `VellumGpuRuntime::DeviceApi` | INTERFACE target. The device headers under `<gpu_runtime/...>`: `GpuSurface`, `GpuCompute`, the Dawn proc-table installer, GPU diagnostics, the startup report, render-time tracking and the benchmark perf counters. They expose opaque handles only and name no Dawn, WebGPU or Skia type. `GPU_RUNTIME_BENCHMARK=ON` adds the perf-counter hooks; the definition travels with the target. |
| `renderer-lock.json` | The Skia/Dawn pin: the skia-builder `chrome/mNNN` milestone, the builder and source commits, and per platform the release asset with its SHA-256 and the SHA-256 of every static library inside it. This is the only place those values are written. |
| `cmake/GpuRuntimeRendererLock.cmake` | `gpu_runtime_read_renderer_lock(PLATFORM <p> PREFIX <v>)` reads and validates the lock for a build. Vellum's `cmake/VellumSkiaDawn.cmake` uses it, and the installed package ships both files. |

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
- `gpu-runtime.install-consumer` (standalone builds) installs the package and
  builds a consumer that finds it by exact version and reads the shipped lock.
