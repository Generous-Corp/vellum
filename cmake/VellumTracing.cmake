# Native Perfetto tracing (development only; OFF by default; never ship enabled).
#
# VELLUM_ENABLE_TRACING=OFF is the default and the only shippable configuration:
# this module defines no target, fetches nothing and adds no compile definition
# or link input, so a default build is unchanged. With it ON, the version-pinned
# Perfetto amalgamation (Apache-2.0) is fetched by URL and SHA-256 and compiled
# as one position-independent static library, vellum-perfetto. vellum-tracing is
# the compile usage every mirrored framework target takes: the include directory
# and the switches that turn the framework's trace macros on (the mirror build
# adds the framework's own switch, tools/mirror/VellumMirror.cmake). Only the
# foundation unit's library links vellum-perfetto, so the process has one
# Perfetto and one trace session, shared by the framework and by the renderer
# through the frame trace adapter.
#
# Perfetto's TRACE_EVENT takes a mutex when a chunk rolls over, so it is not
# real-time safe; trace UI, render and process-level code only, never a live
# audio thread.

include_guard(GLOBAL)

if(NOT VELLUM_ENABLE_TRACING)
    return()
endif()

if(NOT VELLUM_ENABLE_MIRROR)
    message(FATAL_ERROR
        "Vellum: VELLUM_ENABLE_TRACING instruments the mirrored framework units; "
        "it needs VELLUM_ENABLE_MIRROR=ON")
endif()

message(STATUS
    "Vellum: VELLUM_ENABLE_TRACING=ON, development-only Perfetto tracing is "
    "compiled in. Do not ship this build.")

# The amalgamation is a release artifact (perfetto.h and perfetto.cc, no
# CMakeLists). Both pins are cache variables so a developer can move to another
# release by passing a matched pair; the digest check stays on either way.
# FETCHCONTENT_SOURCE_DIR_PERFETTO builds an already unpacked copy instead.
# These values must equal the Perfetto row of DEPENDENCIES.md and
# provenance/third-party-lock.json (tools/provenance/test_perfetto_pin.py).
set(VELLUM_PERFETTO_VERSION "v57.2" CACHE STRING "Pinned Perfetto SDK release tag")
set(VELLUM_PERFETTO_SHA256
    "c6fa3d89aee30f7da39402c9cd178c9f2e344544fda5c2109fd8457e319c3a2f"
    CACHE STRING "SHA-256 of the pinned perfetto-cpp-sdk-src.zip")

include(FetchContent)
FetchContent_Declare(vellum_perfetto
    URL "https://github.com/google/perfetto/releases/download/${VELLUM_PERFETTO_VERSION}/perfetto-cpp-sdk-src.zip"
    URL_HASH "SHA256=${VELLUM_PERFETTO_SHA256}"
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
# The archive holds no CMakeLists, so this only downloads and unpacks it.
FetchContent_MakeAvailable(vellum_perfetto)

add_library(vellum-perfetto STATIC "${vellum_perfetto_SOURCE_DIR}/perfetto.cc")
target_include_directories(vellum-perfetto SYSTEM PUBLIC "${vellum_perfetto_SOURCE_DIR}")
set_target_properties(vellum-perfetto PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    CXX_STANDARD 20
    CXX_STANDARD_REQUIRED ON)
target_compile_options(vellum-perfetto PRIVATE
    $<$<CXX_COMPILER_ID:AppleClang,Clang,GNU>:-w>)

add_library(vellum-tracing INTERFACE)
target_include_directories(vellum-tracing SYSTEM INTERFACE "${vellum_perfetto_SOURCE_DIR}")
target_compile_definitions(vellum-tracing INTERFACE VELLUM_NATIVE_TRACING=1)
