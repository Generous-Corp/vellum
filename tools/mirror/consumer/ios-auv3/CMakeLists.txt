cmake_minimum_required(VERSION 3.24)
project(VellumIOSAUv3Proof LANGUAGES CXX OBJCXX)

string(TOLOWER "${CMAKE_OSX_SYSROOT}" _vellum_ios_sdk)
if(NOT CMAKE_SYSTEM_NAME STREQUAL "iOS" OR
   NOT _vellum_ios_sdk MATCHES "(simulator|iphoneos)")
    message(FATAL_ERROR "Configure with the iOS simulator or device SDK")
endif()

include(CTest)

get_filename_component(vellum_root "${CMAKE_CURRENT_SOURCE_DIR}/../../../.." ABSOLUTE)
set(VELLUM_RMLUI_IOS_METAL_BUILD_DIR "${CMAKE_BINARY_DIR}/rmlui" CACHE PATH
    "Build directory for the iOS Metal compile consumer")
set(VELLUM_RMLUI_NATIVE ON CACHE BOOL "" FORCE)
set(VELLUM_RMLUI_METAL ON CACHE BOOL "" FORCE)
set(VELLUM_RMLUI_CPU ON CACHE BOOL "" FORCE)
set(VELLUM_RMLUI_FONTS ON CACHE BOOL "" FORCE)
set(VELLUM_RMLUI_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(VELLUM_RMLUI_INSTALL OFF CACHE BOOL "" FORCE)
option(VELLUM_RMLUI_IOS_METAL_RUNTIME_TEST
    "Build the linked headless iOS Metal renderer test" ON)
add_subdirectory("${vellum_root}/rmlui" "${VELLUM_RMLUI_IOS_METAL_BUILD_DIR}")

set(VELLUM_RMLUI_SOURCE_DIR "${vellum_root}/rmlui" CACHE PATH
    "RmlUi module source used by the iOS compile consumer")

add_test(NAME vellum.rmlui.metal-ios-compile
    COMMAND "${CMAKE_COMMAND}" --build "${CMAKE_BINARY_DIR}"
        --target vellum-rmlui-metal-ios-compile)

set(_metal_ios_test_sources
    "${vellum_root}/rmlui/tests/metal/metal_effects_test.cpp"
    "${vellum_root}/rmlui/tests/metal/metal_image_decode_test.mm"
    "${vellum_root}/rmlui/tests/metal/metal_layers_test.mm"
    "${vellum_root}/rmlui/tests/metal/metal_trim_test.mm"
    "${vellum_root}/rmlui/tests/metal/metal_filters_test.mm"
    "${vellum_root}/rmlui/tests/metal/metal_visual_test.mm"
    "${vellum_root}/rmlui/tests/metal/cpu_vs_metal_test.mm"
    "${vellum_root}/rmlui/tests/metal/soft_reference.mm")
add_library(vellum-rmlui-metal-ios-tests STATIC ${_metal_ios_test_sources})
target_compile_features(vellum-rmlui-metal-ios-tests PRIVATE cxx_std_20)
target_compile_options(vellum-rmlui-metal-ios-tests PRIVATE
    $<$<COMPILE_LANGUAGE:OBJCXX>:-fobjc-arc>)
target_compile_definitions(vellum-rmlui-metal-ios-tests PRIVATE
    VELLUM_RMLUI_TEST_FIXTURES="${vellum_root}/rmlui/tests/fixtures")
target_include_directories(vellum-rmlui-metal-ios-tests PRIVATE
    "${vellum_root}/rmlui/include" "${vellum_root}/rmlui/tests" "${vellum_root}/rmlui/src")
target_link_libraries(vellum-rmlui-metal-ios-tests PRIVATE
    VellumRmlUi::Native VellumRmlUi::Metal
    "-framework Metal" "-framework QuartzCore" "-framework Foundation"
    "-framework CoreGraphics" "-framework ImageIO")

add_custom_target(vellum-rmlui-metal-ios-compile
    DEPENDS vellum-rmlui-metal vellum-rmlui-metal-ios-tests)

if(VELLUM_RMLUI_IOS_METAL_RUNTIME_TEST)
    add_executable(vellum-rmlui-metal-ios-runtime
        "${vellum_root}/rmlui/tests/metal/cpu_vs_metal_test.mm")
    set_target_properties(vellum-rmlui-metal-ios-runtime PROPERTIES
        OSX_ARCHITECTURES arm64)
    target_compile_features(vellum-rmlui-metal-ios-runtime PRIVATE cxx_std_20)
    target_compile_options(vellum-rmlui-metal-ios-runtime PRIVATE -fobjc-arc)
    target_include_directories(vellum-rmlui-metal-ios-runtime PRIVATE
        "${vellum_root}/rmlui/include" "${vellum_root}/rmlui/tests" "${vellum_root}/rmlui/src")
    target_link_libraries(vellum-rmlui-metal-ios-runtime PRIVATE
        VellumRmlUi::Native VellumRmlUi::Metal VellumRmlUi::Cpu
        "-framework Metal" "-framework QuartzCore" "-framework Foundation"
        "-framework CoreGraphics" "-framework ImageIO")
    add_test(NAME vellum.rmlui.metal-ios-runtime
        COMMAND "$<TARGET_FILE:vellum-rmlui-metal-ios-runtime>"
            "${vellum_root}/rmlui" "${vellum_root}/rmlui/tests/fixtures")
    set_tests_properties(vellum.rmlui.metal-ios-runtime PROPERTIES SKIP_RETURN_CODE 77)
    add_dependencies(vellum-rmlui-metal-ios-compile vellum-rmlui-metal-ios-runtime)
endif()

option(VELLUM_RMLUI_IOS_METAL_NEGATIVE_CONTROL
    "Build the deliberately failing iOS Metal pixel expectation control" OFF)
if(VELLUM_RMLUI_IOS_METAL_NEGATIVE_CONTROL)
    add_executable(vellum-rmlui-metal-ios-runtime-control
        "${vellum_root}/rmlui/tests/metal/cpu_vs_metal_test.mm")
    set_target_properties(vellum-rmlui-metal-ios-runtime-control PROPERTIES
        OSX_ARCHITECTURES arm64)
    target_compile_features(vellum-rmlui-metal-ios-runtime-control PRIVATE cxx_std_20)
    target_compile_options(vellum-rmlui-metal-ios-runtime-control PRIVATE -fobjc-arc)
    target_compile_definitions(vellum-rmlui-metal-ios-runtime-control PRIVATE
        VELLUM_RMLUI_IOS_METAL_WRONG_EXPECTED_PIXEL)
    target_include_directories(vellum-rmlui-metal-ios-runtime-control PRIVATE
        "${vellum_root}/rmlui/include" "${vellum_root}/rmlui/tests" "${vellum_root}/rmlui/src")
    target_link_libraries(vellum-rmlui-metal-ios-runtime-control PRIVATE
        VellumRmlUi::Native VellumRmlUi::Metal VellumRmlUi::Cpu
        "-framework Metal" "-framework QuartzCore" "-framework Foundation"
        "-framework CoreGraphics" "-framework ImageIO")
    add_test(NAME vellum.rmlui.metal-ios-runtime-control
        COMMAND "$<TARGET_FILE:vellum-rmlui-metal-ios-runtime-control>"
            "${vellum_root}/rmlui" "${vellum_root}/rmlui/tests/fixtures")
    set_tests_properties(vellum.rmlui.metal-ios-runtime-control PROPERTIES
        WILL_FAIL TRUE SKIP_RETURN_CODE 77)
    add_dependencies(vellum-rmlui-metal-ios-compile vellum-rmlui-metal-ios-runtime-control)
endif()

add_test(NAME vellum.rmlui.metal-ios-cocoa-negative
    COMMAND "${CMAKE_COMMAND}"
        "-DCMAKE_CXX_COMPILER=${CMAKE_CXX_COMPILER}"
        "-DCMAKE_OSX_SYSROOT=${CMAKE_OSX_SYSROOT}"
        "-DSOURCE=${vellum_root}/tools/mirror/consumer/ios-auv3/metal_ios_cocoa_negative.mm"
        -P "${CMAKE_CURRENT_SOURCE_DIR}/metal_ios_negative_test.cmake")

set(view_root "${vellum_root}/framework/core/view")
set(controller_test "${vellum_root}/tools/framework-tests/ios_au_editor_controller_test.mm")

# Compile the UIKit contract in view-core and the AU-facing controller in its
# extension integration target.
add_library(vellum-ios-auv3-integration STATIC
    "${vellum_root}/integrations/vellum-auv3-ios/ios_au_editor_controller.mm")
set_target_properties(vellum-ios-auv3-integration PROPERTIES OBJCXX_STANDARD 20 OBJCXX_STANDARD_REQUIRED ON)
target_compile_options(vellum-ios-auv3-integration PRIVATE
    -fno-objc-arc -fapplication-extension -Werror=protocol -Werror=objc-method-access)

add_library(vellum-ios-auv3-proof STATIC
    "${view_root}/platform/ios/ios_au_editor_controller.mm"
    "${view_root}/platform/ios/ios_editor_surface_host.mm"
    "${controller_test}")
set_target_properties(vellum-ios-auv3-proof PROPERTIES OBJCXX_STANDARD 20 OBJCXX_STANDARD_REQUIRED ON)
target_compile_options(vellum-ios-auv3-proof PRIVATE
    -fno-objc-arc -fapplication-extension -Werror=protocol -Werror=objc-method-access)
file(GLOB framework_includes LIST_DIRECTORIES true "${vellum_root}/framework/core/*/include")
target_include_directories(vellum-ios-auv3-proof PRIVATE
    ${framework_includes} "${vellum_root}" "${vellum_root}/framework" "${vellum_root}/integrations")
target_include_directories(vellum-ios-auv3-integration PRIVATE
    ${framework_includes} "${vellum_root}" "${vellum_root}/framework" "${vellum_root}/integrations")
target_link_libraries(vellum-ios-auv3-proof PRIVATE vellum-ios-auv3-integration
    "-framework CoreAudioKit" "-framework AudioToolbox" "-framework UIKit")

set(PULP_SOURCE_DIR "" CACHE PATH "Pulp source with the audio-only AU adapter headers")
if(PULP_SOURCE_DIR)
    set(PULP_CHOC_DIR "" CACHE PATH "Checkout of Pulp's pinned choc headers")
    foreach(unit IN ITEMS audio format midi platform runtime simd state)
        list(APPEND pulp_includes "${PULP_SOURCE_DIR}/core/${unit}/include")
    endforeach()
    target_sources(vellum-ios-auv3-integration PRIVATE
        "${vellum_root}/integrations/pulp-auv3/ios_pulp_au_editor_entry.mm")
    target_include_directories(vellum-ios-auv3-proof PRIVATE
        "${PULP_SOURCE_DIR}/core/format/src" "${PULP_CHOC_DIR}" ${pulp_includes})
    target_include_directories(vellum-ios-auv3-integration PRIVATE
        "${PULP_SOURCE_DIR}/core/format/src" "${PULP_CHOC_DIR}" ${pulp_includes})
endif()

option(VELLUM_IOS_AUV3_LINK_TEST "Link the test against an installed iOS Vellum SDK" OFF)
if(VELLUM_IOS_AUV3_LINK_TEST)
    find_package(Vellum CONFIG REQUIRED)
    add_executable(vellum-ios-auv3-controller-test "${controller_test}")
    set_target_properties(vellum-ios-auv3-controller-test PROPERTIES
        OBJCXX_STANDARD 20 OBJCXX_STANDARD_REQUIRED ON)
    target_compile_options(vellum-ios-auv3-controller-test PRIVATE -fno-objc-arc)
    target_include_directories(vellum-ios-auv3-controller-test PRIVATE
        "${vellum_root}" "${vellum_root}/framework" "${vellum_root}/integrations")
    target_sources(vellum-ios-auv3-controller-test PRIVATE
        "${vellum_root}/integrations/vellum-auv3-ios/ios_au_editor_controller.mm"
        "${view_root}/platform/ios/ios_au_editor_controller.mm")
    target_link_libraries(vellum-ios-auv3-controller-test PRIVATE
        Vellum::MirrorView_core "-framework CoreAudioKit" "-framework AudioToolbox" "-framework UIKit")
endif()
