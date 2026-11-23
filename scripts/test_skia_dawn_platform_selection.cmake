cmake_minimum_required(VERSION 3.25)

include("${CMAKE_CURRENT_LIST_DIR}/../cmake/VellumSkiaDawnPlatform.cmake")

function(assert_platform system processor abi expected)
    vellum_skia_dawn_platform_key("${system}" "${processor}" "${abi}" actual)
    if(NOT actual STREQUAL expected)
        message(FATAL_ERROR
            "platform ${system}/${processor}/${abi} selected ${actual}, expected ${expected}")
    endif()
endfunction()

assert_platform("Darwin" "arm64" "" "macos-arm64")
assert_platform("Linux" "x86_64" "" "linux-x64")
assert_platform("Linux" "aarch64" "" "linux-arm64")
assert_platform("Windows" "AMD64" "" "windows-x64")
assert_platform("Windows" "ARM64" "" "windows-arm64")
assert_platform("Android" "aarch64" "arm64-v8a" "android-arm64-v8a")
assert_platform("Android" "x86_64" "x86_64" "android-x86_64")

# QUIET: an unlisted platform yields an empty key instead of an error, so an
# optional-GPU configure on it still succeeds.
foreach(_quiet_case IN ITEMS "Linux|riscv64|" "Linux|armv7l|" "Windows|x86|" "Darwin|x86_64|"
        "FreeBSD|amd64|")
    string(REPLACE "|" ";" _quiet_args "${_quiet_case}")
    list(GET _quiet_args 0 _s)
    list(GET _quiet_args 1 _p)
    vellum_skia_dawn_platform_key("${_s}" "${_p}" "" _quiet_key QUIET)
    if(NOT _quiet_key STREQUAL "")
        message(FATAL_ERROR "QUIET ${_quiet_case} returned ${_quiet_key}")
    endif()
endforeach()

# The lock reader: a row's own release forms its URL; the right archive root is
# read for android-x86_64; an all-zero digest is refused.
include("${CMAKE_CURRENT_LIST_DIR}/../gpu-runtime/cmake/GpuRuntimeRendererLock.cmake")
gpu_runtime_read_renderer_lock(PLATFORM android-x86_64 PREFIX _t)
if(NOT _t_URL MATCHES "/releases/download/chrome/m158-attested\\.2/skia-build-android-x86_64-gpu-release\\.zip$")
    message(FATAL_ERROR "row release not used in URL: ${_t_URL}")
endif()
if(NOT _t_ARCHIVE_ROOT STREQUAL "build/android-gpu-x86_64/lib/Release")
    message(FATAL_ERROR "android-x86_64 archive root is ${_t_ARCHIVE_ROOT}")
endif()
file(READ "${CMAKE_CURRENT_LIST_DIR}/../gpu-runtime/renderer-lock.json" _lock_text)
string(REPLACE "${_t_SHA256}" "0000000000000000000000000000000000000000000000000000000000000000"
    _zeroed "${_lock_text}")
set(_zero_lock "${CMAKE_CURRENT_LIST_DIR}/../build-zero-renderer-lock.json")
file(WRITE "${_zero_lock}" "${_zeroed}")
execute_process(
    COMMAND "${CMAKE_COMMAND}" "-DTEST_ROOT=${CMAKE_CURRENT_LIST_DIR}/.." "-DZERO_LOCK=${_zero_lock}"
        -P "${CMAKE_CURRENT_LIST_DIR}/test_skia_dawn_platform_zero.cmake"
    RESULT_VARIABLE _zero_result ERROR_VARIABLE _zero_error)
if(_zero_result EQUAL 0 OR NOT _zero_error MATCHES "all-zero[ \n]+placeholder")
    message(FATAL_ERROR "zero digest was accepted: ${_zero_error}")
endif()
file(REMOVE "${_zero_lock}")

set(_unknown_error "")
execute_process(
    COMMAND "${CMAKE_COMMAND}" "-DTEST_ROOT=${CMAKE_CURRENT_LIST_DIR}/.."
        -P "${CMAKE_CURRENT_LIST_DIR}/test_skia_dawn_platform_unknown.cmake"
    RESULT_VARIABLE _unknown_result
    OUTPUT_VARIABLE _unknown_output
    ERROR_VARIABLE _unknown_error)
if(_unknown_result EQUAL 0 OR NOT _unknown_error MATCHES "unsupported GPU platform")
    message(FATAL_ERROR "unknown platform was accepted: ${_unknown_error}")
endif()
