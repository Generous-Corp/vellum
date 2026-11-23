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
if(_gpu_runtime_cmake MATCHES "CMAKE_ANDROID_ARCH_ABI" OR
   _gpu_runtime_cmake MATCHES "android-arm64-v8a")
    message(FATAL_ERROR
        "android-gpu-surface: device selection must stay deferred until an Android asset is verified")
endif()

file(READ "${SOURCE_ROOT}/tools/android/CMakeLists.txt" _android_cmake)
if(NOT _android_cmake MATCHES "vellum-android-gpu-surface")
    message(FATAL_ERROR "android-gpu-surface: NDK canary does not compile the surface target")
endif()

file(READ "${SOURCE_ROOT}/gpu-runtime/renderer-lock.json" _renderer_lock)
if(_renderer_lock MATCHES "android-arm64-v8a" OR
   _renderer_lock MATCHES "\"sha256\"[ \t\r\n]*:[ \t\r\n]*\"0+")
    message(FATAL_ERROR
        "android-gpu-surface: renderer lock must not contain unverified Android or zero digests")
endif()

file(READ "${SOURCE_ROOT}/provenance/renderer-attestation.json" _attestation)
if(NOT _attestation MATCHES
   "\"state\"[ \t\r\n]*:[ \t\r\n]*\"deferred-no-verified-asset\"")
    message(FATAL_ERROR "android-gpu-surface: attestation does not record the deferred asset state")
endif()
if(NOT _attestation MATCHES "\"artifact\"[ \t\r\n]*:[ \t\r\n]*null")
    message(FATAL_ERROR "android-gpu-surface: deferred attestation must not name an artifact")
endif()

file(READ "${SOURCE_ROOT}/framework/core/render/src/render_loop_android.cpp" _render_loop)
if(NOT _render_loop MATCHES "CallbackState" OR
   NOT _render_loop MATCHES "shared_ptr<CallbackState>" OR
   NOT _render_loop MATCHES "callback_scheduled" OR
   NOT _render_loop MATCHES "compare_exchange_strong" OR
   _render_loop MATCHES "postFrameCallback[^\n]*this")
    message(FATAL_ERROR
        "android-gpu-surface: Choreographer callbacks must use a lifetime-pinned state token")
endif()

if(NOT _render_loop MATCHES "if \\(!state->choreographer\\)")
    message(FATAL_ERROR
        "android-gpu-surface: start() must stop the loop when AChoreographer is unavailable")
endif()

file(READ "${SOURCE_ROOT}/framework/core/render/platform/android/android_gpu_surface_jni.cpp" _jni)
if(NOT _jni MATCHES "width <= 0 \\|\\| height <= 0")
    message(FATAL_ERROR
        "android-gpu-surface: JNI must reject non-positive dimensions before unsigned conversion")
endif()

file(READ "${SOURCE_ROOT}/framework/core/render/platform/android/android_gpu_surface.cpp" _surface)
if(NOT _surface MATCHES "scaled_extent" OR
   NOT _surface MATCHES "config.width = scaled_extent" OR
   NOT _surface MATCHES "surface->resize")
    message(FATAL_ERROR "android-gpu-surface: create and resize must apply the display scale")
endif()

file(READ "${SOURCE_ROOT}/rmlui/tests/CMakeLists.txt" _rmlui_tests)
if(NOT _rmlui_tests MATCHES "vellum-rmlui-vellum-renderer-test")
    message(FATAL_ERROR "android-gpu-surface: RmlUi Vellum renderer test is not registered")
endif()

message(STATUS "android-gpu-surface: contract passed")
