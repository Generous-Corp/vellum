// widget_bridge/gpu_error_scopes.cpp - script-visible WebGPU error scopes.

#include "gpu_error_scopes.hpp"

#if __has_include(<vellum/render/gpu_surface.hpp>) && __has_include("webgpu/webgpu_cpp.h")
#include <vellum/render/gpu_surface.hpp>
#include "webgpu/webgpu_cpp.h"
#define WIDGET_BRIDGE_HAS_DEVICE_SCOPES 1
#else
#define WIDGET_BRIDGE_HAS_DEVICE_SCOPES 0
#endif

#include <vellum/runtime/log.hpp>

#include <chrono>
#include <memory>
#include <mutex>
#include <thread>

namespace vellum::view::detail {

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

#if WIDGET_BRIDGE_HAS_DEVICE_SCOPES

namespace {

wgpu::Device* device_of(render::GpuSurface* surface) {
    if (surface == nullptr) return nullptr;
    auto* device = static_cast<wgpu::Device*>(surface->dawn_device_handle());
    return device != nullptr && *device ? device : nullptr;
}

const wgpu::Instance* instance_of(render::GpuSurface* surface) {
    return static_cast<const wgpu::Instance*>(surface->dawn_instance_handle());
}

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

// The compiler messages Dawn recorded for `module`, with line and column.
std::vector<GpuCompilationMessage> compilation_messages(const wgpu::ShaderModule& module,
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

} // namespace

DeviceErrorScopeGuard::DeviceErrorScopeGuard(ScriptErrorScopes& scopes, render::GpuSurface* surface,
                                             const char* operation)
    : scopes_(scopes), surface_(surface), operation_(operation) {
    auto* device = device_of(surface_);
    if (device == nullptr) return;
    active_ = true;
    device->PushErrorScope(wgpu::ErrorFilter::Internal);
    device->PushErrorScope(wgpu::ErrorFilter::OutOfMemory);
    device->PushErrorScope(wgpu::ErrorFilter::Validation);
}

DeviceErrorScopeGuard::~DeviceErrorScopeGuard() { finish(); }

void DeviceErrorScopeGuard::attach_diagnostics(std::vector<GpuCompilationMessage> diagnostics) {
    diagnostics_ = std::move(diagnostics);
}

void DeviceErrorScopeGuard::finish() {
    if (!active_) return;
    active_ = false;
    auto* device = device_of(surface_);
    if (device == nullptr) return;
    auto popped = std::make_shared<PoppedScopes>();
    for (int i = 0; i < 3; ++i) {
        device->PopErrorScope(wgpu::CallbackMode::AllowProcessEvents,
                              [popped](wgpu::PopErrorScopeStatus status, wgpu::ErrorType type,
                                       wgpu::StringView message) { popped->record(status, type, message); });
    }
    if (!pump_events(instance_of(surface_), [&] { return popped->done(); })) {
        runtime::log_error("webgpu binding: {}: error scopes did not resolve", operation_);
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
            runtime::log_error("webgpu binding: {}: uncaptured {} error: {}", operation_, type, text);
        }
    }
}

choc::value::Value validate_shader_module(ScriptErrorScopes& scopes, render::GpuSurface* surface,
                                          const std::string& code) {
    auto result = choc::value::createObject("");
    result.addMember("ok", choc::value::createBool(true));
    result.addMember("messages", compilation_messages_to_value({}));
    auto* device = device_of(surface);
    if (device == nullptr || code.empty()) return result;

    DeviceErrorScopeGuard guard(scopes, surface, "shader module");
    wgpu::ShaderSourceWGSL wgsl{};
    wgsl.code = code.c_str();
    wgpu::ShaderModuleDescriptor module_desc{};
    module_desc.nextInChain = &wgsl;
    auto module = device->CreateShaderModule(&module_desc);
    auto messages = compilation_messages(module, instance_of(surface));
    bool failed = false;
    for (const auto& message : messages) failed = failed || message.type == "error";
    result.setMember("ok", choc::value::createBool(!failed));
    result.setMember("messages", compilation_messages_to_value(messages));
    guard.attach_diagnostics(std::move(messages));
    return result;
}

#else

DeviceErrorScopeGuard::DeviceErrorScopeGuard(ScriptErrorScopes& scopes, render::GpuSurface* surface,
                                             const char* operation)
    : scopes_(scopes), surface_(surface), operation_(operation) {}
DeviceErrorScopeGuard::~DeviceErrorScopeGuard() = default;
void DeviceErrorScopeGuard::attach_diagnostics(std::vector<GpuCompilationMessage> diagnostics) {
    diagnostics_ = std::move(diagnostics);
}
void DeviceErrorScopeGuard::finish() {}

choc::value::Value validate_shader_module(ScriptErrorScopes&, render::GpuSurface*, const std::string&) {
    auto result = choc::value::createObject("");
    result.addMember("ok", choc::value::createBool(true));
    result.addMember("messages", compilation_messages_to_value({}));
    return result;
}

#endif

} // namespace vellum::view::detail
