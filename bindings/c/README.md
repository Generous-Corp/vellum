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
  `struct_size`. The caller zero-initializes the struct and sets `struct_size`
  to the `sizeof` it was compiled against. Each input struct (surface and app
  configs, pointer and key events) has a frozen v1 size ending at its last
  field, with no tail padding. A smaller size is refused with
  `VELLUM_HOST_ERROR_VERSION`. A larger size from a newer header is accepted
  only when every byte past v1 is zero; a newer field set to anything else is
  refused with `VELLUM_HOST_ERROR_VERSION` rather than ignored. An input
  `struct_size` above 4096 is refused with `VELLUM_HOST_ERROR_INVALID_ARGUMENT`.
  Output structs (surface info, image, content stats, pump result,
  accessibility node, text input) also have frozen v1 sizes ending at their
  last field. They accept any size of at least v1, and only their v1 fields are
  written.
- **Errors.** Every fallible call returns a `vellum_host_status`.
  `vellum_host_last_error()` returns the message of the last failed call on the
  calling thread. Every status-returning call clears it on entry;
  `vellum_host_abi_version()` and `vellum_host_last_error()` leave it alone. The
  pointer stays valid until the next status-returning call on that thread. No
  C++ exception crosses the boundary.
- **Ownership.** `vellum_host_app_create` and `vellum_host_surface_create` hand
  the caller an opaque handle that it releases with the matching `_destroy`
  call, which returns a status; destroying `NULL` succeeds and does nothing. A surface and an application are
  independent, so either may be destroyed first. The `CAMetalLayer` passed as
  `metal_layer` is borrowed: the caller keeps it alive until the surface is
  destroyed. The bundle bytes and every input string are copied before the call
  returns.
- **Handle validity.** Live handles are tracked in a process-wide registry
  under a mutex. Every entry point resolves its handle there and never reads a
  handle's memory to decide whether it may use it. Destroy removes the handle
  from the registry before freeing it, so a concurrent or later call on a
  destroyed handle returns `VELLUM_HOST_ERROR_INVALID_ARGUMENT` (or
  `VELLUM_HOST_ERROR_WRONG_THREAD` while it is still live) instead of touching
  freed memory. A destroyed address that a later create happens to reuse is
  indistinguishable from the new handle.
- **Borrowed outputs.** The contract is the strictest rule: every borrowed
  string, `vellum_host_last_error()` included, is valid only until the next
  `vellum_host_*` call on the same thread. Strings in surface info belong to
  the surface, and strings in accessibility and text-input records belong to
  the application. The pixels in `vellum_host_image` belong to the surface and
  are invalidated by its next render, resize, capture or destroy. Copy anything
  kept longer. Output structs are zeroed on entry, so a failed call never leaves
  stale pointers or sizes behind.
- **Threading.** A handle is confined to the thread that created it. A call
  from any other thread, destroy included, returns
  `VELLUM_HOST_ERROR_WRONG_THREAD` without touching the handle; a destroy
  refused that way leaves the handle alive for its owner to destroy. The check
  compares `std::thread::id` values, so it is best-effort: it catches a
  mistaken cross-thread call but is not a synchronization primitive, and two
  threads racing on one handle remain undefined.
- **Surfaces.** `metal_layer` set to a `CAMetalLayer` renders to that layer; a
  `NULL` layer creates an offscreen target for deterministic capture. Creation
  fails closed with `VELLUM_HOST_ERROR_UNAVAILABLE` when the GPU target does not
  match the request or reports a fallback. The Dawn proc-table bootstrap is
  registered inside the library; the host has no Dawn step.
- **Pointer.** Only a `VELLUM_HOST_POINTER_DOWN` with button
  `VELLUM_HOST_POINTER_BUTTON_PRIMARY` (0) focuses a text input or fires a
  press. Other buttons and phases are accepted and ignored in v1.
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
