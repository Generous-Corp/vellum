cmake_minimum_required(VERSION 3.24)
project(VellumNativeGpuEditorConsumer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Vellum CONFIG REQUIRED)
find_package(VellumGpuRuntime CONFIG REQUIRED COMPONENTS Device)

add_executable(vellum-native-gpu-editor-consumer editor.cpp gpu_runtime.cpp)
target_link_libraries(vellum-native-gpu-editor-consumer PRIVATE
    Vellum::MirrorView_core VellumGpuRuntime::Device)

get_target_property(_editor_defs vellum-native-gpu-editor-consumer COMPILE_DEFINITIONS)
if(_editor_defs MATCHES "VELLUM_MIRROR_BUILD")
    message(FATAL_ERROR "the native editor consumer must not define VELLUM_MIRROR_BUILD")
endif()

