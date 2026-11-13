cmake_minimum_required(VERSION 3.24)

# An installed-package consumer that links only Vellum::MirrorView_core and
# uses Vellum's value model through it, as an editor binding framework widgets
# to a ParamModel does.
project(VellumMirrorValueConsumer LANGUAGES CXX)

include(CTest)
find_package(Vellum CONFIG REQUIRED)

if(NOT TARGET Vellum::MirrorView_core)
    message(FATAL_ERROR "the installed Vellum package has no Vellum::MirrorView_core")
endif()
get_target_property(_view_links Vellum::MirrorView_core INTERFACE_LINK_LIBRARIES)
if(NOT "Vellum::Value" IN_LIST _view_links)
    message(FATAL_ERROR "Vellum::MirrorView_core does not carry Vellum::Value: ${_view_links}")
endif()

add_executable(vellum-mirror-value-consumer main.cpp)
target_compile_features(vellum-mirror-value-consumer PRIVATE cxx_std_20)
target_link_libraries(vellum-mirror-value-consumer PRIVATE Vellum::MirrorView_core)

add_test(NAME vellum.mirror-value-consumer.run COMMAND vellum-mirror-value-consumer)
