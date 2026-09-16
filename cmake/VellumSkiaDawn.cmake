set(VELLUM_SKIA_ARCHIVE "" CACHE FILEPATH
    "Path to the pinned macOS universal Skia/Dawn release archive")
set(VELLUM_SKIA_DIR "" CACHE PATH
    "Development-only path to the extracted pinned Skia/Dawn archive")

set(_vellum_expected_archive_sha256
    "0ebfe03a209ceefe47edfeae70c3cc6c499583b74f35a26140ea55bad7f1e5a9")
set(_vellum_expected_skia_sha256
    "39f9b1e1c8663ded30afdccce47375e578696e5504c31c6ba1474f1dcddffcd9")
set(_vellum_expected_dawn_sha256
    "73727ddf86ffc34eea6fb6392d8d688f44e317ff37b3bb421f8223c8b8815dc9")
set(_vellum_expected_skshaper_sha256
    "c4120a6149cc63054766e040f020f379ff3e2172b3887d88e8c5f15eb29667c3")
set(_vellum_expected_skparagraph_sha256
    "0f295b4262d0dd26dbf29e69fbe6696a278aa6ae8fec9a935555ec1058665274")
set(_vellum_expected_skunicode_core_sha256
    "a79b68a3a5bb72b12de9b07b1f78b332eaa01c0de5eae0f8ff37c8c1fd57cbaa")
set(_vellum_expected_skunicode_icu_sha256
    "c4b68ea5b8a71740634c9d40b6f2fb4e91a5ac355ea2c5f7f19e5d2364b8f204")

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
   CMAKE_OSX_DEPLOYMENT_TARGET VERSION_LESS "13.0")
    message(FATAL_ERROR
        "Vellum: the locked Dawn archive requires macOS 13.0 or newer")
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
set(_vellum_skshaper "${_vellum_skia_libdir}/libskshaper.a")
set(_vellum_skparagraph "${_vellum_skia_libdir}/libskparagraph.a")
set(_vellum_skunicode_core "${_vellum_skia_libdir}/libskunicode_core.a")
set(_vellum_skunicode_icu "${_vellum_skia_libdir}/libskunicode_icu.a")

if(NOT _vellum_skia_libdir OR
   NOT EXISTS "${_vellum_skia_core}" OR
   NOT EXISTS "${_vellum_dawn_core}" OR
   NOT EXISTS "${_vellum_skshaper}" OR
   NOT EXISTS "${_vellum_skparagraph}" OR
   NOT EXISTS "${_vellum_skunicode_core}" OR
   NOT EXISTS "${_vellum_skunicode_icu}" OR
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
file(SHA256 "${_vellum_skshaper}" _vellum_skshaper_sha256)
file(SHA256 "${_vellum_skparagraph}" _vellum_skparagraph_sha256)
file(SHA256 "${_vellum_skunicode_core}" _vellum_skunicode_core_sha256)
file(SHA256 "${_vellum_skunicode_icu}" _vellum_skunicode_icu_sha256)
if(NOT _vellum_skia_sha256 STREQUAL _vellum_expected_skia_sha256 OR
   NOT _vellum_dawn_sha256 STREQUAL _vellum_expected_dawn_sha256 OR
   NOT _vellum_skshaper_sha256 STREQUAL _vellum_expected_skshaper_sha256 OR
   NOT _vellum_skparagraph_sha256 STREQUAL _vellum_expected_skparagraph_sha256 OR
   NOT _vellum_skunicode_core_sha256 STREQUAL _vellum_expected_skunicode_core_sha256 OR
   NOT _vellum_skunicode_icu_sha256 STREQUAL _vellum_expected_skunicode_icu_sha256)
    message(FATAL_ERROR
        "Vellum: extracted Skia/Dawn libraries do not match the locked "
        "chrome/m153 macOS universal tuple")
endif()

# This public headers-only target lets an embedding host compile its Dawn
# bootstrap and compute code. It intentionally supplies declarations only: the
# sole archive definitions remain private to the Vellum GPU dylib.
add_library(vellum-dawn-headers INTERFACE)
add_library(Vellum::DawnHeaders ALIAS vellum-dawn-headers)
set_target_properties(vellum-dawn-headers PROPERTIES EXPORT_NAME DawnHeaders)
target_include_directories(vellum-dawn-headers INTERFACE
    $<BUILD_INTERFACE:${_vellum_skia_include}>
    $<BUILD_INTERFACE:${_vellum_skia_include}/third_party/externals/dawn/include>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/vellum/dawn>)
target_compile_definitions(vellum-dawn-headers INTERFACE SK_GRAPHITE=1 SK_DAWN=1)
install(DIRECTORY "${_vellum_skia_include}/third_party/externals/dawn/include/"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/vellum/dawn")
install(DIRECTORY "${_vellum_skia_include}/dawn/"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/vellum/dawn/dawn")
install(DIRECTORY "${_vellum_skia_include}/webgpu/"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/vellum/dawn/webgpu")

add_library(VellumSkiaDawnHeaders INTERFACE)
target_link_libraries(VellumSkiaDawnHeaders INTERFACE Vellum::DawnHeaders)

# Keep static archives private to vellum-gpu. Hosts need Dawn declarations to
# supply the explicit bootstrap callback, but must resolve those calls through
# the selected Vellum provider rather than link another Dawn copy.
add_library(VellumSkiaDawn INTERFACE)
target_include_directories(VellumSkiaDawn INTERFACE "${_vellum_skia_include}")
target_link_libraries(VellumSkiaDawn INTERFACE VellumSkiaDawnHeaders)
target_link_libraries(VellumSkiaDawn INTERFACE
    "${_vellum_skparagraph}"
    "${_vellum_skshaper}"
    "${_vellum_skunicode_icu}"
    "${_vellum_skunicode_core}"
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
    "(macOS universal GPU host enabled; minimum macOS 13.0)")
