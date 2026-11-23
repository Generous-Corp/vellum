cmake_minimum_required(VERSION 3.24)

if(NOT DEFINED SOURCE_ROOT)
    message(FATAL_ERROR "SOURCE_ROOT is required")
endif()

set(_required
    "framework/core/render/platform/android/android_gpu_surface.hpp"
    "framework/core/render/platform/android/android_gpu_surface.cpp"
    "framework/core/render/platform/android/android_gpu_surface_jni.cpp"
    "framework/core/render/src/render_loop_android.cpp")
foreach(_path IN LISTS _required)
    if(NOT EXISTS "${SOURCE_ROOT}/${_path}")
        message(FATAL_ERROR "android-gpu-surface: missing ${_path}")
    endif()
endforeach()

file(READ "${SOURCE_ROOT}/gpu-runtime/CMakeLists.txt" _gpu_runtime_cmake)
if(NOT _gpu_runtime_cmake MATCHES "CMAKE_ANDROID_ARCH_ABI")
    message(FATAL_ERROR "android-gpu-surface: gpu-runtime has no Android ABI selection")
endif()
if(NOT _gpu_runtime_cmake MATCHES "android-arm64-v8a")
    message(FATAL_ERROR "android-gpu-surface: gpu-runtime does not select the arm64 Vulkan path")
endif()

file(READ "${SOURCE_ROOT}/tools/android/CMakeLists.txt" _android_cmake)
if(NOT _android_cmake MATCHES "vellum-android-gpu-surface")
    message(FATAL_ERROR "android-gpu-surface: NDK canary does not compile the surface target")
endif()

file(READ "${SOURCE_ROOT}/gpu-runtime/renderer-lock.json" _renderer_lock)
if(NOT _renderer_lock MATCHES "android-arm64-v8a")
    message(FATAL_ERROR "android-gpu-surface: renderer lock has no Android entry")
endif()

file(READ "${SOURCE_ROOT}/rmlui/tests/CMakeLists.txt" _rmlui_tests)
if(NOT _rmlui_tests MATCHES "vellum-rmlui-vellum-renderer-test")
    message(FATAL_ERROR "android-gpu-surface: RmlUi Vellum renderer test is not registered")
endif()

message(STATUS "android-gpu-surface: contract passed")
