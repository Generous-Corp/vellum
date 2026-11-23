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

set(_unknown_error "")
execute_process(
    COMMAND "${CMAKE_COMMAND}" -DTEST_ROOT="${CMAKE_CURRENT_LIST_DIR}/.."
        -P "${CMAKE_CURRENT_LIST_DIR}/test_skia_dawn_platform_unknown.cmake"
    RESULT_VARIABLE _unknown_result
    OUTPUT_VARIABLE _unknown_output
    ERROR_VARIABLE _unknown_error)
if(_unknown_result EQUAL 0 OR NOT _unknown_error MATCHES "unsupported GPU platform")
    message(FATAL_ERROR "unknown platform was accepted: ${_unknown_error}")
endif()
