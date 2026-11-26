cmake_minimum_required(VERSION 3.24)
project(vellum_js_bytecode_collision LANGUAGES CXX)

if(NOT VELLUM_SOURCE_DIR)
    message(FATAL_ERROR "VELLUM_SOURCE_DIR is required")
endif()

# The collision is diagnosed during helper configuration, before a generator
# needs to run. An imported stand-in keeps this consumer focused on that check.
add_executable(vellum-js-bytecode IMPORTED GLOBAL)
set_target_properties(vellum-js-bytecode PROPERTIES
    IMPORTED_LOCATION "${CMAKE_COMMAND}")
add_library(Vellum::MirrorView_script INTERFACE IMPORTED GLOBAL)
set(VELLUM_JS_BYTECODE_GENERATOR_SOURCE
    "${VELLUM_SOURCE_DIR}/cmake/vellum_js_bytecode_main.cpp")
set(VELLUM_JS_BYTECODE_ENCODER
    "${VELLUM_SOURCE_DIR}/framework/tools/cmake/scripts/encode_binary_data.py")
include("${VELLUM_SOURCE_DIR}/cmake/VellumJsBytecode.cmake")

if(COLLISION_KIND STREQUAL "case")
    set(_collision_sources
        "${CMAKE_CURRENT_SOURCE_DIR}/one/ui.js"
        "${CMAKE_CURRENT_SOURCE_DIR}/two/UI.js")
else()
    set(_collision_sources
        "${CMAKE_CURRENT_SOURCE_DIR}/one/duplicate.js"
        "${CMAKE_CURRENT_SOURCE_DIR}/two/duplicate.js")
endif()
vellum_add_js_bytecode(collision SOURCES ${_collision_sources})
get_property(_generator GLOBAL PROPERTY VELLUM_JS_BYTECODE_GENERATOR_TARGET)
if(NOT _generator OR _generator STREQUAL "vellum-js-bytecode")
    message(FATAL_ERROR "vellum_add_js_bytecode reused the unrelated pre-existing generator target")
endif()
