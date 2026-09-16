cmake_minimum_required(VERSION 3.24)
project(VellumLegacyConfigConsumer LANGUAGES CXX)

include(CTest)
find_package(Vellum 0.1 REQUIRED CONFIG)

add_executable(vellum-legacy-config-consumer main.cpp)
target_compile_features(vellum-legacy-config-consumer PRIVATE cxx_std_20)
target_include_directories(vellum-legacy-config-consumer BEFORE PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/legacy_include")
target_link_libraries(vellum-legacy-config-consumer PRIVATE Vellum::Gpu)
add_test(NAME vellum.legacy-config-consumer.run COMMAND vellum-legacy-config-consumer)
