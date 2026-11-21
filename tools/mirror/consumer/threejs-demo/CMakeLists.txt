cmake_minimum_required(VERSION 3.24)
project(VellumThreeJsNativeDemo LANGUAGES CXX)

find_package(Vellum CONFIG REQUIRED)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "Darwin")
    message(FATAL_ERROR "The native Three.js demo requires the macOS SDK")
endif()
if(NOT VELLUM_THREEJS_ROOT)
    message(FATAL_ERROR "Vellum SDK has no installed Three.js payload")
endif()
set(_v8_manifest "${VELLUM_THREEJS_ROOT}/../v8/manifest.json")
if(NOT EXISTS "${_v8_manifest}")
    message(FATAL_ERROR "The native Three.js demo requires a Vellum SDK with V8 enabled")
endif()
file(READ "${_v8_manifest}" _v8)
string(JSON _v8_version GET "${_v8}" v8_version)

add_executable(vellum-threejs-native-demo ../threejs_v8_main.cpp)
target_compile_features(vellum-threejs-native-demo PRIVATE cxx_std_20)
target_link_libraries(vellum-threejs-native-demo PRIVATE
    Vellum::MirrorView_script
    Vellum::MirrorView_core
    Vellum::MirrorState_core
    Vellum::DawnHeaders)
target_link_options(vellum-threejs-native-demo PRIVATE -Wl,-stack_size,0x2000000)
target_compile_definitions(vellum-threejs-native-demo PRIVATE
    VELLUM_THREEJS_ROOT="${VELLUM_THREEJS_ROOT}"
    VELLUM_THREEJS_DEMO_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
    VELLUM_THREEJS_V8_VERSION="${_v8_version}")

include(CTest)
option(VELLUM_THREEJS_REQUIRE_CAPTURE "Fail capture tests when no real GPU adapter is available" OFF)
if(BUILD_TESTING)
    add_test(NAME vellum.threejs-native-demo-v8-identity
        COMMAND vellum-threejs-native-demo --print-engine-identity)
    add_test(NAME vellum.threejs-native-demo-module
        COMMAND vellum-threejs-native-demo --module-smoke)
    foreach(_mode IN ITEMS cube gltf-box)
        set(_test "vellum.threejs-native-demo-${_mode}-capture")
        add_test(NAME "${_test}"
            COMMAND "${CMAKE_COMMAND}"
                "-DDEMO_BIN=$<TARGET_FILE:vellum-threejs-native-demo>"
                "-DDEMO_MODE=${_mode}"
                "-DCAPTURE_PATH=${CMAKE_CURRENT_BINARY_DIR}/${_mode}.png"
                "-DREQUIRE_CAPTURE=${VELLUM_THREEJS_REQUIRE_CAPTURE}"
                -P "${CMAKE_CURRENT_SOURCE_DIR}/capture_test.cmake")
        set_tests_properties("${_test}" PROPERTIES
            ENVIRONMENT "TMPDIR=${CMAKE_CURRENT_BINARY_DIR}"
            TIMEOUT 60
            SKIP_REGULAR_EXPRESSION "SKIP: No real Dawn adapter available")
    endforeach()
    include(../../../../cmake/VellumTestClasses.cmake)
    vellum_apply_test_classes("${CMAKE_CURRENT_SOURCE_DIR}/../../../../scripts/ctest_classes.json")
endif()
