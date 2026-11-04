cmake_minimum_required(VERSION 3.24)
project(GpuRuntimeInstallConsumer LANGUAGES CXX)

# Consumes an installed VellumGpuRuntime package: the exact version, the
# device API target and the shipped renderer lock.
find_package(VellumGpuRuntime ${EXPECTED_VERSION} EXACT REQUIRED)

gpu_runtime_read_renderer_lock(PLATFORM macos-arm64 PREFIX installed
    LOCK "${VellumGpuRuntime_RENDERER_LOCK}")
gpu_runtime_read_renderer_lock(PLATFORM macos-arm64 PREFIX defaulted)
if(NOT installed_SHA256 STREQUAL EXPECTED_SHA256 OR
   NOT defaulted_SHA256 STREQUAL EXPECTED_SHA256)
    message(FATAL_ERROR
        "installed renderer lock differs from the source lock: "
        "${installed_SHA256} / ${defaulted_SHA256} vs ${EXPECTED_SHA256}")
endif()

add_executable(gpu-runtime-install-consumer "${CONSUMER_SOURCE}")
target_link_libraries(gpu-runtime-install-consumer PRIVATE VellumGpuRuntime::DeviceApi)
set_target_properties(gpu-runtime-install-consumer PROPERTIES
    CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON)
