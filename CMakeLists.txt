cmake_minimum_required(VERSION 3.24)

project(Vellum
    VERSION 0.1.2
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
option(VELLUM_ENABLE_GPU "Build the pinned Skia Graphite + Dawn GPU backend when its artifact is available" ON)
option(VELLUM_REQUIRE_GPU "Fail configure unless the requested Skia/Dawn GPU backend is available" OFF)
option(VELLUM_ENABLE_AUTHORING "Build the native JavaScript/TypeScript application host" ON)
option(VELLUM_ENABLE_WEB "Build the experimental browser/Wasm proof" OFF)
set(VELLUM_AUTHORING_TEST_BUNDLE "" CACHE FILEPATH
    "Optional @vellum/ui classic-script bundle for the real JavaScriptCore integration test")
set(VELLUM_PHASE3_TEST_BUNDLE "" CACHE FILEPATH
    "Optional unchanged Phase 3 classic-script bundle for the cross-runtime fixture test")

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# The first locked Dawn archive contains macOS 15.0 objects. Set the deployment
# target before creating any Vellum target so static objects later linked into
# the GPU application cannot silently raise the finished binary's minimum OS.
if(APPLE AND VELLUM_ENABLE_GPU AND
   (VELLUM_SKIA_ARCHIVE OR VELLUM_SKIA_DIR) AND
   NOT CMAKE_OSX_DEPLOYMENT_TARGET)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "15.0" CACHE STRING
        "Minimum macOS version for the locked Vellum GPU artifact" FORCE)
endif()

add_subdirectory(foundation)
add_subdirectory(runtime)
add_subdirectory(components)

if(VELLUM_ENABLE_GPU)
    include(cmake/VellumSkiaDawn.cmake)
endif()

if(VELLUM_ENABLE_GRAPHICS)
    add_subdirectory(graphics)
endif()

if(VELLUM_ENABLE_WEB)
    if(NOT EMSCRIPTEN)
        message(FATAL_ERROR "VELLUM_ENABLE_WEB requires the Emscripten toolchain")
    endif()
    if(NOT VELLUM_ENABLE_GRAPHICS)
        message(FATAL_ERROR "VELLUM_ENABLE_WEB requires VELLUM_ENABLE_GRAPHICS")
    endif()
    add_subdirectory(web)
endif()

if(APPLE AND VELLUM_ENABLE_GRAPHICS AND VELLUM_ENABLE_AUTHORING)
    add_subdirectory(authoring)
endif()

if(VELLUM_BUILD_SMOKE_NATIVE)
    if(APPLE AND VELLUM_ENABLE_GRAPHICS)
        add_subdirectory(apps/smoke-native)
    else()
        message(STATUS
            "Vellum: smoke-native is currently a macOS retained-canvas proof; skipping")
    endif()
endif()

if(APPLE AND TARGET vellum-gpu)
    if(TARGET vellum-authoring)
        add_subdirectory(apps/app-host)
    endif()
    add_subdirectory(apps/gpu-native)
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

    if(TARGET vellum-graphics)
        add_executable(vellum-capture-stats-test
            graphics/tests/capture_stats_test.cpp)
        target_link_libraries(vellum-capture-stats-test PRIVATE Vellum::Graphics)
        add_test(NAME vellum.capture-stats COMMAND vellum-capture-stats-test)

        add_executable(vellum-paint-command-test
            graphics/tests/paint_command_test.cpp)
        target_link_libraries(vellum-paint-command-test PRIVATE Vellum::Graphics)
        add_test(NAME vellum.paint-command COMMAND vellum-paint-command-test)
    endif()

    if(TARGET vellum-authoring)
        add_executable(vellum-authoring-test
            authoring/tests/js_application_test.cpp)
        target_link_libraries(vellum-authoring-test PRIVATE Vellum::Authoring)
        add_test(NAME vellum.authoring COMMAND vellum-authoring-test)
        add_executable(vellum-authoring-phase3-fixture-test
            authoring/tests/phase3_fixture_test.cpp)
        target_link_libraries(
            vellum-authoring-phase3-fixture-test PRIVATE Vellum::Authoring)
        if(VELLUM_AUTHORING_TEST_BUNDLE)
            if(NOT EXISTS "${VELLUM_AUTHORING_TEST_BUNDLE}")
                message(FATAL_ERROR
                    "VELLUM_AUTHORING_TEST_BUNDLE does not exist: "
                    "${VELLUM_AUTHORING_TEST_BUNDLE}")
            endif()
            add_test(NAME vellum.authoring-ui-bundle
                COMMAND vellum-authoring-test "${VELLUM_AUTHORING_TEST_BUNDLE}")
        endif()
        if(VELLUM_PHASE3_TEST_BUNDLE)
            if(NOT EXISTS "${VELLUM_PHASE3_TEST_BUNDLE}")
                message(FATAL_ERROR
                    "VELLUM_PHASE3_TEST_BUNDLE does not exist: "
                    "${VELLUM_PHASE3_TEST_BUNDLE}")
            endif()
            add_test(NAME vellum.authoring-phase3-fixture
                COMMAND vellum-authoring-phase3-fixture-test
                    "${VELLUM_PHASE3_TEST_BUNDLE}")
        endif()
    endif()

    # This consumer proves that the native CMake package is relocatable. A
    # WebAssembly configure produces a target-architecture package, so driving
    # its consumer with the host compiler would only report a false
    # 32-bit/64-bit package-version mismatch.
    if(NOT EMSCRIPTEN)
        add_test(NAME vellum.install-consumer
            COMMAND "${CMAKE_COMMAND}"
                "-DVELLUM_SOURCE_DIR=${CMAKE_CURRENT_SOURCE_DIR}"
                "-DVELLUM_BUILD_DIR=${CMAKE_CURRENT_BINARY_DIR}"
                "-DVELLUM_INSTALL_CONSUMER_SOURCE=${CMAKE_CURRENT_SOURCE_DIR}/apps/minimal-scene"
                "-DVELLUM_INSTALL_LIBDIR=${CMAKE_INSTALL_LIBDIR}"
                "-DTEST_CONFIG=$<CONFIG>"
                -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/RunInstallConsumer.cmake")
    endif()
    add_test(NAME vellum.sdk-artifact
        COMMAND "${Python3_EXECUTABLE}"
            -m unittest cli.tests.test_sdk_artifact
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME vellum.authoring-phase3-contracts
        COMMAND "${Python3_EXECUTABLE}"
            authoring/tests/test_phase3_contracts.py
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME vellum.provenance-unit
        COMMAND "${Python3_EXECUTABLE}"
            tools/provenance/test_verify_extraction.py
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME vellum.provenance-retirement
        COMMAND "${Python3_EXECUTABLE}"
            tools/provenance/verify_extraction.py
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME vellum.downstream-consumer-registry-unit
        COMMAND "${Python3_EXECUTABLE}"
            tools/provenance/test_verify_downstream_consumers.py
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
    add_test(NAME vellum.downstream-consumer-registry
        COMMAND "${Python3_EXECUTABLE}"
            tools/provenance/verify_downstream_consumers.py
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}")
endif()

set(_vellum_install_targets vellum-foundation vellum-runtime vellum-component-abi)
if(VELLUM_ENABLE_GRAPHICS)
    list(APPEND _vellum_install_targets vellum-graphics)
    if(TARGET vellum-gpu)
        list(APPEND _vellum_install_targets vellum-gpu)
    endif()
endif()
if(TARGET vellum-authoring)
    list(APPEND _vellum_install_targets vellum-authoring)
endif()

install(TARGETS ${_vellum_install_targets}
    EXPORT VellumTargets
    ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
    INCLUDES DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")

install(DIRECTORY foundation/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}"
    PATTERN "*.in" EXCLUDE)
install(FILES "${VELLUM_GENERATED_VERSION_HEADER}"
    DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/vellum/foundation")
install(DIRECTORY runtime/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
if(VELLUM_ENABLE_GRAPHICS)
    install(DIRECTORY graphics/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
endif()
if(TARGET vellum-authoring)
    install(DIRECTORY authoring/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
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
