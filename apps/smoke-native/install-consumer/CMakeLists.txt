cmake_minimum_required(VERSION 3.24)

project(VellumInstallConsumer LANGUAGES CXX)

include(CTest)
find_package(Vellum 0.1 REQUIRED CONFIG)

add_executable(vellum-install-consumer main.cpp)
target_compile_features(vellum-install-consumer PRIVATE cxx_std_20)
if(TARGET Vellum::Graphics)
    target_link_libraries(vellum-install-consumer PRIVATE Vellum::Graphics)
    target_compile_definitions(vellum-install-consumer PRIVATE VELLUM_CONSUMER_HAS_GRAPHICS=1)
else()
    target_link_libraries(vellum-install-consumer PRIVATE Vellum::Runtime)
endif()

add_test(NAME vellum.install-consumer.run COMMAND vellum-install-consumer)
