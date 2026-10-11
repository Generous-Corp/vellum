# The installed package refuses a consumer that targets another system or
# architecture: it reports "not found" with a reason and imports no targets, so
# an optional consumer falls back to its own provider instead of linking a
# slice against libraries of the wrong architecture. A consumer that targets
# the package's own platform still finds it (the control).
if(NOT DEFINED VELLUM_BUILD_DIR OR NOT DEFINED VELLUM_INSTALL_LIBDIR)
    message(FATAL_ERROR "arch-guard consumer paths were not supplied")
endif()

set(_work "${VELLUM_BUILD_DIR}/arch-guard-consumer-test")
set(_prefix "${_work}/prefix")
file(REMOVE_RECURSE "${_work}")

set(_config_args)
if(DEFINED TEST_CONFIG AND NOT TEST_CONFIG STREQUAL "")
    list(APPEND _config_args --config "${TEST_CONFIG}")
endif()
execute_process(
    COMMAND "${CMAKE_COMMAND}" --install "${VELLUM_BUILD_DIR}"
            --prefix "${_prefix}" ${_config_args}
    RESULT_VARIABLE _install_result
    OUTPUT_VARIABLE _install_stdout
    ERROR_VARIABLE _install_stderr)
if(NOT _install_result EQUAL 0)
    message(FATAL_ERROR
        "Vellum SDK install failed (${_install_result})\n"
        "${_install_stdout}\n${_install_stderr}")
endif()
set(_package_dir "${_prefix}/${VELLUM_INSTALL_LIBDIR}/cmake/Vellum")

# A consumer with no languages: it only asks whether the package is usable, so
# it can target another system without a cross toolchain.
set(_consumer "${_work}/consumer")
file(MAKE_DIRECTORY "${_consumer}")
file(WRITE "${_consumer}/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(vellum_arch_guard_consumer LANGUAGES NONE)
find_package(Vellum CONFIG QUIET)
get_property(_imported DIRECTORY PROPERTY IMPORTED_TARGETS)
list(FILTER _imported INCLUDE REGEX "^Vellum::")
list(LENGTH _imported _imported_count)
file(WRITE "${CMAKE_BINARY_DIR}/result.txt"
    "found=${Vellum_FOUND}\nimported=${_imported_count}\nmessage=${Vellum_NOT_FOUND_MESSAGE}\n")
]=])

function(_consumer label expect_found expect_text)
    set(_build "${_work}/build-${label}")
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -S "${_consumer}" -B "${_build}"
                "-DVellum_DIR=${_package_dir}" ${ARGN}
        RESULT_VARIABLE _result
        OUTPUT_VARIABLE _stdout
        ERROR_VARIABLE _stderr)
    if(NOT _result EQUAL 0)
        message(FATAL_ERROR "${label}: consumer configure failed (${_result})\n${_stdout}\n${_stderr}")
    endif()
    file(READ "${_build}/result.txt" _report)
    if(expect_found)
        if(NOT _report MATCHES "found=1\n" OR _report MATCHES "imported=0\n")
            message(FATAL_ERROR "${label}: the package must be found with its targets:\n${_report}")
        endif()
    else()
        if(NOT _report MATCHES "found=0\n" OR NOT _report MATCHES "imported=0\n")
            message(FATAL_ERROR "${label}: the package must be refused with no targets:\n${_report}")
        endif()
        if(NOT _report MATCHES "${expect_text}")
            message(FATAL_ERROR "${label}: the refusal does not say why (${expect_text}):\n${_report}")
        endif()
    endif()
    message(STATUS "ok: ${label}")
endfunction()

_consumer(native TRUE "")
_consumer(other-system FALSE "the consumer targets Generic" -DCMAKE_SYSTEM_NAME=Generic)
if(CMAKE_HOST_APPLE)
    file(READ "${_package_dir}/VellumConfig.cmake" _config)
    if(NOT _config MATCHES "set\\(_vellum_package_architectures \"([^\"]*)\"\\)")
        message(FATAL_ERROR "VellumConfig.cmake records no package architectures")
    endif()
    set(_package_architectures "${CMAKE_MATCH_1}")
    if("x86_64" IN_LIST _package_architectures)
        set(_foreign arm64)
    else()
        set(_foreign x86_64)
    endif()
    _consumer(wrong-architecture FALSE "architecture" "-DCMAKE_OSX_ARCHITECTURES=${_foreign}")
    list(GET _package_architectures 0 _own)
    _consumer(universal FALSE "architecture" "-DCMAKE_OSX_ARCHITECTURES=${_own}\\;${_foreign}")
    string(REPLACE ";" "\\;" _own_list "${_package_architectures}")
    _consumer(own-architecture TRUE "" "-DCMAKE_OSX_ARCHITECTURES=${_own_list}")
    _consumer(ios FALSE "the consumer targets iOS" -DCMAKE_SYSTEM_NAME=iOS)
endif()
message(STATUS "vellum_arch_guard_verified=true")
