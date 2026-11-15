#pragma once

#include <vellum/view/widget_bridge.hpp>

#ifdef PULP_HAS_SKIA
#include "webgpu/webgpu_cpp.h"
#endif

#include <cstdint>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace vellum::view::detail {

// One shader compiler message with its source position, as WebGPU's
// GPUCompilationMessage reports it.
struct GpuCompilationMessage {
    std::string type;  // "error", "warning" or "info"
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

// The error-scope stack script sees through device.pushErrorScope /
// popErrorScope. Native device work runs inside its own Dawn scopes and routes
// what they catch here, so script receives its own errors and the device's
// uncaptured-error callback does not fire for them.
struct ScriptErrorScopes {
    struct Scope {
        std::string filter;
        std::optional<GpuScopedError> error;
    };
    std::vector<Scope> stack;

    void push(std::string filter) { stack.push_back({std::move(filter), std::nullopt}); }
    // Removes the innermost scope. Returns false when the stack is empty.
    bool pop(std::optional<GpuScopedError>& error);
    // Offers an error to the innermost scope with a matching filter. Returns
    // false when no scope matches, so the caller can report it as uncaptured.
    // A matching scope keeps only its first error, as the WebGPU spec says.
    bool capture(GpuScopedError error);
};

struct WidgetBridgeGpuInfo {
    bool available = false;
    bool native_bridge = false;
    std::string backend;
    std::string backend_type;
    std::string name;
    std::string preferred_canvas_format;
    std::string vendor;
    std::string architecture;
    std::string description;
};

#ifdef PULP_HAS_SKIA
// Runs one piece of native device work inside validation, out-of-memory and
// internal Dawn scopes. finish() (or the destructor) pops them, waits for the
// results and hands each error to `scopes`; an error no script scope claims is
// logged. Without it such an error reaches the device's uncaptured-error
// callback and script never learns of it.
class DeviceErrorScopeGuard {
public:
    DeviceErrorScopeGuard(ScriptErrorScopes& scopes, const wgpu::Device& device,
                          const wgpu::Instance* instance, const char* operation);
    ~DeviceErrorScopeGuard();
    DeviceErrorScopeGuard(const DeviceErrorScopeGuard&) = delete;
    DeviceErrorScopeGuard& operator=(const DeviceErrorScopeGuard&) = delete;

    // Shader compiler messages to attach to a validation error caught here.
    void attach_diagnostics(std::vector<GpuCompilationMessage> diagnostics);
    void finish();

private:
    ScriptErrorScopes& scopes_;
    wgpu::Device device_;
    const wgpu::Instance* instance_;
    const char* operation_;
    std::vector<GpuCompilationMessage> diagnostics_;
    bool finished_ = false;
};

// The compiler messages Dawn recorded for `module`, with line and column.
std::vector<GpuCompilationMessage> shader_compilation_messages(const wgpu::ShaderModule& module,
                                                               const wgpu::Instance* instance);
#endif

choc::value::Value compilation_messages_to_value(const std::vector<GpuCompilationMessage>& messages);
choc::value::Value gpu_adapter_info_to_value(const WidgetBridgeGpuInfo& info);
choc::value::Value gpu_descriptor_to_value(const WidgetBridgeGpuInfo& info);
WidgetBridgeGpuInfo widget_bridge_gpu_info(render::GpuSurface* gpu_surface);
std::string gpu_host_object_update_script(const WidgetBridgeGpuInfo& info);

} // namespace vellum::view::detail

namespace vellum::view {

struct WidgetBridge::NativeGpuBridgeState {
    struct CanvasContextState {
        uint32_t width = 1;
        uint32_t height = 1;
        std::string format = "bgra8unorm";
        uint32_t usage = 0;
        std::string alpha_mode = "opaque";
#ifdef PULP_HAS_SKIA
        wgpu::Texture texture;
#endif
        bool configured = false;
    };

    struct TextureState {
        uint32_t width = 1;
        uint32_t height = 1;
        uint32_t depth_or_array_layers = 1;
        std::string format = "bgra8unorm";
        uint32_t usage = 0;
        uint32_t mip_level_count = 1;
        uint32_t sample_count = 1;
#ifdef PULP_HAS_SKIA
        wgpu::Texture texture;
#endif
        bool configured = false;
    };

    std::unordered_map<std::string, CanvasContextState> canvases;
    std::unordered_map<std::string, TextureState> textures;
    uint64_t next_texture_id = 1;
    detail::ScriptErrorScopes error_scopes;
};

} // namespace vellum::view
