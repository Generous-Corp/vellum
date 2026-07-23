cmake_minimum_required(VERSION 3.24)

project(Vellum
    VERSION 0.1.0
    DESCRIPTION "Experimental audio-free native application kernel"
    LANGUAGES CXX)

if(APPLE)
    enable_language(OBJCXX)
endif()

include(CTest)
include(GNUInstallDirs)
include(CMakePackageConfigHelpers)

option(VELLUM_BUILD_TESTS "Build Vellum unit and smoke tests" ${BUILD_TESTING})
option(VELLUM_BUILD_SMOKE_NATIVE "Build the macOS native smoke application" ON)
option(VELLUM_ENABLE_GRAPHICS "Build the initial platform graphics backend" ON)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

add_subdirectory(foundation)
add_subdirectory(runtime)

if(VELLUM_ENABLE_GRAPHICS)
    add_subdirectory(graphics)
endif()

if(VELLUM_BUILD_SMOKE_NATIVE)
    if(APPLE AND VELLUM_ENABLE_GRAPHICS)
        add_subdirectory(apps/smoke-native)
    else()
        message(STATUS
            "Vellum: smoke-native is currently a macOS retained-canvas proof; skipping")
    endif()
endif()

if(VELLUM_BUILD_TESTS)
    enable_testing()
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)

    add_executable(vellum-foundation-test
        foundation/tests/foundation_test.cpp)
    target_link_libraries(vellum-foundation-test PRIVATE Vellum::Foundation)
    add_test(NAME vellum.foundation COMMAND vellum-foundation-test)

    add_executable(vellum-runtime-test
        runtime/tests/kernel_test.cpp)
    target_link_libraries(vellum-runtime-test PRIVATE Vellum::Runtime)
    add_test(NAME vellum.runtime COMMAND vellum-runtime-test)

    add_test(NAME vellum.install-consumer
        COMMAND "${CMAKE_COMMAND}"
            "-DVELLUM_SOURCE_DIR=${CMAKE_CURRENT_SOURCE_DIR}"
            "-DVELLUM_BUILD_DIR=${CMAKE_CURRENT_BINARY_DIR}"
            "-DVELLUM_INSTALL_CONSUMER_SOURCE=${CMAKE_CURRENT_SOURCE_DIR}/apps/smoke-native/install-consumer"
            "-DVELLUM_INSTALL_LIBDIR=${CMAKE_INSTALL_LIBDIR}"
            "-DTEST_CONFIG=$<CONFIG>"
            -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/RunInstallConsumer.cmake")
    add_test(NAME vellum.sdk-artifact
        COMMAND "${Python3_EXECUTABLE}"
            -m unittest cli.tests.test_sdk_artifact
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
endif()

set(_vellum_install_targets vellum-foundation vellum-runtime)
if(VELLUM_ENABLE_GRAPHICS)
    list(APPEND _vellum_install_targets vellum-graphics)
endif()

install(TARGETS ${_vellum_install_targets}
    EXPORT VellumTargets
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
    INCLUDES DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")

install(DIRECTORY foundation/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(FILES "${VELLUM_GENERATED_VERSION_HEADER}"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/vellum/foundation")
install(DIRECTORY runtime/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
if(VELLUM_ENABLE_GRAPHICS)
    install(DIRECTORY graphics/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
endif()

configure_package_config_file(
    cmake/VellumConfig.cmake.in
    "${CMAKE_CURRENT_BINARY_DIR}/VellumConfig.cmake"
    INSTALL_DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/Vellum")
write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/VellumConfigVersion.cmake"
    VERSION "${PROJECT_VERSION}"
    COMPATIBILITY SameMajorVersion)

install(EXPORT VellumTargets
    FILE VellumTargets.cmake
    NAMESPACE Vellum::
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/Vellum")
install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/VellumConfig.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/VellumConfigVersion.cmake"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/Vellum")
install(FILES LICENSE.md NOTICE.md DEPENDENCIES.md
    DESTINATION "${CMAKE_INSTALL_DATADIR}/doc/Vellum")
