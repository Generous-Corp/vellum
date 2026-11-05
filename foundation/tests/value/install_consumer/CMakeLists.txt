cmake_minimum_required(VERSION 3.24)

project(VellumValueConsumer LANGUAGES CXX)

include(CTest)
find_package(Vellum 0.1 REQUIRED CONFIG)

if(NOT TARGET Vellum::Value)
    message(FATAL_ERROR "the installed Vellum package does not export Vellum::Value")
endif()

add_executable(vellum-value-consumer main.cpp)
target_compile_features(vellum-value-consumer PRIVATE cxx_std_20)
target_link_libraries(vellum-value-consumer PRIVATE Vellum::Value)

add_test(NAME vellum.value-consumer.run COMMAND vellum-value-consumer)
