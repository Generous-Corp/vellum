// Links only the published gpu-runtime package and the locked Dawn build.

#include <gpu_runtime/dawn_bootstrap.hpp>
#include <gpu_runtime/dawn_native_bootstrap.hpp>
#include <gpu_runtime/gpu_compute.hpp>
#include <gpu_runtime/gpu_proc_installer.hpp>

#include <cstdio>
#include <string>

int main() {
    std::string error;
    if (gpu_runtime::runtime_version() != EXPECTED_VERSION) {
        std::fprintf(stderr, "package reports gpu-runtime %.*s\n",
                     static_cast<int>(gpu_runtime::runtime_version().size()),
                     gpu_runtime::runtime_version().data());
        return 1;
    }
    if (!gpu_runtime::register_native_dawn_bootstrap("com.example.artifact-consumer", &error) ||
        !gpu_runtime::ensure_dawn_procs(&error)) {
        std::fprintf(stderr, "Dawn bootstrap failed: %s\n", error.c_str());
        return 1;
    }
    const auto record = gpu_runtime::dawn_bootstrap_record();
    if (!record || record->bundle != "com.example.artifact-consumer" ||
        record->revision != gpu_runtime::native_dawn_revision()) {
        std::fprintf(stderr, "the bootstrap record does not name this bundle\n");
        return 1;
    }
    auto compute = gpu_runtime::GpuCompute::create();
    if (!compute) {
        std::fprintf(stderr, "GpuCompute::create() returned null\n");
        return 1;
    }
    std::printf("gpu-runtime artifact consumer: ok (Dawn %s, compute %s)\n", record->revision.c_str(),
                compute->initialize_standalone() ? "up" : "no adapter");
    return 0;
}
