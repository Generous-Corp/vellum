cmake_minimum_required(VERSION 3.24)

project(VellumMinimalScene LANGUAGES CXX)

include(CTest)
find_package(Vellum 0.1 REQUIRED CONFIG)

add_executable(vellum-minimal-scene main.cpp)
target_compile_features(vellum-minimal-scene PRIVATE cxx_std_20)
if(TARGET Vellum::Gpu)
    target_link_libraries(vellum-minimal-scene PRIVATE Vellum::Gpu)
    target_compile_definitions(vellum-minimal-scene PRIVATE VELLUM_CONSUMER_HAS_GPU=1)
    # The source-tree consumer is itself a host and can supply the local native
    # bootstrap. An installed Vellum package deliberately does not smuggle a
    # Dawn provider into consumers; its host must supply an authenticated one.
    if(TARGET VellumSkiaDawn)
        target_link_libraries(vellum-minimal-scene PRIVATE VellumSkiaDawn)
        target_include_directories(vellum-minimal-scene PRIVATE "${PROJECT_SOURCE_DIR}/apps")
        target_compile_definitions(vellum-minimal-scene PRIVATE
            VELLUM_CONSUMER_HAS_GPU_NATIVE_BOOTSTRAP=1)
    endif()
elseif(TARGET Vellum::Graphics)
    target_link_libraries(vellum-minimal-scene PRIVATE Vellum::Graphics)
    target_compile_definitions(vellum-minimal-scene PRIVATE VELLUM_CONSUMER_HAS_GRAPHICS=1)
else()
    target_link_libraries(vellum-minimal-scene PRIVATE Vellum::Runtime)
endif()

add_test(NAME vellum.minimal-scene.run COMMAND vellum-minimal-scene)
