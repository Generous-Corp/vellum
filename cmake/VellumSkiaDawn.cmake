set(VELLUM_SKIA_ARCHIVE "" CACHE FILEPATH
    "Path to the pinned macOS arm64 Skia/Dawn release archive")
set(VELLUM_SKIA_DIR "" CACHE PATH
    "Development-only path to the extracted pinned Skia/Dawn archive")

set(_vellum_expected_archive_sha256
    "13b0e9818c3b05db661af85cb1e2bf2ef10e30d468b81351dd90295237d17734")
set(_vellum_expected_skia_sha256
    "7820bb79b92ef3262a036b94f33f16c1e023cb9c5c29728ac71b1e59f86799e6")
set(_vellum_expected_dawn_sha256
    "8fad85ccedc8a7a9baf781a6e639be522baa9ba5805848a6ede1c6523619d3fe")

if(VELLUM_SKIA_ARCHIVE)
    if(NOT EXISTS "${VELLUM_SKIA_ARCHIVE}")
        message(FATAL_ERROR
            "Vellum: VELLUM_SKIA_ARCHIVE does not exist: ${VELLUM_SKIA_ARCHIVE}")
    endif()
    file(SHA256 "${VELLUM_SKIA_ARCHIVE}" _vellum_archive_sha256)
    if(NOT _vellum_archive_sha256 STREQUAL _vellum_expected_archive_sha256)
        message(FATAL_ERROR
            "Vellum: pinned Skia/Dawn archive digest mismatch; expected "
            "${_vellum_expected_archive_sha256}, got ${_vellum_archive_sha256}")
    endif()
    set(VELLUM_SKIA_DIR "${CMAKE_CURRENT_BINARY_DIR}/_deps/vellum-skia")
    file(REMOVE_RECURSE "${VELLUM_SKIA_DIR}")
    file(MAKE_DIRECTORY "${VELLUM_SKIA_DIR}")
    file(ARCHIVE_EXTRACT
        INPUT "${VELLUM_SKIA_ARCHIVE}"
        DESTINATION "${VELLUM_SKIA_DIR}")
endif()

set(VELLUM_HAS_SKIA_DAWN OFF)
if(NOT APPLE)
    if(VELLUM_REQUIRE_GPU)
        message(FATAL_ERROR
            "Vellum: the initial required GPU host is currently macOS-only")
    endif()
    return()
endif()

if(CMAKE_OSX_ARCHITECTURES AND
   NOT CMAKE_OSX_ARCHITECTURES STREQUAL "arm64")
    message(FATAL_ERROR
        "Vellum: the locked first GPU artifact supports only macOS arm64; "
        "requested CMAKE_OSX_ARCHITECTURES=${CMAKE_OSX_ARCHITECTURES}")
endif()
if(NOT CMAKE_OSX_ARCHITECTURES AND
   NOT CMAKE_SYSTEM_PROCESSOR STREQUAL "arm64")
    message(FATAL_ERROR
        "Vellum: the locked first GPU artifact supports only macOS arm64")
endif()
if(CMAKE_OSX_DEPLOYMENT_TARGET AND
   CMAKE_OSX_DEPLOYMENT_TARGET VERSION_LESS "15.0")
    message(FATAL_ERROR
        "Vellum: the locked Dawn archive requires macOS 15.0 or newer")
endif()

if(NOT VELLUM_SKIA_DIR)
    if(VELLUM_REQUIRE_GPU)
        message(FATAL_ERROR
            "Vellum: VELLUM_REQUIRE_GPU=ON requires VELLUM_SKIA_DIR")
    endif()
    return()
endif()

set(_vellum_skia_include "${VELLUM_SKIA_DIR}/build/include")
set(_vellum_skia_release_dir "${VELLUM_SKIA_DIR}/build/mac-gpu/lib/Release")
set(_vellum_skia_libdir "")
foreach(_vellum_candidate IN ITEMS
        "${_vellum_skia_release_dir}"
        "${_vellum_skia_release_dir}/${CMAKE_SYSTEM_PROCESSOR}"
        "${_vellum_skia_release_dir}/arm64"
        "${_vellum_skia_release_dir}/x86_64")
    if(EXISTS "${_vellum_candidate}/libskia.a" AND
       EXISTS "${_vellum_candidate}/libdawn_combined.a")
        set(_vellum_skia_libdir "${_vellum_candidate}")
        break()
    endif()
endforeach()
set(_vellum_skia_core "${_vellum_skia_libdir}/libskia.a")
set(_vellum_dawn_core "${_vellum_skia_libdir}/libdawn_combined.a")

if(NOT _vellum_skia_libdir OR
   NOT EXISTS "${_vellum_skia_core}" OR
   NOT EXISTS "${_vellum_dawn_core}" OR
   NOT EXISTS "${_vellum_skia_include}/include/core/SkCanvas.h" OR
   NOT EXISTS "${_vellum_skia_include}/third_party/externals/dawn/include/dawn/native/DawnNative.h")
    if(VELLUM_REQUIRE_GPU)
        message(FATAL_ERROR
            "Vellum: VELLUM_SKIA_DIR does not contain the required macOS "
            "Skia Graphite + Dawn artifact tuple: ${VELLUM_SKIA_DIR}")
    endif()
    message(WARNING
        "Vellum: GPU backend unavailable; VELLUM_SKIA_DIR does not contain "
        "the required macOS Skia Graphite + Dawn artifact tuple: "
        "${VELLUM_SKIA_DIR}")
    return()
endif()

file(SHA256 "${_vellum_skia_core}" _vellum_skia_sha256)
file(SHA256 "${_vellum_dawn_core}" _vellum_dawn_sha256)
if(NOT _vellum_skia_sha256 STREQUAL _vellum_expected_skia_sha256 OR
   NOT _vellum_dawn_sha256 STREQUAL _vellum_expected_dawn_sha256)
    message(FATAL_ERROR
        "Vellum: extracted Skia/Dawn libraries do not match the locked "
        "chrome/m150 macOS arm64 tuple")
endif()

add_library(VellumSkiaDawn INTERFACE)
target_include_directories(VellumSkiaDawn INTERFACE
    "${_vellum_skia_include}"
    "${_vellum_skia_include}/third_party/externals/dawn/include")
target_compile_definitions(VellumSkiaDawn INTERFACE SK_GRAPHITE=1 SK_DAWN=1)
target_link_libraries(VellumSkiaDawn INTERFACE
    "${_vellum_skia_core}"
    "${_vellum_dawn_core}"
    "-framework Cocoa"
    "-framework CoreFoundation"
    "-framework CoreGraphics"
    "-framework CoreText"
    "-framework Foundation"
    "-framework IOKit"
    "-framework IOSurface"
    "-framework Metal"
    "-framework MetalKit"
    "-framework QuartzCore"
    objc)

set(VELLUM_HAS_SKIA_DAWN ON)
message(STATUS
    "Vellum: locked Skia Graphite + Dawn found at ${VELLUM_SKIA_DIR} "
    "(macOS arm64 GPU host enabled; minimum macOS 15.0)")
