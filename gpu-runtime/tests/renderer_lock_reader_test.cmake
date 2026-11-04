# Tests gpu_runtime_read_renderer_lock() in script mode.
#
#   cmake -DLOCK_MODULE=<module> -DLOCK=<renderer-lock.json> -DWORK_DIR=<dir>
#         -P renderer_lock_reader_test.cmake
#
# The real lock must read back whole and consistent, and every malformed lock
# below must stop the reader with FATAL_ERROR. Each malformed case runs in a
# child cmake so its FATAL_ERROR does not end this script.
cmake_minimum_required(VERSION 3.24)

include("${LOCK_MODULE}")

if(MODE STREQUAL "read")
    gpu_runtime_read_renderer_lock(PLATFORM macos-arm64 PREFIX R LOCK "${LOCK}")
    return()
endif()

gpu_runtime_read_renderer_lock(PLATFORM macos-arm64 PREFIX R LOCK "${LOCK}")
file(READ "${LOCK}" _json)
string(JSON _milestone GET "${_json}" milestone)
string(JSON _repository GET "${_json}" builder repository)
string(JSON _asset GET "${_json}" platforms macos-arm64 asset)
string(JSON _count LENGTH "${_json}" platforms macos-arm64 archives)
list(LENGTH R_ARCHIVES _read_count)
if(NOT R_MILESTONE STREQUAL _milestone OR
   NOT R_URL STREQUAL "${_repository}/releases/download/${_milestone}/${_asset}" OR
   NOT _read_count EQUAL _count OR
   NOT R_SHA256_libskia.a MATCHES "^[0-9a-f]+$" OR
   NOT R_SHA256_libdawn_combined.a MATCHES "^[0-9a-f]+$")
    message(FATAL_ERROR "renderer lock read back incompletely: ${R_MILESTONE} ${R_URL} ${R_ARCHIVES}")
endif()

file(MAKE_DIRECTORY "${WORK_DIR}")
set(_digest_skia "${R_SHA256_libskia.a}")
string(TOUPPER "${_digest_skia}" _digest_skia_upper)
set(_cases
    "schema|\"gpu-runtime.renderer-lock.v1\"|\"gpu-runtime.renderer-lock.v0\""
    "milestone|\"${_milestone}\"|\"main\""
    "digest|\"${_digest_skia}\"|\"${_digest_skia}0\""
    "uppercase|\"${_digest_skia}\"|\"${_digest_skia_upper}\""
    "archive-name|\"libskia.a\"|\"../libskia.a\""
    "asset|\"${_asset}\"|\"../${_asset}\""
    "platform|\"macos-arm64\"|\"macos-x86_64\"")
foreach(_case IN LISTS _cases)
    string(REPLACE "|" ";" _parts "${_case}")
    list(GET _parts 0 _name)
    list(GET _parts 1 _from)
    list(GET _parts 2 _to)
    string(FIND "${_json}" "${_from}" _at)
    if(_at EQUAL -1)
        message(FATAL_ERROR "case ${_name}: ${_from} is not in the lock, so the case tests nothing")
    endif()
    string(REPLACE "${_from}" "${_to}" _broken "${_json}")
    set(_path "${WORK_DIR}/${_name}.json")
    file(WRITE "${_path}" "${_broken}")
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -DMODE=read -DLOCK_MODULE=${LOCK_MODULE}
            -DLOCK=${_path} -P "${CMAKE_CURRENT_LIST_FILE}"
        RESULT_VARIABLE _result OUTPUT_QUIET ERROR_VARIABLE _error)
    if(_result EQUAL 0)
        message(FATAL_ERROR "case ${_name}: a malformed lock was accepted")
    endif()
    if(NOT _error MATCHES "gpu-runtime: renderer lock")
        message(FATAL_ERROR "case ${_name}: failed for the wrong reason: ${_error}")
    endif()
endforeach()
message(STATUS "renderer lock reader: ok")
