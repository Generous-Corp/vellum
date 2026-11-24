if(NOT DEFINED VELLUM_BUILD_DIR OR NOT DEFINED VELLUM_INSTALL_LIBDIR)
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

file(WRITE "${_work}/negative/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumMirrorExportNegative LANGUAGES CXX)
find_package(Vellum CONFIG REQUIRED)
add_library(undeclared INTERFACE)
target_link_libraries(undeclared INTERFACE Vellum::MirrorUndeclared)
]=])
execute_process(
    COMMAND "${CMAKE_COMMAND}" -S "${_work}/negative" -B "${_work}/negative-build"
        "-DVellum_DIR=${_package_dir}" -DCMAKE_BUILD_TYPE=Release
        -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE
    RESULT_VARIABLE _negative_result OUTPUT_VARIABLE _negative_stdout ERROR_VARIABLE _negative_stderr)
if(_negative_result EQUAL 0)
    message(FATAL_ERROR "undeclared mirror target unexpectedly configured successfully")
endif()
message(STATUS "installed mirror export target and undeclared-target controls: ok")
