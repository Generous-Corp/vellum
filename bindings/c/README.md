# Host boundary C ABI

`include/vellum/c/vellum_host.h` is a small, versioned C interface for embedding
a Vellum application in a host written in another language. It covers surfaces,
application loading, input forwarding and the semantic projections a native host
needs, and nothing else. No Skia, Dawn, scene or C++ standard library type
crosses it. The installed `libvellum-host-c.dylib` exports exactly the functions
the header declares; `tests/check_exports.py` enforces that.

The SDK installs the header, the library, the `Vellum::HostC` CMake target and a
relocatable `lib/pkgconfig/vellum-host-c.pc`, so CMake, SwiftPM and plain
`pkg-config` consumers can all build from the installed prefix alone.

## Contract

- **Version.** `vellum_host_abi_version()` returns `VELLUM_HOST_ABI_VERSION`.
  A host compares the two before anything else.
- **Versioned structs.** Every config, event and output struct starts with
  `struct_size`. The caller sets it to `sizeof` the struct it was compiled
  against. A size smaller than ABI v1 is refused with
  `VELLUM_HOST_ERROR_VERSION`; a larger size from a newer header is accepted and
  only the v1 fields are read or written.
- **Errors.** Every fallible call returns a `vellum_host_status`.
  `vellum_host_last_error()` returns the message of the last failed call on the
  calling thread. Each call clears it on entry, and the pointer stays valid until
  the next call on that thread. No C++ exception crosses the boundary.
- **Ownership.** `vellum_host_app_create` and `vellum_host_surface_create` hand
  the caller an opaque handle that it releases with the matching `_destroy`
  call; destroying `NULL` is a no-op. A surface and an application are
  independent, so either may be destroyed first. The `CAMetalLayer` passed as
  `metal_layer` is borrowed: the caller keeps it alive until the surface is
  destroyed. The bundle bytes and every input string are copied before the call
  returns.
- **Borrowed outputs.** Strings in `vellum_host_surface_info` belong to the
  surface and stay valid until it is destroyed. Strings in accessibility and
  text-input records belong to the application and stay valid until its next
  mutating call (create, pump, dispatch, pointer, key, insert text, focus or
  accessibility perform). The pixels in `vellum_host_image` belong to the
  surface and stay valid until its next render, resize, capture or destroy.
  Copy anything kept longer.
- **Threading.** A handle is confined to the thread that created it. A call
  from any other thread returns `VELLUM_HOST_ERROR_WRONG_THREAD` without
  touching the handle.
- **Surfaces.** `metal_layer` set to a `CAMetalLayer` renders to that layer; a
  `NULL` layer creates an offscreen target for deterministic capture. Creation
  fails closed with `VELLUM_HOST_ERROR_UNAVAILABLE` when the GPU target does not
  match the request or reports a fallback. The Dawn proc-table bootstrap is
  registered inside the library; the host has no Dawn step.
- **Text.** All text is UTF-8 and invalid UTF-8 is refused. Selection offsets in
  text-input records are UTF-16 code units, matching JavaScript and AppKit.

## Coverage

| Area | Functions |
| --- | --- |
| Surface | `surface_create`, `surface_resize`, `surface_render`, `surface_capture_rgba`, `surface_info_get`, `surface_destroy` |
| Application | `app_create`, `app_viewport`, `app_pump`, `app_dispatch`, `app_destroy` |
| Input | `app_pointer`, `app_focus`, `app_key`, `app_insert_text` |
| Accessibility | `app_accessibility_count`, `app_accessibility_node`, `app_accessibility_action`, `app_accessibility_perform` |
| Text input | `app_text_input_count`, `app_text_input` |
| Capture check | `content_floor` applies the renderer's own content-floor rule to RGBA bytes |

Composition (marked text) events and state snapshot/restore are not part of v1.
