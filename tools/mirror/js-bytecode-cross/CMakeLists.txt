cmake_minimum_required(VERSION 3.24)
project(vellum_js_bytecode_cross LANGUAGES CXX)

if(NOT VELLUM_SOURCE_DIR)
    message(FATAL_ERROR "VELLUM_SOURCE_DIR is required")
endif()

set(VELLUM_JS_BYTECODE_ENCODER
    "${VELLUM_SOURCE_DIR}/framework/tools/cmake/scripts/encode_binary_data.py")
include("${VELLUM_SOURCE_DIR}/cmake/VellumJsBytecode.cmake")
vellum_add_js_bytecode(cross_probe
    SOURCES "${VELLUM_SOURCE_DIR}/fixtures/js-bytecode/bytecode_fixture.js")
