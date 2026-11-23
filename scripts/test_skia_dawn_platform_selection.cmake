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

# The lock reader runs on a synthetic lock written to the work directory (the
# repository lock has no non-macOS rows yet): a row's own release forms its URL,
# its archive root is read as written, an all-zero digest is refused, and a
# platform without a row reports no locked artifact instead of failing.
include("${CMAKE_CURRENT_LIST_DIR}/../gpu-runtime/cmake/GpuRuntimeRendererLock.cmake")
if(NOT WORK_DIR)
    set(WORK_DIR "$ENV{TMPDIR}")
endif()
set(_a "${WORK_DIR}/skia-dawn-selection-lock-a.json")
set(_z "${WORK_DIR}/skia-dawn-selection-lock-zero.json")
# A failed earlier run may have left these behind; every run starts clean.
file(REMOVE "${_a}" "${_z}")
function(_row_json out asset sha root)
    set(_names skia dawn_combined svg skresources skshaper skparagraph skunicode_core skunicode_icu)
    set(_archives "")
    set(_i 0)
    foreach(_n IN LISTS _names)
        math(EXPR _i "${_i} + 1")
        string(REPEAT "${_i}" 64 _d)
        string(SUBSTRING "${_d}" 0 64 _d)
        string(APPEND _archives "\"lib${_n}.a\": \"${_d}\",")
    endforeach()
    string(REGEX REPLACE ",$" "" _archives "${_archives}")
    set(${out} "{\"asset\": \"${asset}\", \"sha256\": \"${sha}\", \"configuration\": \"Release\",
        \"archive_root\": \"${root}\", \"release\": \"chrome/m158-attested.2\",
        \"builder_commit\": \"${asset}\", \"archives\": {${_archives}}}" PARENT_SCOPE)
endfunction()
function(_write_lock path sha)
    _row_json(_row "skia-build-android-x86_64-gpu-release.zip" "${sha}"
        "build/android-gpu-x86_64/lib/Release")
    file(WRITE "${path}" "{\"schema\": \"gpu-runtime.renderer-lock.v1\", \"milestone\": \"chrome/m158\",
        \"builder\": {\"repository\": \"https://github.com/example/skia-builder\"},
        \"platforms\": {\"android-x86_64\": ${_row}}}")
endfunction()
string(REPEAT "a" 64 _good_sha)
string(REPEAT "0" 64 _zero_sha)
_write_lock("${_a}" "${_good_sha}")
_write_lock("${_z}" "${_zero_sha}")

gpu_runtime_renderer_lock_has_platform(android-x86_64 _has LOCK "${_a}")
gpu_runtime_renderer_lock_has_platform(linux-x64 _has_missing LOCK "${_a}")
gpu_runtime_renderer_lock_has_platform("" _has_empty LOCK "${_a}")
if(NOT _has OR _has_missing OR _has_empty)
    message(FATAL_ERROR "has_platform: row=${_has} missing=${_has_missing} empty=${_has_empty}")
endif()
gpu_runtime_renderer_lock_has_platform(linux-x64 _has_repo)
if(_has_repo)
    message(FATAL_ERROR "the repository lock must not carry non-macOS rows in this change")
endif()

gpu_runtime_read_renderer_lock(PLATFORM android-x86_64 PREFIX _t LOCK "${_a}")
if(NOT _t_URL STREQUAL
        "https://github.com/example/skia-builder/releases/download/chrome/m158-attested.2/skia-build-android-x86_64-gpu-release.zip")
    message(FATAL_ERROR "row release not used in URL: ${_t_URL}")
endif()
if(NOT _t_ARCHIVE_ROOT STREQUAL "build/android-gpu-x86_64/lib/Release")
    message(FATAL_ERROR "android-x86_64 archive root is ${_t_ARCHIVE_ROOT}")
endif()

execute_process(
    COMMAND "${CMAKE_COMMAND}" "-DTEST_ROOT=${CMAKE_CURRENT_LIST_DIR}/.." "-DZERO_LOCK=${_z}"
        -P "${CMAKE_CURRENT_LIST_DIR}/test_skia_dawn_platform_zero.cmake"
    RESULT_VARIABLE _zero_result ERROR_VARIABLE _zero_error)
file(REMOVE "${_a}" "${_z}")
if(_zero_result EQUAL 0 OR NOT _zero_error MATCHES "all-zero[ \n]+placeholder")
    message(FATAL_ERROR "zero digest was accepted: ${_zero_error}")
endif()

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
