cmake_minimum_required(VERSION 3.24)
project(VellumIOSAUv3Proof LANGUAGES CXX OBJCXX)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "iOS" OR NOT CMAKE_OSX_SYSROOT MATCHES "[Ss]imulator")
    message(FATAL_ERROR "Configure with the iOS simulator SDK")
endif()

get_filename_component(vellum_root "${CMAKE_CURRENT_SOURCE_DIR}/../../../.." ABSOLUTE)
set(view_root "${vellum_root}/framework/core/view")
set(controller_test "${vellum_root}/tools/framework-tests/ios_au_editor_controller_test.mm")

# Compile the same controller and CPU host sources the iOS mirror owns.
add_library(vellum-ios-auv3-proof STATIC
    "${view_root}/platform/ios/ios_au_editor_controller.mm"
    "${view_root}/platform/ios/ios_editor_surface_host.mm"
    "${controller_test}")
set_target_properties(vellum-ios-auv3-proof PROPERTIES OBJCXX_STANDARD 20 OBJCXX_STANDARD_REQUIRED ON)
target_compile_options(vellum-ios-auv3-proof PRIVATE
    -fno-objc-arc -fapplication-extension -Werror=protocol -Werror=objc-method-access)
file(GLOB framework_includes LIST_DIRECTORIES true "${vellum_root}/framework/core/*/include")
target_include_directories(vellum-ios-auv3-proof PRIVATE ${framework_includes})

set(PULP_SOURCE_DIR "" CACHE PATH "Pulp source with the audio-only AU adapter headers")
if(PULP_SOURCE_DIR)
    set(PULP_CHOC_DIR "" CACHE PATH "Checkout of Pulp's pinned choc headers")
    foreach(unit IN ITEMS audio format midi platform runtime simd state)
        list(APPEND pulp_includes "${PULP_SOURCE_DIR}/core/${unit}/include")
    endforeach()
    target_sources(vellum-ios-auv3-proof PRIVATE
        "${vellum_root}/integrations/pulp-auv3/ios_pulp_au_editor_entry.mm")
    target_include_directories(vellum-ios-auv3-proof PRIVATE
        "${PULP_SOURCE_DIR}/core/format/src" "${PULP_CHOC_DIR}" ${pulp_includes})
endif()

option(VELLUM_IOS_AUV3_LINK_TEST "Link the test against an installed iOS Vellum SDK" OFF)
if(VELLUM_IOS_AUV3_LINK_TEST)
    find_package(Vellum CONFIG REQUIRED)
    add_executable(vellum-ios-auv3-controller-test "${controller_test}")
    set_target_properties(vellum-ios-auv3-controller-test PROPERTIES
        OBJCXX_STANDARD 20 OBJCXX_STANDARD_REQUIRED ON)
    target_compile_options(vellum-ios-auv3-controller-test PRIVATE -fno-objc-arc)
    target_link_libraries(vellum-ios-auv3-controller-test PRIVATE
        Vellum::MirrorView_core "-framework CoreAudioKit" "-framework AudioToolbox" "-framework UIKit")
endif()
