// widget_bridge/gpu_common.cpp - shared native GPU descriptor helpers for WidgetBridge.

#include "gpu_common.hpp"

#if __has_include(<vellum/render/gpu_surface.hpp>)
#include <vellum/render/gpu_surface.hpp>
#define PULP_WIDGET_BRIDGE_HAS_GPU_SURFACE 1
#else
#define PULP_WIDGET_BRIDGE_HAS_GPU_SURFACE 0
#endif

#include <vellum/runtime/log.hpp>

#include <chrono>
#include <memory>
#include <mutex>
#include <string_view>
#include <thread>

namespace vellum::view::detail {

choc::value::Value compilation_messages_to_value(const std::vector<GpuCompilationMessage>& messages) {
    auto list = choc::value::createEmptyArray();
    for (const auto& message : messages) {
        auto entry = choc::value::createObject("");
        entry.addMember("type", choc::value::createString(message.type));
        entry.addMember("message", choc::value::createString(message.message));
        entry.addMember("lineNum", choc::value::createInt64(static_cast<int64_t>(message.line_num)));
        entry.addMember("linePos", choc::value::createInt64(static_cast<int64_t>(message.line_pos)));
        entry.addMember("offset", choc::value::createInt64(static_cast<int64_t>(message.offset)));
        entry.addMember("length", choc::value::createInt64(static_cast<int64_t>(message.length)));
        list.addArrayElement(entry);
    }
    return list;
}

bool ScriptErrorScopes::pop(std::optional<GpuScopedError>& error) {
    if (stack.empty()) return false;
    error = std::move(stack.back().error);
    stack.pop_back();
    return true;
}

bool ScriptErrorScopes::capture(GpuScopedError error) {
    for (auto it = stack.rbegin(); it != stack.rend(); ++it) {
        if (it->filter != error.type) continue;
        if (!it->error) it->error = std::move(error);
        return true;
    }
    return false;
}

#ifdef PULP_HAS_SKIA
namespace {

std::string to_std_string(wgpu::StringView view) {
    if (view.data == nullptr) return {};
    if (view.length == wgpu::kStrlen) return std::string(view.data);
    return std::string(view.data, view.length);
}

const char* script_error_type(wgpu::ErrorType type) {
    switch (type) {
        case wgpu::ErrorType::Validation: return "validation";
        case wgpu::ErrorType::OutOfMemory: return "out-of-memory";
        default: return "internal";
    }
}

// Pumps the instance until `done` holds or the deadline passes. The callbacks
// here are AllowProcessEvents, so they only run while events are processed.
template <typename Done>
bool pump_events(const wgpu::Instance* instance, Done&& done) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    for (;;) {
        if (instance != nullptr) instance->ProcessEvents();
        if (done()) return true;
        if (std::chrono::steady_clock::now() >= deadline) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

struct PoppedScopes {
    std::mutex mutex;
    int remaining = 3;
    std::vector<GpuScopedError> errors;

    void record(wgpu::PopErrorScopeStatus status, wgpu::ErrorType type, wgpu::StringView message) {
        std::lock_guard<std::mutex> lock(mutex);
        --remaining;
        if (status == wgpu::PopErrorScopeStatus::Success && type != wgpu::ErrorType::NoError) {
            errors.push_back({script_error_type(type), to_std_string(message), {}});
        }
    }
    bool done() {
        std::lock_guard<std::mutex> lock(mutex);
        return remaining <= 0;
    }
};

}  // namespace

DeviceErrorScopeGuard::DeviceErrorScopeGuard(ScriptErrorScopes& scopes, const wgpu::Device& device,
                                             const wgpu::Instance* instance, const char* operation)
    : scopes_(scopes), device_(device), instance_(instance), operation_(operation) {
    device_.PushErrorScope(wgpu::ErrorFilter::Internal);
    device_.PushErrorScope(wgpu::ErrorFilter::OutOfMemory);
    device_.PushErrorScope(wgpu::ErrorFilter::Validation);
}

DeviceErrorScopeGuard::~DeviceErrorScopeGuard() { finish(); }

void DeviceErrorScopeGuard::attach_diagnostics(std::vector<GpuCompilationMessage> diagnostics) {
    diagnostics_ = std::move(diagnostics);
}

void DeviceErrorScopeGuard::finish() {
    if (finished_) return;
    finished_ = true;
    auto popped = std::make_shared<PoppedScopes>();
    for (int i = 0; i < 3; ++i) {
        device_.PopErrorScope(wgpu::CallbackMode::AllowProcessEvents,
                              [popped](wgpu::PopErrorScopeStatus status, wgpu::ErrorType type,
                                       wgpu::StringView message) {
                                  popped->record(status, type, message);
                              });
    }
    if (!pump_events(instance_, [&] { return popped->done(); })) {
        runtime::log_error("PULP_WEBGPU_BRIDGE: {}: error scopes did not resolve", operation_);
        return;
    }
    std::vector<GpuScopedError> errors;
    {
        std::lock_guard<std::mutex> lock(popped->mutex);
        errors = popped->errors;
    }
    for (auto& error : errors) {
        if (error.type == "validation") error.diagnostics = diagnostics_;
        const std::string text = error.message;
        const std::string type = error.type;
        if (!scopes_.capture(std::move(error))) {
            runtime::log_error("PULP_WEBGPU_BRIDGE: {}: uncaptured {} error: {}", operation_, type, text);
        }
    }
}

std::vector<GpuCompilationMessage> shader_compilation_messages(const wgpu::ShaderModule& module,
                                                               const wgpu::Instance* instance) {
    struct Result {
        std::mutex mutex;
        bool done = false;
        std::vector<GpuCompilationMessage> messages;
    };
    auto result = std::make_shared<Result>();
    module.GetCompilationInfo(
        wgpu::CallbackMode::AllowProcessEvents,
        [result](wgpu::CompilationInfoRequestStatus status, const wgpu::CompilationInfo* info) {
            std::lock_guard<std::mutex> lock(result->mutex);
            if (status == wgpu::CompilationInfoRequestStatus::Success && info != nullptr) {
                for (size_t i = 0; i < info->messageCount; ++i) {
                    const auto& m = info->messages[i];
                    const char* type = m.type == wgpu::CompilationMessageType::Error     ? "error"
                                       : m.type == wgpu::CompilationMessageType::Warning ? "warning"
                                                                                         : "info";
                    result->messages.push_back(
                        {type, to_std_string(m.message), m.lineNum, m.linePos, m.offset, m.length});
                }
            }
            result->done = true;
        });
    pump_events(instance, [&] {
        std::lock_guard<std::mutex> lock(result->mutex);
        return result->done;
    });
    std::lock_guard<std::mutex> lock(result->mutex);
    return result->messages;
}
#endif

#if PULP_WIDGET_BRIDGE_HAS_GPU_SURFACE
WidgetBridgeGpuInfo widget_bridge_gpu_info_from_adapter(const render::GpuSurface::AdapterInfo& info) {
    WidgetBridgeGpuInfo result;
    result.available = info.available;
    result.native_bridge = info.native_bridge;
    result.backend = info.backend;
    result.backend_type = info.backend_type;
    result.name = info.name;
    result.preferred_canvas_format = info.preferred_canvas_format;
    result.vendor = info.vendor;
    result.architecture = info.architecture;
    result.description = info.description;
    return result;
}
#endif

choc::value::Value gpu_adapter_info_to_value(const WidgetBridgeGpuInfo& info) {
    auto value = choc::value::createObject("");
    value.addMember("vendor", choc::value::createString(info.vendor));
    value.addMember("architecture", choc::value::createString(info.architecture));
    value.addMember("description", choc::value::createString(info.description));
    value.addMember("backendType", choc::value::createString(info.backend_type));
    return value;
}

choc::value::Value gpu_descriptor_to_value(const WidgetBridgeGpuInfo& info) {
    auto value = choc::value::createObject("");
    value.addMember("available", choc::value::createBool(info.available));
    value.addMember("nativeBridge", choc::value::createBool(info.native_bridge));
    value.addMember("backend", choc::value::createString(info.backend));
    value.addMember("backendType", choc::value::createString(info.backend_type));
    value.addMember("name", choc::value::createString(info.name));
    value.addMember("preferredCanvasFormat", choc::value::createString(info.preferred_canvas_format));
    value.addMember("info", gpu_adapter_info_to_value(info));
    return value;
}

WidgetBridgeGpuInfo widget_bridge_gpu_info(render::GpuSurface* gpu_surface) {
#if PULP_WIDGET_BRIDGE_HAS_GPU_SURFACE
    if (gpu_surface != nullptr) {
        auto info = gpu_surface->adapter_info();
        if (info.available) {
            return widget_bridge_gpu_info_from_adapter(info);
        }
    }
#endif

    WidgetBridgeGpuInfo info{};
    info.available = true;
    info.backend = "Dawn/WebGPU";
    info.preferred_canvas_format = "bgra8unorm";
    return info;
}

std::string gpu_host_object_update_script(const WidgetBridgeGpuInfo& info) {
    const auto string_literal = [] (std::string_view text) {
        return choc::json::toString(choc::value::createString(std::string(text)));
    };
    const auto bool_literal = [] (bool value) {
        return value ? "true" : "false";
    };

    std::string script;
    script.reserve(512);
    script += "if (typeof navigatorGPU !== 'undefined' && navigatorGPU) {";
    script += "navigatorGPU.backend = " + string_literal(info.backend) + ";";
    script += "navigatorGPU.backendType = " + string_literal(info.backend_type) + ";";
    script += "navigatorGPU.available = " + std::string(bool_literal(info.available)) + ";";
    script += "navigatorGPU.nativeBridge = " + std::string(bool_literal(info.native_bridge)) + ";";
    script += "}";
    return script;
}

} // namespace vellum::view::detail
