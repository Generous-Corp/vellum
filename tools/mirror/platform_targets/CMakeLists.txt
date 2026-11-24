# Evaluates tools/mirror/VellumMirror.cmake for one target platform and writes
# the properties it produced to ${PROBE_REPORT}. Run by test_mirror_platform_targets.py.
cmake_minimum_required(VERSION 3.24)
project(MirrorPlatformProbe LANGUAGES C CXX)
set(CMAKE_INSTALL_LIBDIR lib)
set(CMAKE_INSTALL_DATADIR share)

set(PROJECT_SOURCE_DIR "${PROBE_ROOT}")
list(PREPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}")
set(CMAKE_BUILD_TYPE Release)
set(VELLUM_MACOS_DEPLOYMENT_TARGET 13.0)
set(VELLUM_HAS_SKIA_DAWN ON)
set(VELLUM_SKIA_DIR "${CMAKE_CURRENT_BINARY_DIR}/skia")
set(_vellum_expected_archive_sha256 "probe")
set(VELLUM_ENABLE_MIRROR ON)
set(VELLUM_ENABLE_V8 ${PROBE_V8})
set(VELLUM_ENABLE_TRACING OFF)
set(VELLUM_ENABLE_GRAPHICS OFF)
set(VELLUM_ENABLE_GPU ON)
set(VELLUM_ENABLE_ADDRESS_SANITIZER OFF)

# What the parent project provides before it includes the module.
add_library(vellum-value INTERFACE)
add_library(Vellum::Value ALIAS vellum-value)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/provider.cpp" "int vellum_provider_probe;\n")
add_library(vellum-skia-dawn-provider SHARED "${CMAKE_CURRENT_BINARY_DIR}/provider.cpp")
add_library(Vellum::SkiaDawnProvider ALIAS vellum-skia-dawn-provider)

# The module's export set names both, so the generate step needs them in it.
install(TARGETS vellum-value vellum-skia-dawn-provider EXPORT VellumMirrorTargets)

include("${PROBE_ROOT}/tools/mirror/VellumMirror.cmake")

set(_report "")
foreach(_target IN ITEMS vellum-mirror-view-core vellum-mirror-view-script vellum-mirror-canvas
        pulp-view-core pulp-view-script vellum-skia-dawn-provider)
    foreach(_property IN ITEMS INSTALL_RPATH LINK_OPTIONS OSX_ARCHITECTURES SOURCES
            LINK_LIBRARIES INTERFACE_LINK_LIBRARIES COMPILE_DEFINITIONS)
        get_target_property(_value "${_target}" ${_property})
        if(NOT _value)
            set(_value "")
        endif()
        string(APPEND _report "${_target}|${_property}|${_value}\n")
    endforeach()
endforeach()
string(APPEND _report "darwin|${_vellum_mirror_darwin}|\n")
file(WRITE "${PROBE_REPORT}" "${_report}")
