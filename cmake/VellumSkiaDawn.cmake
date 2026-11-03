set(VELLUM_SKIA_ARCHIVE "" CACHE FILEPATH
    "Path to the pinned macOS universal Skia/Dawn release archive")
set(VELLUM_SKIA_DIR "" CACHE PATH
    "Development-only path to the extracted pinned Skia/Dawn archive")

set(_vellum_expected_archive_sha256
    "dca73c0eee5b0f6d129fe357d0754f7165c6b436051e0de947aeedd3e1bde250")
set(_vellum_expected_skia_sha256
    "4f5e148866dab6f260a80ee6da006539b8f47885daece28489972726295fde1c")
set(_vellum_expected_dawn_sha256
    "6c6c5535e3b004ca260f388536c1baa737c11479d0a95865d174888d7d3077f3")
set(_vellum_expected_svg_sha256
    "651c113ade708034b19461016b219a216ef8b2f451e83e60931c9eae1aed594b")
set(_vellum_expected_skresources_sha256
    "af723156c3896a5d3a51f09f7bcf464795c5cedb2672cf07e646e2ecdba84024")
set(_vellum_expected_skshaper_sha256
    "a6a16bedd0bf001f813f03517a919f7c6932128537c60721c1c1be8f151a1e74")
set(_vellum_expected_skparagraph_sha256
    "4144a0a4d1ce6dadd2c53d379bf21a56f59d16922b2eeac3bff4a0c0768abf53")
set(_vellum_expected_skunicode_core_sha256
    "e3026eedb11a9af0c245e8f3e227f0ce92e383271e3da8b55e87fdefe89f93cb")
set(_vellum_expected_skunicode_icu_sha256
    "ea7352e2ebab5734852924be28ffba61e99db578f4b065a302cbff9dcae484cb")

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
   CMAKE_OSX_DEPLOYMENT_TARGET VERSION_LESS VELLUM_MACOS_DEPLOYMENT_TARGET)
    message(FATAL_ERROR
        "Vellum: the GPU artifact requires macOS ${VELLUM_MACOS_DEPLOYMENT_TARGET} or newer")
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
set(_vellum_svg "${_vellum_skia_libdir}/libsvg.a")
set(_vellum_skresources "${_vellum_skia_libdir}/libskresources.a")
set(_vellum_skshaper "${_vellum_skia_libdir}/libskshaper.a")
set(_vellum_skparagraph "${_vellum_skia_libdir}/libskparagraph.a")
set(_vellum_skunicode_core "${_vellum_skia_libdir}/libskunicode_core.a")
set(_vellum_skunicode_icu "${_vellum_skia_libdir}/libskunicode_icu.a")

if(NOT _vellum_skia_libdir OR
   NOT EXISTS "${_vellum_skia_core}" OR
   NOT EXISTS "${_vellum_dawn_core}" OR
   NOT EXISTS "${_vellum_svg}" OR
   NOT EXISTS "${_vellum_skresources}" OR
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
file(SHA256 "${_vellum_svg}" _vellum_svg_sha256)
file(SHA256 "${_vellum_skresources}" _vellum_skresources_sha256)
file(SHA256 "${_vellum_skshaper}" _vellum_skshaper_sha256)
file(SHA256 "${_vellum_skparagraph}" _vellum_skparagraph_sha256)
file(SHA256 "${_vellum_skunicode_core}" _vellum_skunicode_core_sha256)
file(SHA256 "${_vellum_skunicode_icu}" _vellum_skunicode_icu_sha256)
if(NOT _vellum_skia_sha256 STREQUAL _vellum_expected_skia_sha256 OR
   NOT _vellum_dawn_sha256 STREQUAL _vellum_expected_dawn_sha256 OR
   NOT _vellum_svg_sha256 STREQUAL _vellum_expected_svg_sha256 OR
   NOT _vellum_skresources_sha256 STREQUAL _vellum_expected_skresources_sha256 OR
   NOT _vellum_skshaper_sha256 STREQUAL _vellum_expected_skshaper_sha256 OR
   NOT _vellum_skparagraph_sha256 STREQUAL _vellum_expected_skparagraph_sha256 OR
   NOT _vellum_skunicode_core_sha256 STREQUAL _vellum_expected_skunicode_core_sha256 OR
   NOT _vellum_skunicode_icu_sha256 STREQUAL _vellum_expected_skunicode_icu_sha256)
    message(FATAL_ERROR
        "Vellum: extracted Skia/Dawn libraries do not match the locked "
        "chrome/m158 macOS universal tuple")
endif()

# This public headers-only target lets an embedding host compile its Dawn
# bootstrap and compute code. It intentionally supplies declarations only: the
# sole archive definitions live in Vellum::SkiaDawnProvider.
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

# One installed dylib owns the locked archives for both GPU and mirror users.
# Whole-archive linking makes the public Skia/Dawn API available independently
# of which entry points either Vellum client happens to use today.
add_library(vellum-skia-dawn-provider SHARED
    "${CMAKE_CURRENT_LIST_DIR}/skia_dawn_provider.cpp")
add_library(Vellum::SkiaDawnProvider ALIAS vellum-skia-dawn-provider)
file(RELATIVE_PATH _vellum_provider_to_mirror_lib
    "${CMAKE_INSTALL_FULL_LIBDIR}"
    "${CMAKE_INSTALL_PREFIX}/mirror/lib")
set(_vellum_provider_install_rpath "@loader_path")
if(NOT _vellum_provider_to_mirror_lib STREQUAL ".")
    list(APPEND _vellum_provider_install_rpath
        "@loader_path/${_vellum_provider_to_mirror_lib}")
endif()
set_target_properties(vellum-skia-dawn-provider PROPERTIES
    EXPORT_NAME SkiaDawnProvider
    MACOSX_RPATH ON
    INSTALL_RPATH "${_vellum_provider_install_rpath}"
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    INTERFACE_VELLUM_SKIA_DAWN_PROVIDER vellum-skia-dawn-provider)
set_property(TARGET vellum-skia-dawn-provider APPEND PROPERTY
    COMPATIBLE_INTERFACE_STRING VELLUM_SKIA_DAWN_PROVIDER)
target_include_directories(vellum-skia-dawn-provider PUBLIC
    $<BUILD_INTERFACE:${_vellum_skia_include}>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/vellum/skia>)
target_link_libraries(vellum-skia-dawn-provider PUBLIC Vellum::DawnHeaders)
install(DIRECTORY "${_vellum_skia_include}/include/"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/vellum/skia/include"
    FILES_MATCHING PATTERN "*.h")
install(DIRECTORY "${_vellum_skia_include}/modules/"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/vellum/skia/modules"
    FILES_MATCHING PATTERN "*.h")
set(_vellum_provider_archives
    "${_vellum_skparagraph}"
    "${_vellum_skshaper}"
    "${_vellum_skunicode_icu}"
    "${_vellum_skunicode_core}"
    "${_vellum_skresources}"
    "${_vellum_skia_core}"
    "${_vellum_dawn_core}")
target_link_libraries(vellum-skia-dawn-provider PRIVATE
    "$<LINK_LIBRARY:WHOLE_ARCHIVE,${_vellum_provider_archives}>"
    # libsvg.a bundles copies of Skia/resource objects. Its factory anchor
    # selects SVG objects normally, resolving dependencies through the owners
    # above instead of force-loading the embedded duplicate implementations.
    "${_vellum_svg}"
    "-framework ImageIO"
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

# The provider must link to the same bytes every time: the SDK artifact is
# built twice and compared. Some macOS SDKs (the macOS 15 SDK in Xcode 16) give
# the CoreServices umbrella symbol-level re-exports of libm entry points that
# libSystem also exports (_exp2, _log2, _nan, _nanf, _modff, _nextafterf, the
# fenv functions). The provider links CoreServices for FSEvents and
# LaunchServices, so both dylibs offer those names, and Apple's parallel linker
# (ld-prime) picks either owner from one link to the next: identical inputs
# bound them to libSystem in about five links of six and to CoreServices in
# the rest, changing the stubs, fixups and symbol table. The classic linker
# resolves the duplicate by command-line order (libSystem, which -lpthread
# names first), so the binding and the bytes stay fixed. Use it only when the
# SDK carries the duplicate; newer SDKs dropped those re-exports and newer
# linkers ignore -ld_classic.
set(_vellum_sdk_path "${CMAKE_OSX_SYSROOT}")
if(NOT IS_DIRECTORY "${_vellum_sdk_path}")
    execute_process(
        COMMAND xcrun --sdk macosx --show-sdk-path
        OUTPUT_VARIABLE _vellum_sdk_path
        OUTPUT_STRIP_TRAILING_WHITESPACE
        RESULT_VARIABLE _vellum_sdk_path_result)
    if(NOT _vellum_sdk_path_result EQUAL 0)
        set(_vellum_sdk_path "")
    endif()
endif()
set(_vellum_coreservices_tbd
    "${_vellum_sdk_path}/System/Library/Frameworks/CoreServices.framework/Versions/A/CoreServices.tbd")
set(_vellum_coreservices_reexports_libm OFF)
if(_vellum_sdk_path AND EXISTS "${_vellum_coreservices_tbd}")
    file(STRINGS "${_vellum_coreservices_tbd}" _vellum_coreservices_libm
        REGEX "_(feholdexcept|nextafterf)[^A-Za-z0-9_]")
    if(_vellum_coreservices_libm)
        set(_vellum_coreservices_reexports_libm ON)
    endif()
endif()
if(_vellum_coreservices_reexports_libm)
    include(CheckLinkerFlag)
    check_linker_flag(CXX "LINKER:-ld_classic" VELLUM_LINKER_ACCEPTS_LD_CLASSIC)
    if(VELLUM_LINKER_ACCEPTS_LD_CLASSIC)
        target_link_options(vellum-skia-dawn-provider PRIVATE "LINKER:-ld_classic")
        message(STATUS
            "Vellum: the SDK's CoreServices re-exports libm symbols; linking the "
            "Skia/Dawn provider with the classic linker for a deterministic binding")
    else()
        message(WARNING
            "Vellum: ${_vellum_coreservices_tbd} re-exports libm symbols that "
            "libSystem also exports, and the linker does not accept -ld_classic; "
            "the Skia/Dawn provider dylib may not link reproducibly")
    endif()
endif()

set(VELLUM_HAS_SKIA_DAWN ON)
message(STATUS
    "Vellum: locked Skia Graphite + Dawn found at ${VELLUM_SKIA_DIR} "
    "(macOS universal GPU host enabled; minimum macOS ${VELLUM_MACOS_DEPLOYMENT_TARGET})")
