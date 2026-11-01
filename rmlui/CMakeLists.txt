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
#
# A renderer that draws through Vellum's canvas implements
# vellum::rmlui::RenderInterface in its own target linking Core; Core itself
# never depends on a Vellum renderer.
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
option(VELLUM_RMLUI_BUILD_TESTS "Build the RmlUi module tests" ${BUILD_TESTING})
option(VELLUM_RMLUI_INSTALL "Install the VellumRmlUi CMake package" ON)

include(cmake/VellumRmlUiDependencies.cmake)

add_library(vellum-rmlui-core SHARED
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

if(VELLUM_RMLUI_INSTALL)
    set(_vellum_rmlui_cmake_dir "${CMAKE_INSTALL_LIBDIR}/cmake/VellumRmlUi")
    install(TARGETS ${_vellum_rmlui_install_targets}
        EXPORT VellumRmlUiTargets
        COMPONENT VellumRmlUi
        LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
        ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
        RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
    install(DIRECTORY include/vellum
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
        COMPONENT VellumRmlUi)
    install(EXPORT VellumRmlUiTargets
        NAMESPACE VellumRmlUi::
        DESTINATION ${_vellum_rmlui_cmake_dir}
        COMPONENT VellumRmlUi)
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
