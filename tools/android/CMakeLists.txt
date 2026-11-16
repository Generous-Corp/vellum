cmake_minimum_required(VERSION 3.24)

# Cross-compiles the platform-neutral framework units for Android. It is a
# standalone project, not part of the root build: the root build's framework
# path is the Darwin mirror, which refuses any other host. Configure it with
# the NDK toolchain file (see .github/workflows/android.yml).
#
# Units compiled: runtime, state, events, dataplane, simd (scalar) and platform
# (with its POSIX child-process backend). Not compiled, because each needs an
# input this lane does not supply: runtime's mbedTLS users (big_integer,
# crypto, http, license, model_download), simd's Highway and Accelerate backends,
# and the canvas, render and view units, which need the Skia/Dawn artifact.

project(VellumAndroid LANGUAGES CXX)

if(NOT ANDROID)
    message(FATAL_ERROR
        "tools/android builds for Android only; pass the NDK's android.toolchain.cmake")
endif()

set(VELLUM_CHOC_ROOT "" CACHE PATH
    "Root of the pinned CHOC checkout (the header-only utility library)")
if(NOT EXISTS "${VELLUM_CHOC_ROOT}/choc/text/choc_JSON.h")
    message(FATAL_ERROR
        "VELLUM_CHOC_ROOT must name the pinned CHOC checkout; "
        "${VELLUM_CHOC_ROOT}/choc/text/choc_JSON.h does not exist")
endif()

set(_root "${CMAKE_CURRENT_SOURCE_DIR}/../..")
get_filename_component(_root "${_root}" ABSOLUTE)
set(_core "${_root}/framework/core")

set(_excluded
    runtime/src/big_integer.cpp
    runtime/src/crypto.cpp
    runtime/src/http.cpp
    runtime/src/license.cpp
    runtime/src/model_download.cpp)

set(_sources)
foreach(_unit IN ITEMS runtime state events dataplane)
    file(GLOB _unit_sources CONFIGURE_DEPENDS "${_core}/${_unit}/src/*.cpp")
    list(APPEND _sources ${_unit_sources})
endforeach()
list(APPEND _sources
    ${_core}/simd/src/scalar.cpp
    ${_core}/platform/platform/posix/child_process_posix.cpp)
file(GLOB _platform_sources CONFIGURE_DEPENDS "${_core}/platform/src/*.cpp")
list(APPEND _sources ${_platform_sources})
foreach(_skip IN LISTS _excluded)
    list(REMOVE_ITEM _sources "${_core}/${_skip}")
endforeach()

# One source per line, the framework units plus the probe, so a lane can hold
# the number of compiled objects to what this file selected.
set(_selected ${_sources} "${CMAKE_CURRENT_SOURCE_DIR}/jni_probe.cpp")
list(JOIN _selected "\n" _selected_text)
file(WRITE "${CMAKE_BINARY_DIR}/android-sources.txt" "${_selected_text}\n")

add_library(vellum-android-framework STATIC ${_sources})
target_compile_features(vellum-android-framework PUBLIC cxx_std_20)
target_compile_options(vellum-android-framework PRIVATE -Wall -Wextra -Wno-unused-parameter)
target_include_directories(vellum-android-framework PUBLIC
    "${_core}/runtime/include" "${_core}/state/include" "${_core}/events/include"
    "${_core}/dataplane/include" "${_core}/simd/include" "${_core}/platform/include"
    "${_core}/canvas/include" "${_core}/view/include" "${_core}/render/include"
    "${_root}/foundation/include"
    "${_root}/framework/external/cpp-httplib" "${_root}/framework/external/miniz"
    "${_root}/framework/external/pugixml" "${_root}/framework/external/tweetnacl"
    "${VELLUM_CHOC_ROOT}")
set_target_properties(vellum-android-framework PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(vellum-android-framework PUBLIC log android)

# The JNI bridge headers are inert off Android, so a header that compiled to
# nothing would pass a plain compile. The probe names symbols they declare, and
# the CI lane reads them back from the object.
add_library(vellum-android-jni-probe OBJECT jni_probe.cpp)
target_link_libraries(vellum-android-jni-probe PRIVATE vellum-android-framework)
# An object library: has_asset_manager() is defined by the host sources, which
# are not part of this lane, so the probe is never linked.
