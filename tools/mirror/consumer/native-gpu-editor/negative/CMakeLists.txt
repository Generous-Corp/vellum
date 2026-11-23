cmake_minimum_required(VERSION 3.24)
project(VellumNativeGpuEditorNegative LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Vellum CONFIG REQUIRED)

add_library(vellum-native-gpu-editor-negative OBJECT negative_editor.cpp)
target_link_libraries(vellum-native-gpu-editor-negative PRIVATE Vellum::MirrorView_core)
