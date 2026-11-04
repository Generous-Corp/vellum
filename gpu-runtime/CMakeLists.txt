cmake_minimum_required(VERSION 3.24)

# The GPU device layer shared by GPU audio and rendering. It is its own project
# so it builds on its own (cmake -S gpu-runtime) and depends on neither Pulp nor
# Vellum: an audio-only GPU plug-in links it without a UI framework, and a
# renderer links it without an audio framework.
#
# Targets:
#   VellumGpuRuntime::DeviceApi  INTERFACE. The device headers under
#                                <gpu_runtime/...>: opaque handles only, so it
#                                names no Dawn, WebGPU or Skia dependency.
#
# renderer-lock.json is the one place the Skia/Dawn milestone and the
# skia-builder digests are written; cmake/GpuRuntimeRendererLock.cmake reads it
# for every build that needs the pin.
#
# The version is the Vellum release version: gpu-runtime is published from
# every Vellum tag with the same number.
if(NOT DEFINED Vellum_VERSION)
    file(STRINGS "${CMAKE_CURRENT_LIST_DIR}/../CMakeLists.txt" _gpu_runtime_version_line
        REGEX "^    VERSION [0-9]+\\.[0-9]+\\.[0-9]+$" LIMIT_COUNT 1)
    string(STRIP "${_gpu_runtime_version_line}" _gpu_runtime_version_line)
    string(REPLACE "VERSION " "" Vellum_VERSION "${_gpu_runtime_version_line}")
    if(NOT Vellum_VERSION MATCHES "^[0-9]+\\.[0-9]+\\.[0-9]+$")
        message(FATAL_ERROR
            "gpu-runtime: cannot read the Vellum version from ../CMakeLists.txt")
    endif()
endif()

project(VellumGpuRuntime
    VERSION ${Vellum_VERSION}
    DESCRIPTION "GPU device layer shared by GPU audio and rendering"
    LANGUAGES CXX)

if(PROJECT_IS_TOP_LEVEL)
    include(CTest)
    set(CMAKE_CXX_STANDARD 20)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    set(CMAKE_CXX_EXTENSIONS OFF)
endif()
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)
include("${CMAKE_CURRENT_LIST_DIR}/cmake/GpuRuntimeRendererLock.cmake")

option(GPU_RUNTIME_BUILD_TESTS "Build the gpu-runtime tests" ${BUILD_TESTING})
option(GPU_RUNTIME_INSTALL "Install the VellumGpuRuntime CMake package" ${PROJECT_IS_TOP_LEVEL})
option(GPU_RUNTIME_BENCHMARK
    "Expose the benchmark perf-counter hooks in the device API" OFF)

add_library(gpu-runtime-device-api INTERFACE)
add_library(VellumGpuRuntime::DeviceApi ALIAS gpu-runtime-device-api)
set_target_properties(gpu-runtime-device-api PROPERTIES EXPORT_NAME DeviceApi)
target_include_directories(gpu-runtime-device-api INTERFACE
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
target_compile_features(gpu-runtime-device-api INTERFACE cxx_std_20)
# The benchmark hooks change the GpuCompute vtable, so the definition travels
# with the target: every consumer and the implementation agree on it.
if(GPU_RUNTIME_BENCHMARK)
    target_compile_definitions(gpu-runtime-device-api INTERFACE GPU_RUNTIME_BENCHMARK=1)
endif()

if(GPU_RUNTIME_BUILD_TESTS)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_executable(gpu-runtime-device-api-consumer tests/device_api_consumer.cpp)
    target_link_libraries(gpu-runtime-device-api-consumer
        PRIVATE VellumGpuRuntime::DeviceApi)
    add_test(NAME gpu-runtime.device-api-consumer
        COMMAND gpu-runtime-device-api-consumer)
    add_test(NAME gpu-runtime.device-api-closure
        COMMAND "${Python3_EXECUTABLE}" -I
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/check_device_api_closure.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/include")
    add_test(NAME gpu-runtime.device-api-closure-unit
        COMMAND "${Python3_EXECUTABLE}" -I
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/test_check_device_api_closure.py")
    add_test(NAME gpu-runtime.renderer-lock-reader
        COMMAND "${CMAKE_COMMAND}"
            -DLOCK_MODULE=${CMAKE_CURRENT_SOURCE_DIR}/cmake/GpuRuntimeRendererLock.cmake
            -DLOCK=${CMAKE_CURRENT_SOURCE_DIR}/renderer-lock.json
            -DWORK_DIR=${CMAKE_CURRENT_BINARY_DIR}/renderer-lock-reader
            -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/renderer_lock_reader_test.cmake")
endif()

if(GPU_RUNTIME_INSTALL)
    install(TARGETS gpu-runtime-device-api EXPORT VellumGpuRuntimeTargets)
    install(DIRECTORY include/gpu_runtime
        DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
    set(_gpu_runtime_cmake_dir "${CMAKE_INSTALL_LIBDIR}/cmake/VellumGpuRuntime")
    install(EXPORT VellumGpuRuntimeTargets
        NAMESPACE VellumGpuRuntime::
        DESTINATION "${_gpu_runtime_cmake_dir}")
    # The lock and its reader ship with the package, so a consumer reads the
    # same pin the runtime was built against.
    install(FILES renderer-lock.json cmake/GpuRuntimeRendererLock.cmake
        DESTINATION "${_gpu_runtime_cmake_dir}")
    configure_package_config_file(
        cmake/VellumGpuRuntimeConfig.cmake.in
        "${CMAKE_CURRENT_BINARY_DIR}/VellumGpuRuntimeConfig.cmake"
        INSTALL_DESTINATION "${_gpu_runtime_cmake_dir}")
    write_basic_package_version_file(
        "${CMAKE_CURRENT_BINARY_DIR}/VellumGpuRuntimeConfigVersion.cmake"
        COMPATIBILITY ExactVersion)
    install(FILES
        "${CMAKE_CURRENT_BINARY_DIR}/VellumGpuRuntimeConfig.cmake"
        "${CMAKE_CURRENT_BINARY_DIR}/VellumGpuRuntimeConfigVersion.cmake"
        DESTINATION "${_gpu_runtime_cmake_dir}")
endif()
