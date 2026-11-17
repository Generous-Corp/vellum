# Debugging the renderer

`SkiaDawnSurface` measures every frame and can show where it repainted. Two
tools expose that: a frame trace you open in Perfetto, and a damage overlay
drawn over the presented image. Both are off by default and cost one atomic
load per frame stage while off.

## Capture a trace in three steps

```sh
VELLUM_FRAME_TRACE=/tmp/vellum-trace.json ./your-vellum-app
open https://ui.perfetto.dev
# drag /tmp/vellum-trace.json into the page
```

Run the app, use it, and quit. The file is written when a surface is
destroyed and again at process exit. Instead of the web UI,
`trace_processor_shell /tmp/vellum-trace.json` answers SQL queries over the
same file. The file is Chrome trace-event JSON, which Perfetto imports
directly.

## What the tracks mean

Slices, per frame on the thread that renders:

| Slice | Covers |
| --- | --- |
| `graphics.frame` | One `render`, `render_incremental` or damage render, from the device check to the end of the frame's bookkeeping. |
| `graphics.record` | Lowering the scene, planning layers and drawing into the retained surface. |
| `graphics.submit` | Acquiring the swapchain texture, copying the retained image into it, recording and submitting the command buffer. A capture's readback shows as a lone `graphics.submit`. |
| `graphics.present_wait` | Inside `graphics.submit`: waiting for the native swapchain texture. |
| `graphics.present` | The swapchain `Present` call. Native surfaces only. |

Counters, once per drawn frame. Perfetto names each one `<counter> value`.

| Counter | Meaning |
| --- | --- |
| `graphics.damage_pixels`, `graphics.damage_rects` | Device pixels and rectangles repainted. A full repaint is the whole surface and one rectangle. |
| `graphics.paint_commands_recorded`, `graphics.paint_commands_total` | Commands drawn this frame against all commands in the scene. |
| `graphics.cpu_ms`, `graphics.present_wait_ms` | CPU time for the frame, and the part spent waiting for the swapchain. |
| `graphics.gpu_ms` | GPU execution time, reported when the frame has finished: with the next frame or `finish()`. Absent on a device without timestamp queries. |
| `graphics.layer_count` | Offscreen layers opened by the frame. |
| `graphics.cache.paragraphs.bytes`, `.gradients.bytes`, `.textures.bytes` | Bytes each cache holds after the frame. |
| `graphics.cache.paragraphs.hit_rate`, `.gradients.hit_rate`, `.textures.hit_rate` | Hits over lookups in this frame, zero when there were none. |

Instants:

| Instant | Detail |
| --- | --- |
| `graphics.full_repaint` | Why the frame repainted everything, for example `first_frame` or `damage_threshold` (`FullRepaintReason`). |
| `graphics.cache_trim` | Why the caches dropped entries: `resize_settled`, `device_lost` or `budget_changed`. |
| `graphics.device_lost` | The Dawn device was lost; the detail is the failure message. |

A frame that repaints most of the surface every time shows as large
`graphics.damage_pixels` with a `graphics.full_repaint` instant naming the
cause. A stutter shows as a long `graphics.present_wait` slice, or as
`graphics.gpu_ms` above `graphics.cpu_ms`.

## Embedding in a host's tracing

`vellum/graphics/frame_trace.hpp` is the whole integration surface. A host
that already owns a tracing session installs a `FrameTraceSink`, four
function pointers for slice begin and end, counters and instants, with
`set_frame_trace_sink`, and forwards each call to its session so the
renderer's tracks appear beside its own. The sink is process-wide and is
called on the rendering thread, so a sink shared by several surfaces must be
thread-safe. `FrameTraceRecorder` is the sink behind `VELLUM_FRAME_TRACE`: it
holds events in memory, caps them at a limit it reports as dropped events,
and writes the JSON file. `VELLUM_FRAME_TRACE` does nothing while a host sink
is installed.

With no sink installed, each emission site is one inlined atomic load and a
branch, and nothing is formatted or allocated. The cache and layer counters
are read only inside that branch.

### One native Perfetto trace for the framework and the renderer

A development build can put the renderer's tracks in the same Perfetto session
as the framework's. It is off by default: nothing is fetched, compiled or
linked for it unless you configure it, and no shippable build has it.

1. Configure a mirror build with tracing on. The configure fetches Perfetto
   v57.2 and refuses any archive whose SHA-256 differs from the pin in
   `cmake/VellumTracing.cmake`:

   ```sh
   cmake -S . -B build-trace -G Ninja -DCMAKE_BUILD_TYPE=Release \
       -DVELLUM_ENABLE_MIRROR=ON -DVELLUM_ENABLE_TRACING=ON \
       -DVELLUM_SKIA_ARCHIVE=<skia-build-mac-universal-gpu-release.zip>
   cmake --build build-trace
   ```

2. In your app, start the session and install the adapter. Both are no-ops
   returning false in a build without tracing, so no `#if` is needed:

   ```cpp
   #include <vellum/render/frame_trace_adapter.hpp>
   #include <vellum/runtime/trace_session.hpp>

   vellum::runtime::Tracing::start({}, "/tmp/vellum.pftrace");
   vellum::render::install_native_frame_trace();
   // ... render ...
   vellum::render::remove_native_frame_trace();
   vellum::runtime::Tracing::stop();
   ```

3. Open `/tmp/vellum.pftrace` in https://ui.perfetto.dev. Framework slices and
   the renderer's `graphics.*` slices, counters and instants share the file;
   the renderer's tracks are in the `render` category.

`install_native_frame_trace` installs the process-wide frame trace sink, so it
replaces a sink you set before it and `VELLUM_FRAME_TRACE` stands down while it
is installed. `vellum.framework.native-trace` runs this path on a real surface,
reads the file back and checks both sets of slices; run it with
`ctest --test-dir build-trace -R "tracing|native-trace"`.

## Damage overlay

```sh
VELLUM_DAMAGE_OVERLAY=1 ./your-vellum-app
```

or `SkiaDawnSurface::Config::damage_overlay = true`, or
`set_damage_overlay(true)` on a running surface (it takes effect from the next
frame). Each presented frame gets a two pixel translucent outline around every
damage rectangle it repainted: pink for a partial repaint, orange for a full
repaint, which outlines the whole frame. A frame with no damage shows no
outline.

The outline is drawn after the frame is complete, into the swapchain texture
for a native surface, or into a scratch copy for a capture of an offscreen
surface. The retained surface, the damage tracker and the caches never see
it, so the overlay does not change what is damaged or drawn next frame, and
turning it off restores byte-identical output. A capture shows the outlines of
the frame it captures. A surface created with the overlay off and turned on
later has no outline until its next frame.
