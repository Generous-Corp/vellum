cmake_minimum_required(VERSION 3.24)
project(GpuRuntimeArtifactConsumer LANGUAGES CXX)

# An audio-only GPU consumer of the published gpu-runtime archive: it finds the
# package by exact version and links Device against the Dawn library the
# archive ships, with nothing from Vellum and no skia-builder asset.
find_package(VellumGpuRuntime ${EXPECTED_VERSION} EXACT REQUIRED COMPONENTS Device)
if(NOT TARGET VellumGpuRuntime::Device)
    message(FATAL_ERROR "the installed package has no Device target")
endif()

add_executable(gpu-runtime-artifact-consumer main.cpp)
target_link_libraries(gpu-runtime-artifact-consumer PRIVATE VellumGpuRuntime::Device)
set_target_properties(gpu-runtime-artifact-consumer PROPERTIES
    CXX_STANDARD 20 CXX_STANDARD_REQUIRED ON)
target_compile_definitions(gpu-runtime-artifact-consumer PRIVATE
    EXPECTED_VERSION="${EXPECTED_VERSION}")
