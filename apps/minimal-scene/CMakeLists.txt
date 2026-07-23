cmake_minimum_required(VERSION 3.24)

project(VellumMinimalScene LANGUAGES CXX)

include(CTest)
find_package(Vellum 0.1 REQUIRED CONFIG)

add_executable(vellum-minimal-scene main.cpp)
target_compile_features(vellum-minimal-scene PRIVATE cxx_std_20)
if(TARGET Vellum::Gpu)
    target_link_libraries(vellum-minimal-scene PRIVATE Vellum::Gpu)
    target_compile_definitions(vellum-minimal-scene PRIVATE VELLUM_CONSUMER_HAS_GPU=1)
elseif(TARGET Vellum::Graphics)
    target_link_libraries(vellum-minimal-scene PRIVATE Vellum::Graphics)
    target_compile_definitions(vellum-minimal-scene PRIVATE VELLUM_CONSUMER_HAS_GRAPHICS=1)
else()
    target_link_libraries(vellum-minimal-scene PRIVATE Vellum::Runtime)
endif()

add_test(NAME vellum.minimal-scene.run COMMAND vellum-minimal-scene)
