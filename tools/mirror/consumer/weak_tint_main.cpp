#include <vellum/graphics/dawn_native_bootstrap.hpp>

#include <dawn/webgpu_cpp.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <string_view>
#include <utility>

// The locked Tint build stores this weak matcher in its WGSL intrinsic table.
// A vector binary expression reaches it during shader-module validation.
extern "C" [[gnu::weak, gnu::used, gnu::visibility("default")]]
void conflicting_tint_matcher()
    asm("__ZN4tint4core9intrinsic19TemplateTypeMatcherILm0EE7matcherMUlRNS1_10MatchStateEPKNS0_4type4TypeEE_8__invokeES5_S9_");

void conflicting_tint_matcher() {
    std::fputs("provider called the executable's weak Tint matcher\n", stderr);
    std::exit(86);
}

namespace {

int fail(const char* message) {
    std::fprintf(stderr, "%s\n", message);
    return 1;
}

}  // namespace

int main(int argc, char** argv) {
    const bool live_gpu = argc == 2 && std::string_view(argv[1]) == "--live-gpu";
    if (argc != 1 && !live_gpu)
        return fail("usage: vellum-weak-tint-consumer [--live-gpu]");

    std::string error;
    if (!vellum::app_host::register_native_dawn_bootstrap(&error))
        return fail(error.c_str());

    wgpu::InstanceDescriptor instance_desc{};
    const wgpu::InstanceFeatureName timed_wait = wgpu::InstanceFeatureName::TimedWaitAny;
    instance_desc.requiredFeatureCount = 1;
    instance_desc.requiredFeatures = &timed_wait;
    const auto instance = wgpu::CreateInstance(&instance_desc);
    if (!instance)
        return fail("could not create a Dawn instance");

    constexpr std::uint64_t timeout_ns = 10'000'000'000;
    wgpu::RequestAdapterOptions options{};
    options.backendType = live_gpu ? wgpu::BackendType::Metal : wgpu::BackendType::Null;
    wgpu::Adapter adapter;
    auto requested = instance.RequestAdapter(&options, wgpu::CallbackMode::WaitAnyOnly,
        [&](wgpu::RequestAdapterStatus status, wgpu::Adapter value, wgpu::StringView) {
            if (status == wgpu::RequestAdapterStatus::Success)
                adapter = std::move(value);
        });
    if (instance.WaitAny(requested, timeout_ns) != wgpu::WaitStatus::Success || !adapter)
        return fail("could not get the requested Dawn adapter");

    wgpu::Device device;
    auto created = adapter.RequestDevice(nullptr, wgpu::CallbackMode::WaitAnyOnly,
        [&](wgpu::RequestDeviceStatus status, wgpu::Device value, wgpu::StringView) {
            if (status == wgpu::RequestDeviceStatus::Success)
                device = std::move(value);
        });
    if (instance.WaitAny(created, timeout_ns) != wgpu::WaitStatus::Success || !device)
        return fail("could not create the Dawn device");

    wgpu::ShaderSourceWGSL source{};
    source.code = R"(
        @group(0) @binding(0) var<storage, read_write> results: array<vec4f>;
        @compute @workgroup_size(1)
        fn main(@builtin(local_invocation_index) index: u32) {
            let sum = vec4f(f32(index)) + vec4f(1.0);
            results[index] = sum;
        }
    )";
    wgpu::ShaderModuleDescriptor descriptor{};
    descriptor.nextInChain = &source;
    device.PushErrorScope(wgpu::ErrorFilter::Validation);
    const auto module = device.CreateShaderModule(&descriptor);
    bool valid = false;
    const auto checked = device.PopErrorScope(wgpu::CallbackMode::WaitAnyOnly,
        [&](wgpu::PopErrorScopeStatus status, wgpu::ErrorType type, wgpu::StringView message) {
            if (status == wgpu::PopErrorScopeStatus::Success && type == wgpu::ErrorType::NoError)
                valid = true;
            else if (message.data)
                std::fprintf(stderr, "%.*s\n", static_cast<int>(message.length), message.data);
        });
    if (instance.WaitAny(checked, timeout_ns) != wgpu::WaitStatus::Success || !module || !valid)
        return fail("WGSL shader-module compilation failed");

    std::puts(live_gpu ? "WGSL compiled with the Metal adapter" : "WGSL compiled with the Null adapter");
    return 0;
}
