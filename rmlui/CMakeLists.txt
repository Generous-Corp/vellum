cmake_minimum_required(VERSION 3.24)

# The optional RmlUi module. It is its own project so it builds on its own
# (cmake -S rmlui) with nothing but its pinned dependencies, and so it can move
# to a repository of its own as one directory. Vellum adds it only when
# VELLUM_ENABLE_RMLUI=ON.
#
# Targets:
#   VellumRmlUi::Core          shared library; the RmlUi backend, asset
#                              providers, renderer-neutral draw commands and
#                              model/action/extension hosts. RmlUi (and FreeType
#                              when fonts are on) are linked privately.
#   VellumRmlUi::SoftRenderer  CPU rasteriser writing PNGs, for headless and CI.
#   VellumRmlUi::DevAssets     development-only hot-reload asset provider.
#   VellumRmlUi::Lua           sandboxed per-instance Lua 5.4 host for skins
#                              (VELLUM_RMLUI_LUA=ON).
#   VellumRmlUi::Native        static library; the open entry point. Links RmlUi
#                              publicly (RmlUi::Core, RmlUi::Debugger), so any
#                              Rml::RenderInterface draws it and the caller uses
#                              RmlUi's own API (VELLUM_RMLUI_NATIVE=ON). It never
#                              links Core: a process uses one or the other.
#   VellumRmlUi::Metal         static library; an Rml::RenderInterface on Metal
#                              (VELLUM_RMLUI_METAL=ON, Apple only, needs
#                              Native's packaging of RmlUi). It links only RmlUi
#                              and Apple frameworks.
#   VellumRmlUi::VellumRenderer draws the backend's commands as triangle meshes
#                              through Vellum's SkiaDawnSurface
#                              (VELLUM_RMLUI_VELLUM_RENDERER=ON). It is the only
#                              target that links Vellum; Core and SoftRenderer
#                              never do.
# On its own the module pins the macOS deployment target Vellum uses, so the
# archives Native installs (RmlUi, FreeType) can link into a plug-in that runs
# on older macOS than the build host. Inside a Vellum build the parent decides.
# The package records the target it was built with.
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR AND NOT DEFINED CMAKE_OSX_DEPLOYMENT_TARGET)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "13.3" CACHE STRING
        "Minimum macOS version for the VellumRmlUi libraries")
endif()

project(VellumRmlUi
    VERSION 0.1.0
    DESCRIPTION "Optional RmlUi module for Vellum"
    LANGUAGES C CXX)

if(PROJECT_IS_TOP_LEVEL)
    include(CTest)
    set(CMAKE_CXX_STANDARD 20)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    set(CMAKE_CXX_EXTENSIONS OFF)
endif()
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

option(VELLUM_RMLUI_FONTS
    "Build RmlUi's FreeType font engine from the pinned FreeType source" OFF)
option(VELLUM_RMLUI_LUA
    "Build the sandboxed Lua host (VellumRmlUi::Lua) from the pinned Lua source" OFF)
option(VELLUM_RMLUI_VELLUM_RENDERER
    "Build VellumRmlUi::VellumRenderer, which draws through Vellum's GPU renderer (needs Vellum::Gpu)" OFF)
option(VELLUM_RMLUI_NATIVE
    "Build VellumRmlUi::Native, which links RmlUi publicly and accepts any Rml::RenderInterface" OFF)
option(VELLUM_RMLUI_METAL
    "Build VellumRmlUi::Metal, an Rml::RenderInterface on Metal (Apple only; needs VELLUM_RMLUI_NATIVE)" OFF)
option(VELLUM_RMLUI_CPU
    "Build VellumRmlUi::Cpu, an Rml::RenderInterface on the CPU into an RGBA8 buffer (needs VELLUM_RMLUI_NATIVE)" OFF)
if(VELLUM_RMLUI_CPU AND NOT VELLUM_RMLUI_NATIVE)
    message(FATAL_ERROR "VELLUM_RMLUI_CPU needs VELLUM_RMLUI_NATIVE=ON, which packages RmlUi for it")
endif()
if(VELLUM_RMLUI_METAL)
    if(NOT APPLE)
        message(FATAL_ERROR "VELLUM_RMLUI_METAL needs an Apple platform")
    endif()
    if(NOT VELLUM_RMLUI_NATIVE)
        message(FATAL_ERROR "VELLUM_RMLUI_METAL needs VELLUM_RMLUI_NATIVE=ON, which packages RmlUi for it")
    endif()
    enable_language(OBJCXX)
endif()
option(VELLUM_RMLUI_BUILD_TESTS "Build the RmlUi module tests" ${BUILD_TESTING})
option(VELLUM_RMLUI_INSTALL "Install the VellumRmlUi CMake package" ON)

include(cmake/VellumRmlUiDependencies.cmake)

add_library(vellum-rmlui-core SHARED
    src/assets.cpp
    src/backend.cpp
    src/headless_recorder.cpp
    src/rooted_assets.cpp
    src/session.cpp)
add_library(VellumRmlUi::Core ALIAS vellum-rmlui-core)
set_target_properties(vellum-rmlui-core PROPERTIES
    EXPORT_NAME Core
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    VERSION ${PROJECT_VERSION}
    SOVERSION ${PROJECT_VERSION_MAJOR}.${PROJECT_VERSION_MINOR})
target_compile_features(vellum-rmlui-core PUBLIC cxx_std_20)
target_include_directories(vellum-rmlui-core PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
target_compile_definitions(vellum-rmlui-core PRIVATE
    VELLUM_RMLUI_BUILDING_CORE
    VELLUM_RMLUI_FONTS_ENABLED=$<BOOL:${VELLUM_RMLUI_FONTS}>)
target_link_libraries(vellum-rmlui-core PRIVATE RmlUi::Core)
# FreeType marks its public API with default visibility; keep it, and anything
# else from the private archives, out of the library's exports.
if(APPLE)
    target_link_options(vellum-rmlui-core PRIVATE
        "LINKER:-exported_symbol,__ZN6vellum5rmlui*"
        "LINKER:-exported_symbol,__ZNK6vellum5rmlui*"
        "LINKER:-exported_symbol,__ZTIN6vellum5rmlui*"
        "LINKER:-exported_symbol,__ZTSN6vellum5rmlui*"
        "LINKER:-exported_symbol,__ZTVN6vellum5rmlui*")
elseif(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    target_link_options(vellum-rmlui-core PRIVATE "LINKER:--exclude-libs,ALL")
endif()

add_library(vellum-rmlui-soft-renderer STATIC
    src/soft_renderer.cpp
    src/png_encoder.cpp)
add_library(VellumRmlUi::SoftRenderer ALIAS vellum-rmlui-soft-renderer)
set_target_properties(vellum-rmlui-soft-renderer PROPERTIES
    EXPORT_NAME SoftRenderer
    POSITION_INDEPENDENT_CODE ON)
target_include_directories(vellum-rmlui-soft-renderer PRIVATE
    $<BUILD_INTERFACE:${VELLUM_RMLUI_STB_INCLUDE_DIR}>)
target_link_libraries(vellum-rmlui-soft-renderer PUBLIC vellum-rmlui-core)
# The rasteriser's output is compared byte for byte; fused multiply-add would
# let the compiler change rounding between targets.
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(vellum-rmlui-soft-renderer PRIVATE -ffp-contract=off)
endif()

add_library(vellum-rmlui-dev-assets STATIC
    src/development_assets.cpp)
add_library(VellumRmlUi::DevAssets ALIAS vellum-rmlui-dev-assets)
set_target_properties(vellum-rmlui-dev-assets PROPERTIES
    EXPORT_NAME DevAssets
    POSITION_INDEPENDENT_CODE ON)
target_link_libraries(vellum-rmlui-dev-assets PUBLIC vellum-rmlui-core)

set(VELLUM_RMLUI_COMPONENTS Core SoftRenderer DevAssets)
set(_vellum_rmlui_install_targets
    vellum-rmlui-core vellum-rmlui-soft-renderer vellum-rmlui-dev-assets)

if(VELLUM_RMLUI_LUA)
    add_library(vellum-rmlui-lua STATIC
        src/lua_host.cpp)
    add_library(VellumRmlUi::Lua ALIAS vellum-rmlui-lua)
    set_target_properties(vellum-rmlui-lua PROPERTIES
        EXPORT_NAME Lua
        POSITION_INDEPENDENT_CODE ON)
    target_link_libraries(vellum-rmlui-lua
        PUBLIC vellum-rmlui-core
        PRIVATE vellum-rmlui-lua54)
    list(APPEND VELLUM_RMLUI_COMPONENTS Lua)
    list(APPEND _vellum_rmlui_install_targets vellum-rmlui-lua vellum-rmlui-lua54)
endif()

if(VELLUM_RMLUI_NATIVE)
    # The same runtime, asset and log sources as Core, compiled again into a
    # static library that exposes RmlUi instead of hiding it.
    add_library(vellum-rmlui-native STATIC
        src/assets.cpp
        src/native_context.cpp
        src/rooted_assets.cpp
        src/session.cpp)
    add_library(VellumRmlUi::Native ALIAS vellum-rmlui-native)
    set_target_properties(vellum-rmlui-native PROPERTIES
        EXPORT_NAME Native
        POSITION_INDEPENDENT_CODE ON)
    target_compile_features(vellum-rmlui-native PUBLIC cxx_std_20)
    target_include_directories(vellum-rmlui-native PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
    target_compile_definitions(vellum-rmlui-native
        PUBLIC VELLUM_RMLUI_STATIC
        PRIVATE VELLUM_RMLUI_FONTS_ENABLED=$<BOOL:${VELLUM_RMLUI_FONTS}>)
    target_link_libraries(vellum-rmlui-native PUBLIC RmlUi::Core RmlUi::Debugger)
    list(APPEND VELLUM_RMLUI_COMPONENTS Native)
endif()

if(VELLUM_RMLUI_METAL)
    add_library(vellum-rmlui-metal STATIC
        src/metal_render_interface.mm
        src/metal_effects.cpp)
    add_library(VellumRmlUi::Metal ALIAS vellum-rmlui-metal)
    set_target_properties(vellum-rmlui-metal PROPERTIES
        EXPORT_NAME Metal
        POSITION_INDEPENDENT_CODE ON)
    target_compile_features(vellum-rmlui-metal PUBLIC cxx_std_20)
    target_compile_options(vellum-rmlui-metal PRIVATE $<$<COMPILE_LANGUAGE:OBJCXX>:-fobjc-arc>)
    target_include_directories(vellum-rmlui-metal PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
    target_link_libraries(vellum-rmlui-metal
        PUBLIC RmlUi::Core "-framework Metal" "-framework QuartzCore" "-framework Foundation"
        PRIVATE "-framework CoreGraphics" "-framework ImageIO")
    list(APPEND VELLUM_RMLUI_COMPONENTS Metal)
endif()

if(VELLUM_RMLUI_CPU)
    add_library(vellum-rmlui-cpu STATIC
        src/cpu_render_interface.cpp)
    add_library(VellumRmlUi::Cpu ALIAS vellum-rmlui-cpu)
    set_target_properties(vellum-rmlui-cpu PROPERTIES
        EXPORT_NAME Cpu
        POSITION_INDEPENDENT_CODE ON)
    target_compile_features(vellum-rmlui-cpu PUBLIC cxx_std_20)
    target_include_directories(vellum-rmlui-cpu PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
    # The renderer may own worker threads (set_threads()).
    find_package(Threads REQUIRED)
    target_link_libraries(vellum-rmlui-cpu PUBLIC RmlUi::Core PRIVATE Threads::Threads)
    # Same output on every platform: no fused multiply-add in the projection.
    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
        target_compile_options(vellum-rmlui-cpu PRIVATE -ffp-contract=off)
    elseif(MSVC)
        target_compile_options(vellum-rmlui-cpu PRIVATE /fp:precise)
    endif()
    list(APPEND VELLUM_RMLUI_COMPONENTS Cpu)
endif()

if(VELLUM_RMLUI_VELLUM_RENDERER)
    # Inside a Vellum build the GPU target already exists; on its own the
    # module takes it from an installed Vellum package.
    if(NOT TARGET Vellum::Gpu)
        find_package(Vellum CONFIG REQUIRED)
    endif()
    if(NOT TARGET Vellum::Gpu OR NOT TARGET Vellum::DawnHeaders)
        message(FATAL_ERROR
            "VELLUM_RMLUI_VELLUM_RENDERER needs Vellum's GPU renderer (Vellum::Gpu and "
            "Vellum::DawnHeaders); configure Vellum with its locked Skia/Dawn archive")
    endif()
    add_library(vellum-rmlui-vellum-renderer STATIC
        src/vellum_renderer.cpp)
    add_library(VellumRmlUi::VellumRenderer ALIAS vellum-rmlui-vellum-renderer)
    set_target_properties(vellum-rmlui-vellum-renderer PROPERTIES
        EXPORT_NAME VellumRenderer
        POSITION_INDEPENDENT_CODE ON)
    target_link_libraries(vellum-rmlui-vellum-renderer PUBLIC vellum-rmlui-core Vellum::Gpu)
    list(APPEND VELLUM_RMLUI_COMPONENTS VellumRenderer)
endif()

# Only VellumRenderer may link Vellum; every other library here stays usable
# without it.
include(cmake/VellumRmlUiLinkBoundary.cmake)
get_property(_vellum_rmlui_targets DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}" PROPERTY BUILDSYSTEM_TARGETS)
vellum_rmlui_check_link_boundary(ALLOW vellum-rmlui-vellum-renderer TARGETS ${_vellum_rmlui_targets})

if(VELLUM_RMLUI_INSTALL)
    set(_vellum_rmlui_cmake_dir "${CMAKE_INSTALL_LIBDIR}/cmake/VellumRmlUi")
    install(TARGETS ${_vellum_rmlui_install_targets}
        EXPORT VellumRmlUiTargets
        COMPONENT VellumRmlUi
        LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
        ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
    # The Native-only headers include RmlUi's, which this package does not
    # carry; the Native component installs them with RmlUi.
    set(_vellum_rmlui_native_headers
        native.hpp element_classes_native.hpp midi_learn_native.hpp
        cpu_render_interface.hpp metal_render_interface.hpp)
    set(_vellum_rmlui_native_header_excludes)
    foreach(_header IN LISTS _vellum_rmlui_native_headers)
        list(APPEND _vellum_rmlui_native_header_excludes PATTERN "${_header}" EXCLUDE)
    endforeach()
    install(DIRECTORY include/vellum
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
        COMPONENT VellumRmlUi
        ${_vellum_rmlui_native_header_excludes})
    install(FILES assets/midi-learn.rcss assets/midi-panic.rcss
        DESTINATION ${CMAKE_INSTALL_DATADIR}/VellumRmlUi
        COMPONENT VellumRmlUi)
    # Core links RmlUi privately, so the package that carries Core carries
    # RmlUi's licence. Native installs the same file for its own component.
    FetchContent_GetProperties(vellum_rmlui_rmlui SOURCE_DIR _vellum_rmlui_core_rmlui_src)
    install(FILES "${_vellum_rmlui_core_rmlui_src}/LICENSE.txt"
        DESTINATION ${CMAKE_INSTALL_DATADIR}/doc/VellumRmlUi
        RENAME RmlUi-LICENSE.txt
        COMPONENT VellumRmlUi)
    install(EXPORT VellumRmlUiTargets
        NAMESPACE VellumRmlUi::
        DESTINATION ${_vellum_rmlui_cmake_dir}
        COMPONENT VellumRmlUi)
    if(VELLUM_RMLUI_VELLUM_RENDERER)
        # A separate export set, so the base package never needs Vellum; the
        # config loads it, after finding Vellum, only when it is asked for.
        install(TARGETS vellum-rmlui-vellum-renderer
            EXPORT VellumRmlUiVellumRendererTargets
            COMPONENT VellumRmlUi
            ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
        install(EXPORT VellumRmlUiVellumRendererTargets
            NAMESPACE VellumRmlUi::
            DESTINATION ${_vellum_rmlui_cmake_dir}
            COMPONENT VellumRmlUi)
    endif()
    if(VELLUM_RMLUI_NATIVE)
        # Native exposes RmlUi, so its package carries RmlUi: the pinned
        # static archives and headers, exported under RmlUi's own names
        # (RmlUi::Core, RmlUi::Debugger). It is a separate component and
        # export set, so the Core package stays free of RmlUi files.
        install(TARGETS vellum-rmlui-native
            EXPORT VellumRmlUiNativeTargets
            COMPONENT VellumRmlUiNative
            ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
        list(TRANSFORM _vellum_rmlui_native_headers PREPEND include/vellum/rmlui/
            OUTPUT_VARIABLE _vellum_rmlui_native_header_files)
        install(FILES ${_vellum_rmlui_native_header_files}
            DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/vellum/rmlui
            COMPONENT VellumRmlUiNative)
        install(EXPORT VellumRmlUiNativeTargets
            NAMESPACE VellumRmlUi::
            DESTINATION ${_vellum_rmlui_cmake_dir}
            COMPONENT VellumRmlUiNative)
        if(VELLUM_RMLUI_METAL)
            install(TARGETS vellum-rmlui-metal
                EXPORT VellumRmlUiMetalTargets
                COMPONENT VellumRmlUiNative
                ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
            install(EXPORT VellumRmlUiMetalTargets
                NAMESPACE VellumRmlUi::
                DESTINATION ${_vellum_rmlui_cmake_dir}
                COMPONENT VellumRmlUiNative)
        endif()
        if(VELLUM_RMLUI_CPU)
            install(TARGETS vellum-rmlui-cpu
                EXPORT VellumRmlUiCpuTargets
                COMPONENT VellumRmlUiNative
                ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
            install(EXPORT VellumRmlUiCpuTargets
                NAMESPACE VellumRmlUi::
                DESTINATION ${_vellum_rmlui_cmake_dir}
                COMPONENT VellumRmlUiNative)
        endif()
        if(VELLUM_RMLUI_FONTS)
            # RmlUi's static archive links FreeType privately; a consumer's
            # final link needs the pinned archive too.
            install(TARGETS freetype
                EXPORT RmlUiTargets
                COMPONENT VellumRmlUiNative
                ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
        endif()
        # The notices for what this component redistributes. A package built
        # with fonts carries FreeType's archive, so it carries the FTL too.
        set(_vellum_rmlui_doc_dir "${CMAKE_INSTALL_DATADIR}/doc/VellumRmlUi")
        FetchContent_GetProperties(vellum_rmlui_rmlui SOURCE_DIR _vellum_rmlui_rmlui_src)
        install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/NOTICE-Native.md"
            DESTINATION ${_vellum_rmlui_doc_dir}
            COMPONENT VellumRmlUiNative)
        install(FILES "${_vellum_rmlui_rmlui_src}/LICENSE.txt"
            DESTINATION ${_vellum_rmlui_doc_dir}
            RENAME RmlUi-LICENSE.txt
            COMPONENT VellumRmlUiNative)
        if(VELLUM_RMLUI_FONTS)
            FetchContent_GetProperties(vellum_rmlui_freetype SOURCE_DIR _vellum_rmlui_freetype_src)
            install(FILES "${_vellum_rmlui_freetype_src}/docs/FTL.TXT"
                DESTINATION ${_vellum_rmlui_doc_dir}
                RENAME FreeType-FTL.TXT
                COMPONENT VellumRmlUiNative)
        endif()
        install(EXPORT RmlUiTargets
            NAMESPACE RmlUi::
            FILE VellumRmlUiRmlUiTargets.cmake
            DESTINATION ${_vellum_rmlui_cmake_dir}
            COMPONENT VellumRmlUiNative)
    endif()
    configure_package_config_file(cmake/VellumRmlUiConfig.cmake.in
        "${CMAKE_CURRENT_BINARY_DIR}/VellumRmlUiConfig.cmake"
        INSTALL_DESTINATION ${_vellum_rmlui_cmake_dir})
    write_basic_package_version_file(
        "${CMAKE_CURRENT_BINARY_DIR}/VellumRmlUiConfigVersion.cmake"
        COMPATIBILITY SameMinorVersion)
    install(FILES
        "${CMAKE_CURRENT_BINARY_DIR}/VellumRmlUiConfig.cmake"
        "${CMAKE_CURRENT_BINARY_DIR}/VellumRmlUiConfigVersion.cmake"
        DESTINATION ${_vellum_rmlui_cmake_dir}
        COMPONENT VellumRmlUi)
endif()

if(VELLUM_RMLUI_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()
