# This consumer configures the installed export for every declared target,
# checks the platform closure through a GPU-off link, and rejects FreeBSD via
# the real packaging gate.
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
project(VellumMirrorExportConsumer LANGUAGES NONE)
include("@VELLUM_PACKAGE_DIR@/VellumTargets.cmake")
include("@VELLUM_MIRROR_TARGETS@/VellumMirrorTargets.cmake")
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
if(NOT _location)
    message(FATAL_ERROR "the exported mirror target has no imported location")
endif()
if(NOT TARGET Vellum::SkiaDawnProvider)
    message(FATAL_ERROR "the installed package did not export Vellum::SkiaDawnProvider")
endif()
get_target_property(_canvas_links Vellum::MirrorCanvas INTERFACE_LINK_LIBRARIES)
if(NOT "Vellum::SkiaDawnProvider" IN_LIST _canvas_links)
    message(FATAL_ERROR "the exported canvas target lost Vellum::SkiaDawnProvider: ${_canvas_links}")
endif()
add_library(platform_closure INTERFACE)
target_link_libraries(platform_closure INTERFACE Vellum::MirrorView_core Vellum::MirrorCanvas)
]=])
string(REPLACE "@VELLUM_PACKAGE_DIR@" "${_package_dir}" _positive_project
    "${_work}/positive/CMakeLists.txt")
string(REPLACE "@VELLUM_MIRROR_TARGETS@" "${_prefix}/mirror/lib/cmake/VellumMirror"
    _positive_project "${_positive_project}")
file(WRITE "${_work}/positive/CMakeLists.txt" "${_positive_project}")
file(GLOB _platform_records "${VELLUM_SOURCE_DIR}/provenance/framework/platforms/*/record.json")
set(_declared_platforms)
foreach(_record IN LISTS _platform_records)
    file(READ "${_record}" _record_json)
    string(JSON _system ERROR_VARIABLE _system_error
        GET "${_record_json}" configure identity system)
    string(JSON _processor ERROR_VARIABLE _processor_error
        GET "${_record_json}" configure identity processor)
    if(NOT _system_error AND NOT _processor_error AND
       (_system STREQUAL "Linux" OR _system STREQUAL "Windows"))
        list(APPEND _declared_platforms "${_system}|${_processor}")
    endif()
endforeach()
if(NOT _declared_platforms)
    message(FATAL_ERROR "no declared Linux or Windows platform records were found")
endif()
foreach(_platform IN LISTS _declared_platforms)
    string(REPLACE "|" ";" _platform_parts "${_platform}")
    list(GET _platform_parts 0 _system)
    list(GET _platform_parts 1 _processor)
    string(TOLOWER "${_system}" _platform_name)
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -S "${_work}/positive" -B "${_work}/positive-${_platform_name}"
            "-DCMAKE_SYSTEM_NAME=${_system}" "-DCMAKE_SYSTEM_PROCESSOR=${_processor}"
            -DCMAKE_BUILD_TYPE=Release -DPULP_ENABLE_GPU=OFF
            -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE
        RESULT_VARIABLE _positive_result OUTPUT_VARIABLE _positive_stdout ERROR_VARIABLE _positive_stderr)
    if(NOT _positive_result EQUAL 0)
        message(FATAL_ERROR "installed ${_system} mirror export configure failed (${_positive_result})\n"
            "${_positive_stdout}\n${_positive_stderr}")
    endif()
endforeach()

file(WRITE "${_work}/negative/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumMirrorExportNegative LANGUAGES NONE)
set(VELLUM_MIRROR_PLATFORM_RECORD_ROOT "@VELLUM_RECORD_ROOT@")
include("@VELLUM_SOURCE_DIR@/tools/mirror/VellumMirrorPlatform.cmake")
set(CMAKE_SYSTEM_NAME FreeBSD)
vellum_mirror_require_packaging()
]=])
string(REPLACE "@VELLUM_SOURCE_DIR@" "${VELLUM_SOURCE_DIR}" _negative_project
    "${_work}/negative/CMakeLists.txt")
string(REPLACE "@VELLUM_RECORD_ROOT@" "${VELLUM_SOURCE_DIR}/provenance/framework/platforms"
    _negative_project "${_negative_project}")
file(WRITE "${_work}/negative/CMakeLists.txt" "${_negative_project}")
execute_process(
    COMMAND "${CMAKE_COMMAND}" -S "${_work}/negative" -B "${_work}/negative-build"
        -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE
    RESULT_VARIABLE _negative_result OUTPUT_VARIABLE _negative_stdout ERROR_VARIABLE _negative_stderr)
if(_negative_result EQUAL 0)
    message(FATAL_ERROR "undeclared FreeBSD mirror platform unexpectedly configured successfully")
endif()
message(STATUS "installed per-platform mirror exports, GPU-off closure, and FreeBSD control: ok")
