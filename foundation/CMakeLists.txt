add_library(vellum-foundation
    src/platform.cpp
    src/status.cpp)
add_library(Vellum::Foundation ALIAS vellum-foundation)

set(_vellum_foundation_generated_include
    "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(VELLUM_GENERATED_VERSION_HEADER
    "${_vellum_foundation_generated_include}/vellum/foundation/version.hpp"
    PARENT_SCOPE)
configure_file(
    include/vellum/foundation/version.hpp.in
    "${_vellum_foundation_generated_include}/vellum/foundation/version.hpp"
    @ONLY)

set_target_properties(vellum-foundation PROPERTIES
    EXPORT_NAME Foundation)

target_include_directories(vellum-foundation
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<BUILD_INTERFACE:${_vellum_foundation_generated_include}>
        $<INSTALL_INTERFACE:include>)

target_compile_features(vellum-foundation PUBLIC cxx_std_20)
