cmake_minimum_required(VERSION 3.24)

# An installed-package consumer that embeds build-time QuickJS bytecode through
# vellum_add_js_bytecode() and registers it with the view-script unit, as an
# editor shipping a large scripted UI does. It names no build-tree path: the
# generator source and encoder come from the installed package.
project(VellumJsBytecodeConsumer LANGUAGES CXX)

include(CTest)
find_package(Vellum CONFIG REQUIRED)

if(NOT COMMAND vellum_add_js_bytecode)
    message(FATAL_ERROR "the installed Vellum package does not define vellum_add_js_bytecode")
endif()

set(_fixture "${CMAKE_CURRENT_SOURCE_DIR}/../../../fixtures/js-bytecode/bytecode_fixture.js")
configure_file("${_fixture}"
    "${CMAKE_CURRENT_BINARY_DIR}/bridge/bytecode_bridge_fixture.js" COPYONLY)
vellum_add_js_bytecode(consumer_script_bytecode SOURCES "${_fixture}")
vellum_add_js_bytecode(consumer_bridge_script_bytecode
    SOURCES "${CMAKE_CURRENT_BINARY_DIR}/bridge/bytecode_bridge_fixture.js"
    NAMESPACE consumer_script_bytecode
    WIDGET_BRIDGE_SCRIPT)

add_executable(vellum-js-bytecode-consumer main.cpp)
target_compile_features(vellum-js-bytecode-consumer PRIVATE cxx_std_20)
target_link_libraries(vellum-js-bytecode-consumer PRIVATE
    Vellum::MirrorView_script consumer_script_bytecode consumer_bridge_script_bytecode)
target_compile_definitions(vellum-js-bytecode-consumer PRIVATE
    VELLUM_TEST_JS_BYTECODE_FIXTURE="${_fixture}")

add_test(NAME vellum.js-bytecode-consumer.run COMMAND vellum-js-bytecode-consumer)
