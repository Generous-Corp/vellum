#pragma once

namespace vellum::render {

/// Routes the renderer's per-frame trace signals (`vellum/graphics/frame_trace.hpp`)
/// into the process's native trace session (`vellum/runtime/trace_session.hpp`), so
/// one Perfetto trace carries the framework's slices and the renderer's `graphics.*`
/// slices, counters and instants. Slices and instants land in the "render"
/// category; counters become counter tracks named after the renderer's counters.
///
/// Does nothing and returns false unless the build has `VELLUM_ENABLE_TRACING`
/// on, so a caller needs no `#if`. It installs the process-wide frame trace sink,
/// replacing any sink installed before it, and records only while a session is
/// active (`Tracing::start`).
bool install_native_frame_trace() noexcept;

/// Removes the sink this adapter installed. A no-op when another sink has
/// replaced it, or when it installed none, so a sink a host installed on its
/// own is left alone.
void remove_native_frame_trace() noexcept;

/// Whether this adapter's sink is the one now installed.
[[nodiscard]] bool native_frame_trace_installed() noexcept;

}  // namespace vellum::render
