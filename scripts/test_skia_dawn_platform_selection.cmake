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

# The lock reader runs on synthetic locks written to the work directory (the
# repository lock has no non-macOS rows yet). Each case below is one rule of the
# release/row contract that scripts/check_renderer_lock.py enforces too, run in
# a child cmake so a FATAL_ERROR does not end this script.
include("${CMAKE_CURRENT_LIST_DIR}/../gpu-runtime/cmake/GpuRuntimeRendererLock.cmake")
if(NOT WORK_DIR)
    set(WORK_DIR "$ENV{TMPDIR}")
endif()
set(_work "${WORK_DIR}/skia-dawn-selection")
# A failed earlier run may have left files behind; every run starts clean.
file(REMOVE_RECURSE "${_work}")
file(MAKE_DIRECTORY "${_work}")

# _lock(<out-path> KEY k [RELEASE r|NONE] [MILESTONE m] [SUFFIX .a|.lib]
#       [SHA hex] [ASSET name] [ROOT path] [DAWN hex])
function(_lock path)
    cmake_parse_arguments(L "" "KEY;RELEASE;MILESTONE;SUFFIX;SHA;ASSET;ROOT;DAWN;REPOSITORY"
        "" ${ARGN})
    if(NOT DEFINED L_RELEASE)
        set(L_RELEASE "chrome/m158-attested.2")
    endif()
    if(NOT L_MILESTONE)
        set(L_MILESTONE "chrome/m158")
    endif()
    if(NOT L_SUFFIX)
        set(L_SUFFIX ".a")
    endif()
    if(NOT L_SHA)
        string(REPEAT "a" 64 L_SHA)
    endif()
    if(NOT L_ASSET)
        set(L_ASSET "skia-build-${L_KEY}-gpu-release.zip")
    endif()
    if(NOT L_ROOT)
        set(L_ROOT "build/${L_KEY}/lib/Release")
    endif()
    if(NOT L_REPOSITORY)
        set(L_REPOSITORY "https://github.com/example/skia-builder")
    endif()
    set(_names skia dawn_combined svg skresources skshaper skparagraph skunicode_core skunicode_icu)
    set(_archives "")
    set(_i 0)
    foreach(_n IN LISTS _names)
        math(EXPR _i "${_i} + 1")
        string(REPEAT "${_i}" 64 _d)
        string(SUBSTRING "${_d}" 0 64 _d)
        if(_n STREQUAL "dawn_combined" AND L_DAWN)
            set(_d "${L_DAWN}")
        endif()
        if(L_SUFFIX STREQUAL ".lib")
            string(APPEND _archives "\"${_n}.lib\": \"${_d}\",")
        else()
            string(APPEND _archives "\"lib${_n}.a\": \"${_d}\",")
        endif()
    endforeach()
    string(REGEX REPLACE ",$" "" _archives "${_archives}")
    set(_release_json "")
    if(NOT L_RELEASE STREQUAL "NONE")
        set(_release_json "\"release\": \"${L_RELEASE}\",")
    endif()
    file(WRITE "${path}" "{\"schema\": \"gpu-runtime.renderer-lock.v1\", \"milestone\": \"${L_MILESTONE}\",
        \"builder\": {\"repository\": \"${L_REPOSITORY}\"},
        \"platforms\": {\"${L_KEY}\": {\"asset\": \"${L_ASSET}\", \"sha256\": \"${L_SHA}\",
        \"configuration\": \"Release\", \"archive_root\": \"${L_ROOT}\", ${_release_json}
        \"builder_commit\": \"${L_KEY}\", \"archives\": {${_archives}}}}}")
endfunction()

# _case(<name> <mode> <platform> <lock> <fatal-regex|OK>)
function(_case name mode platform lock expect)
    # CMake wraps long messages, so a space in the pattern matches any run of
    # whitespace.
    string(REPLACE " " "[ \n]+" expect "${expect}")
    execute_process(
        COMMAND "${CMAKE_COMMAND}" "-DTEST_ROOT=${CMAKE_CURRENT_LIST_DIR}/.."
            "-DMODE=${mode}" "-DPLATFORM=${platform}" "-DLOCK=${lock}"
            -P "${CMAKE_CURRENT_LIST_DIR}/test_skia_dawn_lock_case.cmake"
        RESULT_VARIABLE _result OUTPUT_VARIABLE _out ERROR_VARIABLE _error)
    if(expect STREQUAL "OK")
        if(NOT _result EQUAL 0)
            message(FATAL_ERROR "${name}: expected success, got: ${_error}")
        endif()
    else()
        if(_result EQUAL 0)
            message(FATAL_ERROR "${name}: accepted; expected /${expect}/")
        endif()
        if(NOT _error MATCHES "${expect}")
            message(FATAL_ERROR "${name}: refused for another reason; expected /${expect}/, got: ${_error}")
        endif()
    endif()
endfunction()
function(_lock_case name platform expect)
    set(_p "${_work}/${name}.json")
    _lock("${_p}" KEY "${platform}" ${ARGN})
    _case("${name}" read "${platform}" "${_p}" "${expect}")
endfunction()

string(REPEAT "0" 64 _zero_sha)
# A good attested row reads, and the numeric milestone stays separate from the
# row's release (the release must never reach the int32 GPU_RUNTIME_SKIA_MILESTONE).
_lock("${_work}/good.json" KEY android-x86_64)
gpu_runtime_read_renderer_lock(PLATFORM android-x86_64 PREFIX _t LOCK "${_work}/good.json")
if(NOT _t_MILESTONE STREQUAL "chrome/m158")
    message(FATAL_ERROR "MILESTONE must stay the top-level milestone, got ${_t_MILESTONE}")
endif()
if(NOT _t_RELEASE STREQUAL "chrome/m158-attested.2")
    message(FATAL_ERROR "RELEASE must be the row release, got ${_t_RELEASE}")
endif()
if(NOT _t_URL STREQUAL
        "https://github.com/example/skia-builder/releases/download/chrome/m158-attested.2/skia-build-android-x86_64-gpu-release.zip")
    message(FATAL_ERROR "row release not used in URL: ${_t_URL}")
endif()
if(NOT _t_ARCHIVE_ROOT STREQUAL "build/android-x86_64/lib/Release")
    message(FATAL_ERROR "android-x86_64 archive root is ${_t_ARCHIVE_ROOT}")
endif()

# macOS: no release, or the milestone itself, reads; RELEASE then is the milestone.
_lock("${_work}/mac-none.json" KEY macos-arm64 RELEASE NONE)
gpu_runtime_read_renderer_lock(PLATFORM macos-arm64 PREFIX _m LOCK "${_work}/mac-none.json")
if(NOT _m_RELEASE STREQUAL "chrome/m158" OR NOT _m_MILESTONE STREQUAL "chrome/m158")
    message(FATAL_ERROR "macOS without a release: ${_m_RELEASE} / ${_m_MILESTONE}")
endif()
_lock_case(mac-same macos-arm64 OK RELEASE chrome/m158)
_lock_case(mac-other-milestone macos-arm64 "macos-arm64 release chrome/m999-attested.7 is not of"
    RELEASE chrome/m999-attested.7)
_lock_case(mac-attested macos-arm64 "macos-arm64 release is chrome/m158-attested.2, expected chrome/m158")
_lock_case(mac-malformed macos-arm64 "release is malformed" RELEASE latest)

# Non-macOS rows: attested, of the lock's milestone, and present.
_lock_case(row-other-milestone android-x86_64 "release chrome/m999-attested.7 is not of milestone chrome/m158"
    RELEASE chrome/m999-attested.7)
_lock_case(row-mutable-tag android-x86_64 "release is not attested: chrome/m158" RELEASE chrome/m158)
_lock_case(row-no-release android-x86_64 "android-x86_64 names no release" RELEASE NONE)
_lock_case(row-malformed-release android-x86_64 "release is malformed: latest" RELEASE latest)
_lock_case(row-malformed-attested android-x86_64 "release is malformed" RELEASE chrome/m158-attested)
# The top-level milestone is the mutable tag; an attested one is refused, as in
# scripts/check_renderer_lock.py.
_lock_case(top-attested android-x86_64 "milestone is not chrome/mNNN" MILESTONE chrome/m158-attested.1)
_lock_case(top-plain-word android-x86_64 "milestone is not chrome/mNNN" MILESTONE main)
# Digests and names.
_lock_case(zero-asset android-x86_64 "all-zero placeholder" SHA "${_zero_sha}")
_lock_case(asset-name android-x86_64 "asset name is malformed" ASSET "evil/../asset.zip")
_lock_case(repository android-x86_64 "builder repository is malformed" REPOSITORY "http://x/y")
# Windows rows lock .lib names: the reader accepts them and lists them.
_lock("${_work}/win.json" KEY windows-x64 SUFFIX .lib)
gpu_runtime_read_renderer_lock(PLATFORM windows-x64 PREFIX _w LOCK "${_work}/win.json")
list(FIND _w_ARCHIVES "skia.lib" _skia_lib)
list(FIND _w_ARCHIVES "dawn_combined.lib" _dawn_lib)
if(_skia_lib LESS 0 OR _dawn_lib LESS 0 OR "${_w_SHA256_skia.lib}" STREQUAL "")
    message(FATAL_ERROR "windows .lib archives not read: ${_w_ARCHIVES}")
endif()

# has_platform: listed, unlisted, empty.
gpu_runtime_renderer_lock_has_platform(android-x86_64 _has LOCK "${_work}/good.json")
gpu_runtime_renderer_lock_has_platform(linux-x64 _has_missing LOCK "${_work}/good.json")
gpu_runtime_renderer_lock_has_platform("" _has_empty LOCK "${_work}/good.json")
if(NOT _has OR _has_missing OR _has_empty)
    message(FATAL_ERROR "has_platform: row=${_has} missing=${_has_missing} empty=${_has_empty}")
endif()
gpu_runtime_renderer_lock_has_platform(linux-x64 _has_repo)
if(_has_repo)
    message(FATAL_ERROR "the repository lock must not carry non-macOS rows in this change")
endif()
# A broken lock is fatal, never an unlisted platform.
_case(has-missing-lock has linux-x64 "${_work}/absent.json" "renderer lock not found")
file(WRITE "${_work}/not-json.json" "this is not json")
_case(has-not-json has linux-x64 "${_work}/not-json.json" "not a JSON object")
file(WRITE "${_work}/array.json" "[1, 2]")
_case(has-array has linux-x64 "${_work}/array.json" "not a JSON object")
file(WRITE "${_work}/no-platforms.json" "{\"schema\": \"x\"}")
_case(has-no-platforms has linux-x64 "${_work}/no-platforms.json" "has no platforms")
file(WRITE "${_work}/string-row.json" "{\"platforms\": {\"linux-x64\": \"oops\", \"macos-arm64\": {}}}")
_case(has-string-row has linux-x64 "${_work}/string-row.json" "row linux-x64 is not an object")
_case(has-other-row-is-fine has macos-arm64 "${_work}/string-row.json" "OK")

# Dawn library selection by platform: windows rows name dawn_combined.lib. The
# library's digest is locked under that name only, so selecting libdawn_combined.a
# finds no digest and stops configure.
file(WRITE "${_work}/dawn_combined.lib" "locked dawn bytes")
file(SHA256 "${_work}/dawn_combined.lib" _dawn_sha)
_lock("${_work}/win-dawn.json" KEY windows-x64 SUFFIX .lib DAWN "${_dawn_sha}")
_lock("${_work}/linux-dawn.json" KEY linux-x64 DAWN "${_dawn_sha}")
file(WRITE "${_work}/libdawn_combined.a" "locked dawn bytes")
function(_dawn_project name platform lock library expect)
    set(_dir "${_work}/${name}")
    file(MAKE_DIRECTORY "${_dir}")
    file(WRITE "${_dir}/CMakeLists.txt" "cmake_minimum_required(VERSION 3.25)
project(dawn_case NONE)
include(\"${CMAKE_CURRENT_LIST_DIR}/../gpu-runtime/cmake/GpuRuntimeDawn.cmake\")
gpu_runtime_find_dawn(PLATFORM ${platform} LOCK \"${lock}\" LIBRARY \"${library}\")
if(NOT GPU_RUNTIME_HAS_DAWN OR NOT GPU_RUNTIME_DAWN_LIBRARY STREQUAL \"${library}\")
    message(FATAL_ERROR \"dawn not selected: ${library}\")
endif()
if(NOT GPU_RUNTIME_SKIA_MILESTONE STREQUAL \"158\")
    message(FATAL_ERROR \"GPU_RUNTIME_SKIA_MILESTONE is not the numeric milestone: ${GPU_RUNTIME_SKIA_MILESTONE}\")
endif()
if(NOT GPU_RUNTIME_SKIA_RELEASE MATCHES \"^chrome/m158-attested\\\\.2$\")
    message(FATAL_ERROR \"GPU_RUNTIME_SKIA_RELEASE is ${GPU_RUNTIME_SKIA_RELEASE}\")
endif()
")
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -S "${_dir}" -B "${_dir}/build"
        RESULT_VARIABLE _result OUTPUT_QUIET ERROR_VARIABLE _error)
    if(expect STREQUAL "OK")
        if(NOT _result EQUAL 0)
            message(FATAL_ERROR "${name}: expected success, got: ${_error}")
        endif()
    else()
        string(REPLACE " " "[ \n]+" _pattern "${expect}")
        if(_result EQUAL 0 OR NOT _error MATCHES "${_pattern}")
            message(FATAL_ERROR "${name}: expected /${expect}/, got ${_result}: ${_error}")
        endif()
    endif()
endfunction()
_dawn_project(dawn-windows windows-x64 "${_work}/win-dawn.json" "${_work}/dawn_combined.lib" OK)
_dawn_project(dawn-linux linux-x64 "${_work}/linux-dawn.json" "${_work}/libdawn_combined.a" OK)
# A row that locks only the other platform's name finds no digest: the windows
# lock holds dawn_combined.lib, never libdawn_combined.a.
_dawn_project(dawn-wrong-digest windows-x64 "${_work}/win.json" "${_work}/dawn_combined.lib"
    "does not match the lock")

file(REMOVE_RECURSE "${_work}")

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
