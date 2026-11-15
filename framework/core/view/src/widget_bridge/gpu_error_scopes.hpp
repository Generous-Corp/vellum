#pragma once

// widget_bridge/gpu_error_scopes.hpp - WebGPU error scopes for the scripted
// binding.
//
// Script's device.pushErrorScope / popErrorScope stack lives here. Native device
// work runs inside its own device scopes and routes what they catch to the
// innermost script scope with a matching filter, so script receives its own
// errors and the device's uncaptured-error callback does not fire for them.

#include <vellum/view/widget_bridge.hpp>

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace vellum::view::detail {

// One shader compiler message with its source position, as WebGPU's
// GPUCompilationMessage reports it.
struct GpuCompilationMessage {
    std::string type; // "error", "warning" or "info"
    std::string message;
    uint64_t line_num = 0;
    uint64_t line_pos = 0;
    uint64_t offset = 0;
    uint64_t length = 0;
};

// What a popped scope hands back to script: a WebGPU error category
// ("validation", "out-of-memory" or "internal"), its text, and any shader
// compiler messages that explain it.
struct GpuScopedError {
    std::string type;
    std::string message;
    std::vector<GpuCompilationMessage> diagnostics;
};

struct ScriptErrorScopes {
    struct Scope {
        std::string filter;
        std::optional<GpuScopedError> error;
    };
    std::vector<Scope> stack;
    // Errors no script scope claimed, waiting for script to dispatch them as
    // the device's `uncapturederror` event.
    std::vector<GpuScopedError> uncaptured;

    void push(std::string filter) { stack.push_back({std::move(filter), std::nullopt}); }
    // Removes the innermost scope. Returns false when the stack is empty.
    bool pop(std::optional<GpuScopedError>& error);
    // Offers an error to the innermost scope with a matching filter. Returns
    // false when no scope matches. A matching scope keeps only its first error,
    // as the WebGPU spec says.
    bool capture(GpuScopedError error);
};

// Runs one piece of native device work inside validation, out-of-memory and
// internal device scopes. finish() (or the destructor) pops them, waits for the
// results and hands each error to `scopes`; an error no script scope claims is
// logged. Without it such an error reaches the device's uncaptured-error
// callback and script never learns of it. Does nothing when the surface has no
// native device.
class DeviceErrorScopeGuard {
public:
    DeviceErrorScopeGuard(ScriptErrorScopes& scopes, render::GpuSurface* surface, const char* operation);
    ~DeviceErrorScopeGuard();
    DeviceErrorScopeGuard(const DeviceErrorScopeGuard&) = delete;
    DeviceErrorScopeGuard& operator=(const DeviceErrorScopeGuard&) = delete;

    // Shader compiler messages to attach to a validation error caught here.
    void attach_diagnostics(std::vector<GpuCompilationMessage> diagnostics);
    void finish();

private:
    ScriptErrorScopes& scopes_;
    render::GpuSurface* surface_;
    const char* operation_;
    std::vector<GpuCompilationMessage> diagnostics_;
    bool active_ = false;
};

// Takes the queued unclaimed errors as an array of {type, message, diagnostics}.
choc::value::Value take_uncaptured_errors(ScriptErrorScopes& scopes);

choc::value::Value compilation_messages_to_value(const std::vector<GpuCompilationMessage>& messages);

// Compiles `code` as a WGSL module on the surface's native device, inside a
// guard, and returns {ok, messages} with each compiler message's line and
// column. {ok: true, messages: []} when there is no native device.
choc::value::Value validate_shader_module(ScriptErrorScopes& scopes, render::GpuSurface* surface,
                                          const std::string& code);

} // namespace vellum::view::detail
