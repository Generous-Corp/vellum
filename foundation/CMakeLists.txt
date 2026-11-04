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

# vellum::value: the generic value model Vellum widgets bind to (ParamModel,
# ValueFeed) and its in-memory implementations. It depends on nothing else in
# Vellum and on no audio framework; its headers install with the rest of
# foundation/include.
add_library(vellum-value
    src/value/in_memory_param_model.cpp
    src/value/in_memory_value_feed.cpp)
add_library(Vellum::Value ALIAS vellum-value)

set_target_properties(vellum-value PROPERTIES
    EXPORT_NAME Value)

target_include_directories(vellum-value
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)

target_compile_features(vellum-value PUBLIC cxx_std_20)
