# This consumer proves the installed package from a real consumer project:
# the Darwin export resolves through find_package(Vellum CONFIG REQUIRED), each
# declared non-Darwin platform is refused by the package's platform guard (the
# installed binaries are built for one system), and the real packaging gate
# rejects an undeclared system. The per-platform generated target properties
# (rpath, provider path, editor host sources) are proved against
# VellumMirror.cmake itself by tools/mirror/test_mirror_platform_targets.py. No
# GPU-off platform link is made here: nothing in this script links a closure.
if(NOT DEFINED VELLUM_BUILD_DIR OR NOT DEFINED VELLUM_INSTALL_LIBDIR OR
   NOT DEFINED VELLUM_SOURCE_DIR)
    message(FATAL_ERROR "mirror export consumer paths were not supplied")
endif()

set(_work "${VELLUM_BUILD_DIR}/mirror-export-consumer")
set(_prefix "${_work}/prefix")
file(REMOVE_RECURSE "${_work}")
file(MAKE_DIRECTORY "${_work}/positive" "${_work}/negative")

execute_process(
    COMMAND "${CMAKE_COMMAND}" --install "${VELLUM_BUILD_DIR}" --prefix "${_prefix}"
    RESULT_VARIABLE _install_result OUTPUT_VARIABLE _install_stdout ERROR_VARIABLE _install_stderr)
if(NOT _install_result EQUAL 0)
    message(FATAL_ERROR "mirror export consumer install failed (${_install_result})\n"
        "${_install_stdout}\n${_install_stderr}")
endif()

set(_package_dir "${_prefix}/${VELLUM_INSTALL_LIBDIR}/cmake/Vellum")
if(NOT EXISTS "${_package_dir}/VellumConfig.cmake" OR
   NOT EXISTS "${_prefix}/mirror/lib/cmake/VellumMirror/VellumMirrorTargets.cmake")
    message(FATAL_ERROR "the installed SDK has no Vellum mirror package")
endif()

file(WRITE "${_work}/positive/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumMirrorExportConsumer LANGUAGES CXX)
find_package(Vellum CONFIG REQUIRED)
if(NOT TARGET Vellum::MirrorView_core)
    message(FATAL_ERROR "the installed package did not export Vellum::MirrorView_core")
endif()
get_target_property(_links Vellum::MirrorView_core INTERFACE_LINK_LIBRARIES)
if(NOT "Vellum::Value" IN_LIST _links)
    message(FATAL_ERROR "the exported mirror target lost Vellum::Value: ${_links}")
endif()
get_target_property(_location Vellum::MirrorView_core IMPORTED_LOCATION_RELEASE)
if(NOT _location)
    get_target_property(_location Vellum::MirrorView_core IMPORTED_LOCATION_NOCONFIG)
endif()
if(NOT _location OR NOT EXISTS "${_location}")
    message(FATAL_ERROR "the exported mirror target has no installed image: ${_location}")
endif()
if(NOT TARGET Vellum::SkiaDawnProvider)
    message(FATAL_ERROR "the installed package did not export Vellum::SkiaDawnProvider")
endif()
get_target_property(_canvas_links Vellum::MirrorCanvas INTERFACE_LINK_LIBRARIES)
if(NOT "Vellum::SkiaDawnProvider" IN_LIST _canvas_links)
    message(FATAL_ERROR "the exported canvas target lost Vellum::SkiaDawnProvider: ${_canvas_links}")
endif()
if(TARGET Vellum::MirrorUndeclared)
    message(FATAL_ERROR "the package exported an undeclared mirror target")
endif()
]=])
execute_process(
    COMMAND "${CMAKE_COMMAND}" -S "${_work}/positive" -B "${_work}/positive-build"
        "-DVellum_DIR=${_package_dir}" -DCMAKE_BUILD_TYPE=Release
        -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE
    RESULT_VARIABLE _positive_result OUTPUT_VARIABLE _positive_stdout ERROR_VARIABLE _positive_stderr)
if(NOT _positive_result EQUAL 0)
    message(FATAL_ERROR "installed mirror export configure failed (${_positive_result})\n"
        "${_positive_stdout}\n${_positive_stderr}")
endif()

# Each declared non-Darwin platform: the Darwin-built package refuses it, and
# says why, instead of importing images of the wrong format.
include("${VELLUM_SOURCE_DIR}/tools/mirror/VellumMirrorPlatform.cmake")
vellum_mirror_declared_platforms(_declared_platforms)
set(_checked_platforms 0)
foreach(_platform IN LISTS _declared_platforms)
    string(REPLACE "|" ";" _platform_parts "${_platform}")
    list(GET _platform_parts 0 _system)
    list(GET _platform_parts 1 _processor)
    if(_system STREQUAL "Darwin")
        continue()
    endif()
    string(TOLOWER "${_system}" _platform_name)
    file(MAKE_DIRECTORY "${_work}/refuse-${_platform_name}")
    file(WRITE "${_work}/refuse-${_platform_name}/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumMirrorPlatformRefusal LANGUAGES NONE)
find_package(Vellum CONFIG REQUIRED)
]=])
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -S "${_work}/refuse-${_platform_name}"
            -B "${_work}/refuse-${_platform_name}-build"
            "-DVellum_DIR=${_package_dir}"
            "-DCMAKE_SYSTEM_NAME=${_system}" "-DCMAKE_SYSTEM_PROCESSOR=${_processor}"
            -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE
        RESULT_VARIABLE _refuse_result OUTPUT_VARIABLE _refuse_stdout ERROR_VARIABLE _refuse_stderr)
    string(REGEX REPLACE "[ \t\r\n]+" " " _refuse_text "${_refuse_stdout} ${_refuse_stderr}")
    if(_refuse_result EQUAL 0 OR NOT _refuse_text MATCHES "unsupported target platform")
        message(FATAL_ERROR "the Darwin package did not refuse a ${_system} consumer by its "
            "platform guard (${_refuse_result})\n${_refuse_stdout}\n${_refuse_stderr}")
    endif()
    math(EXPR _checked_platforms "${_checked_platforms} + 1")
endforeach()
if(_checked_platforms LESS 2)
    message(FATAL_ERROR "expected the Linux and Windows platforms to be declared; checked ${_checked_platforms}")
endif()

file(WRITE "${_work}/negative/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumMirrorExportNegative LANGUAGES NONE)
include("@VELLUM_SOURCE_DIR@/tools/mirror/VellumMirrorPlatform.cmake")
set(CMAKE_SYSTEM_NAME FreeBSD)
vellum_mirror_require_packaging()
]=])
file(READ "${_work}/negative/CMakeLists.txt" _negative_text)
string(REPLACE "@VELLUM_SOURCE_DIR@" "${VELLUM_SOURCE_DIR}" _negative_text "${_negative_text}")
file(WRITE "${_work}/negative/CMakeLists.txt" "${_negative_text}")
execute_process(
    COMMAND "${CMAKE_COMMAND}" -S "${_work}/negative" -B "${_work}/negative-build"
    RESULT_VARIABLE _negative_result OUTPUT_VARIABLE _negative_stdout ERROR_VARIABLE _negative_stderr)
string(REGEX REPLACE "[ \t\r\n]+" " " _negative_text "${_negative_stderr}")
if(_negative_result EQUAL 0)
    message(FATAL_ERROR "undeclared FreeBSD mirror platform unexpectedly configured successfully")
endif()
if(NOT _negative_text MATCHES "requires a verified platform closure")
    message(FATAL_ERROR "FreeBSD was refused for the wrong reason:\n${_negative_stderr}")
endif()
message(STATUS "installed mirror export, per-platform refusal, and FreeBSD packaging control: ok")
