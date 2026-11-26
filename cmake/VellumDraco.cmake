# Native Draco mesh decoding (OFF by default).
#
# With VELLUM_ENABLE_DRACO=OFF this module defines no target, fetches nothing
# and adds no compile definition or link input, so a default build is
# unchanged and the decode_draco function is the stub that reports
# draco_decoder_available() == false. With it ON, the version-pinned Draco
# source release (Apache-2.0) is fetched by URL and SHA-256 and built as a
# position-independent static library, vellum-draco is the compile and link
# usage for the one source that needs it (framework/core/render/src/
# draco_decoder.cpp), and the mirror build (tools/mirror/VellumMirror.cmake)
# turns the decoder on in the render-2d unit. Only the decoder library is
# built: the encoder, transcoder and tools are off, so no third_party code of
# Draco's is compiled.

include_guard(GLOBAL)

if(NOT VELLUM_ENABLE_DRACO)
    return()
endif()

if(NOT VELLUM_ENABLE_MIRROR)
    message(FATAL_ERROR
        "Vellum: VELLUM_ENABLE_DRACO compiles the mirror's render-2d unit; "
        "it needs VELLUM_ENABLE_MIRROR=ON")
endif()

# Only macos-arm64 has a closure and a proof for the mirror build; fail closed
# elsewhere, taking the platform from CMake itself and not a cache variable.
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Darwin" OR NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(arm64|aarch64)$")
    message(FATAL_ERROR
        "Vellum: VELLUM_ENABLE_DRACO is supported only on macos-arm64 "
        "(this configure is ${CMAKE_SYSTEM_NAME}/${CMAKE_SYSTEM_PROCESSOR}); "
        "turn it off until another platform proves it")
endif()
if(CMAKE_OSX_ARCHITECTURES AND NOT CMAKE_OSX_ARCHITECTURES STREQUAL "arm64")
    message(FATAL_ERROR
        "Vellum: VELLUM_ENABLE_DRACO is supported only on macos-arm64 "
        "(CMAKE_OSX_ARCHITECTURES is '${CMAKE_OSX_ARCHITECTURES}')")
endif()

# These values must equal the Draco row of DEPENDENCIES.md and
# provenance/third-party-lock.json (tools/provenance/test_draco_pin.py). The
# SHA-256 is of the GitHub source archive for the tag; the commit is recorded
# for the reader and checked against the lock, not used to fetch.
set(VELLUM_DRACO_VERSION "1.5.9" CACHE STRING "Pinned Draco release tag")
set(VELLUM_DRACO_SHA256
    "5b84e0e212ccc7b03ace681760a65a66fe2481a5f54df65154cfc8f0cbb87148"
    CACHE STRING "SHA-256 of the pinned Draco source archive")

include(FetchContent)
FetchContent_Declare(vellum_draco
    URL "https://github.com/google/draco/archive/refs/tags/${VELLUM_DRACO_VERSION}.tar.gz"
    URL_HASH "SHA256=${VELLUM_DRACO_SHA256}"
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    EXCLUDE_FROM_ALL)

# Draco's own options, scoped so they do not leak into Vellum's cache: the
# decoder is all the mirror needs, and the static library is linked into a
# shared provider, so it must be position independent.
set(_vellum_draco_saved_pic "${CMAKE_POSITION_INDEPENDENT_CODE}")
set(_vellum_draco_saved_shared "${BUILD_SHARED_LIBS}")
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(BUILD_SHARED_LIBS OFF)
set(DRACO_TRANSCODER_SUPPORTED OFF CACHE BOOL "" FORCE)
set(DRACO_TESTS OFF CACHE BOOL "" FORCE)
set(DRACO_JS_GLUE OFF CACHE BOOL "" FORCE)
set(DRACO_WASM OFF CACHE BOOL "" FORCE)
set(DRACO_UNITY_PLUGIN OFF CACHE BOOL "" FORCE)
set(DRACO_MAYA_PLUGIN OFF CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(vellum_draco)
set(CMAKE_POSITION_INDEPENDENT_CODE "${_vellum_draco_saved_pic}")
set(BUILD_SHARED_LIBS "${_vellum_draco_saved_shared}")

add_library(vellum-draco INTERFACE)
# draco_features.h is generated into the Draco build directory.
target_include_directories(vellum-draco SYSTEM INTERFACE
    "${vellum_draco_SOURCE_DIR}/src"
    "${vellum_draco_BINARY_DIR}")
target_link_libraries(vellum-draco INTERFACE draco_static)
