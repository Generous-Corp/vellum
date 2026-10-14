cmake_minimum_required(VERSION 3.24)
project(VellumMirrorConsumer LANGUAGES CXX)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
find_package(Vellum CONFIG REQUIRED)

add_executable(vellum-mirror-consumer main.cpp)
target_link_libraries(vellum-mirror-consumer PRIVATE Vellum::MirrorCanvas)
