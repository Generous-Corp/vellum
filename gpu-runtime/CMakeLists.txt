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
#   VellumGpuRuntime::Bootstrap  STATIC. The image's one Dawn proc-table
#                                coordinator (ABI v1 plus per-bundle
#                                registration). Names no Dawn type.
#   VellumGpuRuntime::Device     STATIC. The Dawn surface and compute
#                                implementation, the native bootstrap, GPU
#                                diagnostics and the host hooks. Needs the
#                                locked Dawn build (GPU_RUNTIME_SKIA_DIR or
#                                GPU_RUNTIME_RENDERER_ARCHIVE) and no Skia.
#   VellumGpuRuntime::SkiaLogBridge
#                                STATIC. The one piece that names Skia: routes
#                                Skia's log handler into the diagnostic sink.
#                                A renderer that links Skia registers it.
#
# Device objects are compiled with hidden visibility, so a plug-in that links
# them statically exports none of them and each bundle keeps its own copy.
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
if(APPLE)
    enable_language(OBJCXX)
endif()

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

set(GPU_RUNTIME_SKIA_DIR "" CACHE PATH
    "An extracted locked skia-builder asset, for the device library's Dawn")
set(GPU_RUNTIME_RENDERER_ARCHIVE "" CACHE FILEPATH
    "The locked skia-builder asset itself, for the device library's Dawn")
option(GPU_RUNTIME_REQUIRE_DEVICE "Fail configure unless the device library can be built" OFF)
option(GPU_RUNTIME_HOST_OWNS_DAWN_PROCS
    "Give the device library no native proc-table default; the host must bootstrap" OFF)

add_library(gpu-runtime-bootstrap STATIC src/dawn_bootstrap.cpp)
add_library(VellumGpuRuntime::Bootstrap ALIAS gpu-runtime-bootstrap)
set_target_properties(gpu-runtime-bootstrap PROPERTIES
    EXPORT_NAME Bootstrap
    OUTPUT_NAME gpu-runtime-bootstrap
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    POSITION_INDEPENDENT_CODE ON)
target_compile_definitions(gpu-runtime-bootstrap PRIVATE
    GPU_RUNTIME_VERSION="${PROJECT_VERSION}")
target_link_libraries(gpu-runtime-bootstrap PUBLIC VellumGpuRuntime::DeviceApi)

if(APPLE)
    set(_gpu_runtime_platform macos-arm64)
else()
    set(_gpu_runtime_platform "")
endif()
set(GPU_RUNTIME_HAS_DEVICE OFF)
if(_gpu_runtime_platform)
    include("${CMAKE_CURRENT_LIST_DIR}/cmake/GpuRuntimeDawn.cmake")
    set(_gpu_runtime_dawn_args PLATFORM ${_gpu_runtime_platform}
        SKIA_DIR "${GPU_RUNTIME_SKIA_DIR}" ARCHIVE "${GPU_RUNTIME_RENDERER_ARCHIVE}")
    if(GPU_RUNTIME_REQUIRE_DEVICE)
        list(APPEND _gpu_runtime_dawn_args REQUIRED)
    endif()
    gpu_runtime_find_dawn(${_gpu_runtime_dawn_args})
    set(GPU_RUNTIME_HAS_DEVICE ${GPU_RUNTIME_HAS_DAWN})
elseif(GPU_RUNTIME_REQUIRE_DEVICE)
    message(FATAL_ERROR "gpu-runtime: the device library is macOS-only for now")
endif()

if(GPU_RUNTIME_HAS_DEVICE)
    add_library(gpu-runtime-device STATIC
        src/dawn_native_bootstrap.cpp
        src/gpu_compute.cpp
        src/gpu_diagnostics.cpp
        src/gpu_proc_installer.cpp
        src/gpu_startup_report.cpp
        src/gpu_surface_dawn.cpp
        src/host_hooks.cpp
        src/render_observer.cpp)
    if(APPLE AND NOT IOS)
        target_sources(gpu-runtime-device PRIVATE src/metal_surface_mac.mm)
    elseif(IOS)
        target_sources(gpu-runtime-device PRIVATE src/metal_surface_ios.mm)
    endif()
    add_library(VellumGpuRuntime::Device ALIAS gpu-runtime-device)
    set_target_properties(gpu-runtime-device PROPERTIES
        EXPORT_NAME Device
        OUTPUT_NAME gpu-runtime-device
        CXX_VISIBILITY_PRESET hidden
        OBJCXX_VISIBILITY_PRESET hidden
        VISIBILITY_INLINES_HIDDEN ON
        POSITION_INDEPENDENT_CODE ON)
    target_include_directories(gpu-runtime-device PRIVATE src)
    target_compile_definitions(gpu-runtime-device PRIVATE
        GPU_RUNTIME_HAS_DAWN=1
        GPU_RUNTIME_SKIA_MILESTONE=${GPU_RUNTIME_SKIA_MILESTONE})
    if(GPU_RUNTIME_HOST_OWNS_DAWN_PROCS)
        target_compile_definitions(gpu-runtime-device PRIVATE GPU_RUNTIME_HOST_OWNS_DAWN_PROCS=1)
    endif()
    target_link_libraries(gpu-runtime-device
        PUBLIC VellumGpuRuntime::DeviceApi VellumGpuRuntime::Bootstrap
        PRIVATE $<BUILD_INTERFACE:gpu-runtime-dawn>)
    # A static library carries its link dependencies to the consumer; the
    # installed package re-creates gpu-runtime-dawn from the consumer's locked
    # asset (VellumGpuRuntimeConfig.cmake).
    target_link_libraries(gpu-runtime-device INTERFACE $<INSTALL_INTERFACE:gpu-runtime-dawn>)
    if(APPLE)
        target_link_libraries(gpu-runtime-device PRIVATE
            "-framework Cocoa" "-framework Metal" "-framework QuartzCore")
    endif()

    add_library(gpu-runtime-skia-log-bridge STATIC src/skia_log_bridge.cpp)
    add_library(VellumGpuRuntime::SkiaLogBridge ALIAS gpu-runtime-skia-log-bridge)
    set_target_properties(gpu-runtime-skia-log-bridge PROPERTIES
        EXPORT_NAME SkiaLogBridge
        OUTPUT_NAME gpu-runtime-skia-log-bridge
        CXX_VISIBILITY_PRESET hidden
        VISIBILITY_INLINES_HIDDEN ON
        POSITION_INDEPENDENT_CODE ON)
    # Skia's headers only; the image that registers the bridge links Skia.
    target_include_directories(gpu-runtime-skia-log-bridge PRIVATE
        "${GPU_RUNTIME_DAWN_DIR}/build/include")
    target_compile_definitions(gpu-runtime-skia-log-bridge PRIVATE SK_RELEASE)
    target_link_libraries(gpu-runtime-skia-log-bridge PUBLIC VellumGpuRuntime::Device)
endif()

if(GPU_RUNTIME_BUILD_TESTS)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_executable(gpu-runtime-bootstrap-test tests/bootstrap_test.cpp)
    target_link_libraries(gpu-runtime-bootstrap-test PRIVATE VellumGpuRuntime::Bootstrap)
    target_compile_definitions(gpu-runtime-bootstrap-test PRIVATE
        GPU_RUNTIME_EXPECTED_VERSION="${PROJECT_VERSION}")
    add_test(NAME gpu-runtime.bootstrap COMMAND gpu-runtime-bootstrap-test)
    if(GPU_RUNTIME_HAS_DEVICE)
        add_executable(gpu-runtime-device-test tests/device_test.cpp)
        target_link_libraries(gpu-runtime-device-test PRIVATE VellumGpuRuntime::Device gpu-runtime-dawn)
        target_include_directories(gpu-runtime-device-test PRIVATE src)
        if(GPU_RUNTIME_HOST_OWNS_DAWN_PROCS)
            target_compile_definitions(gpu-runtime-device-test PRIVATE
                GPU_RUNTIME_HOST_OWNS_DAWN_PROCS=1)
        endif()
        add_test(NAME gpu-runtime.device COMMAND gpu-runtime-device-test)
    endif()
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
    if(GPU_RUNTIME_INSTALL)
        add_test(NAME gpu-runtime.install-consumer
            COMMAND "${CMAKE_COMMAND}"
                -DBUILD_DIR=${CMAKE_CURRENT_BINARY_DIR}
                -DSOURCE_DIR=${CMAKE_CURRENT_SOURCE_DIR}
                -DWORK_DIR=${CMAKE_CURRENT_BINARY_DIR}/install-consumer
                -DVERSION=${PROJECT_VERSION}
                -DGENERATOR=${CMAKE_GENERATOR}
                -P "${CMAKE_CURRENT_SOURCE_DIR}/tests/install_consumer_test.cmake")
    endif()
endif()

if(GPU_RUNTIME_INSTALL)
    set(_gpu_runtime_install_targets gpu-runtime-device-api gpu-runtime-bootstrap)
    if(GPU_RUNTIME_HAS_DEVICE)
        list(APPEND _gpu_runtime_install_targets gpu-runtime-device gpu-runtime-skia-log-bridge)
    endif()
    install(TARGETS ${_gpu_runtime_install_targets} EXPORT VellumGpuRuntimeTargets
        ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}")
    install(DIRECTORY include/gpu_runtime
        DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
    set(_gpu_runtime_cmake_dir "${CMAKE_INSTALL_LIBDIR}/cmake/VellumGpuRuntime")
    install(EXPORT VellumGpuRuntimeTargets
        NAMESPACE VellumGpuRuntime::
        DESTINATION "${_gpu_runtime_cmake_dir}")
    # The lock and its reader ship with the package, so a consumer reads the
    # same pin the runtime was built against.
    install(FILES renderer-lock.json cmake/GpuRuntimeRendererLock.cmake
            cmake/GpuRuntimeDawn.cmake
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
