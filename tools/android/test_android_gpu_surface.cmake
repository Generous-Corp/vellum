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

# The renderer lock carries a row for each Android ABI. A row is accepted only
# when it reads back whole: an attested release, an archive root under build/,
# a nonzero 64-digit archive digest and a nonzero digest for the Dawn library.
function(android_lock_row_error json platform out_var)
    set(_err "")
    string(JSON _row ERROR_VARIABLE _missing GET "${json}" platforms "${platform}")
    if(_missing)
        set(_err "no ${platform} row")
    else()
        foreach(_field IN ITEMS asset sha256 archive_root release)
            string(JSON _type ERROR_VARIABLE _type_error TYPE "${_row}" "${_field}")
            string(JSON _value ERROR_VARIABLE _e GET "${_row}" "${_field}")
            if(_type_error OR _e OR NOT _type STREQUAL "STRING" OR _value STREQUAL "")
                set(_err "${platform} row lacks ${_field}")
                break()
            endif()
            set(_${_field} "${_value}")
        endforeach()
    endif()
    if(NOT _err)
        string(JSON _dawn_type ERROR_VARIABLE _dawn_type_error
            TYPE "${_row}" archives libdawn_combined.a)
        string(JSON _dawn ERROR_VARIABLE _e GET "${_row}" archives libdawn_combined.a)
        if(_dawn_type_error OR _e OR NOT _dawn_type STREQUAL "STRING")
            set(_err "${platform} row lacks the Dawn library digest")
        endif()
    endif()
    if(NOT _err)
        foreach(_digest IN ITEMS "${_sha256}" "${_dawn}")
            string(LENGTH "${_digest}" _digest_length)
            if(NOT _digest MATCHES "^[0-9a-f]+$" OR NOT _digest_length EQUAL 64 OR
               _digest MATCHES "^0+$")
                set(_err "${platform} row has a malformed or zero digest")
            endif()
        endforeach()
    endif()
    if(NOT _err AND (NOT _archive_root MATCHES "^build/[^/].*" OR
                     _archive_root MATCHES "(^|/)\\.\\.(/|$)"))
        set(_err "${platform} archive_root is ${_archive_root}")
    endif()
    if(NOT _err AND NOT _release MATCHES "^chrome/m[0-9]+-attested\\.[0-9]+$")
        set(_err "${platform} release is not attested: ${_release}")
    endif()
    set(${out_var} "${_err}" PARENT_SCOPE)
endfunction()

file(READ "${SOURCE_ROOT}/gpu-runtime/renderer-lock.json" _renderer_lock)
foreach(_abi IN ITEMS android-arm64-v8a android-x86_64)
    android_lock_row_error("${_renderer_lock}" ${_abi} _row_error)
    if(_row_error)
        message(FATAL_ERROR "android-gpu-surface: renderer lock: ${_row_error}")
    endif()
endforeach()
if(_renderer_lock MATCHES "\"sha256\"[ \t\r\n]*:[ \t\r\n]*\"0+\"")
    message(FATAL_ERROR "android-gpu-surface: renderer lock must not contain zero digests")
endif()

# Negative controls: each malformed row must be refused, and the unmodified
# row must be accepted, so the checks above cannot pass by never running.
string(JSON _good GET "${_renderer_lock}" platforms android-arm64-v8a)
set(_zeros "0000000000000000000000000000000000000000000000000000000000000000")
string(JSON _bad_zero SET "${_renderer_lock}" platforms android-arm64-v8a sha256 "\"${_zeros}\"")
string(JSON _bad_short SET "${_renderer_lock}" platforms android-arm64-v8a sha256 "\"abcd\"")
string(JSON _bad_asset SET "${_renderer_lock}" platforms android-arm64-v8a asset "42")
string(JSON _bad_root SET "${_renderer_lock}" platforms android-arm64-v8a archive_root "\"/abs\"")
string(JSON _bad_empty_root SET "${_renderer_lock}" platforms android-arm64-v8a archive_root "\"build/\"")
string(JSON _bad_traversal SET "${_renderer_lock}" platforms android-arm64-v8a archive_root "\"build/../outside\"")
string(JSON _bad_release SET "${_renderer_lock}" platforms android-arm64-v8a release "\"unattested/chrome/m158\"")
string(JSON _bad_dawn_type SET "${_renderer_lock}" platforms android-arm64-v8a archives libdawn_combined.a "42")
string(JSON _bad_dawn SET "${_renderer_lock}" platforms android-arm64-v8a archives libdawn_combined.a "\"${_zeros}\"")
string(JSON _bad_gone REMOVE "${_renderer_lock}" platforms android-arm64-v8a)
foreach(_case IN ITEMS bad_zero bad_short bad_asset bad_root bad_empty_root bad_traversal
                     bad_release bad_dawn_type bad_dawn bad_gone)
    android_lock_row_error("${_${_case}}" android-arm64-v8a _control_error)
    if(NOT _control_error)
        message(FATAL_ERROR "android-gpu-surface: malformed Android row (${_case}) was accepted")
    endif()
endforeach()
android_lock_row_error("${_renderer_lock}" android-arm64-v8a _control_error)
if(_control_error)
    message(FATAL_ERROR "android-gpu-surface: control row refused: ${_control_error}")
endif()

# The Android attestation record ships separately from the verifier rule.
# Until it lands there is none; once it does, it must stay deferred with a
# null artifact, the same shape verify_renderer_dependency.py accepts.
file(READ "${SOURCE_ROOT}/provenance/renderer-attestation.json" _attestation)
string(JSON _android_type ERROR_VARIABLE _android_missing
    TYPE "${_attestation}" android_arm64_v8a)
if(NOT _android_missing)
    string(JSON _android_state GET "${_attestation}" android_arm64_v8a state)
    string(JSON _android_artifact_type TYPE "${_attestation}" android_arm64_v8a artifact)
    if(NOT _android_state STREQUAL "deferred-no-verified-asset")
        message(FATAL_ERROR "android-gpu-surface: attestation does not record the deferred asset state")
    endif()
    if(NOT _android_artifact_type STREQUAL "NULL")
        message(FATAL_ERROR "android-gpu-surface: deferred attestation must not name an artifact")
    endif()
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

if(_render_loop MATCHES "callback\\(\\);[ \n\t]*request_vsync")
    message(FATAL_ERROR
        "android-gpu-surface: on_vsync must not re-post a vsync callback while idle")
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
