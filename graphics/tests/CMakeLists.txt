enable_testing()

add_library(vellum-scene-capture-harness STATIC
    support/scene_capture_harness.cpp)
target_include_directories(vellum-scene-capture-harness PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(vellum-scene-capture-harness PUBLIC
    Vellum::Gpu Vellum::DawnHeaders)
target_compile_definitions(vellum-scene-capture-harness PRIVATE
    VELLUM_CAPTURE_GOLDEN_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/capture")

add_executable(vellum-scene-capture-harness-test
    scene_capture_harness_test.cpp)
target_link_libraries(vellum-scene-capture-harness-test PRIVATE
    vellum-scene-capture-harness)
target_compile_definitions(vellum-scene-capture-harness-test PRIVATE
    VELLUM_SCENE_CAPTURE_REQUIRE_DEVICE=$<BOOL:${VELLUM_REQUIRE_GPU}>)
add_test(NAME vellum.scene-capture-harness
    COMMAND vellum-scene-capture-harness-test
        "${CMAKE_CURRENT_BINARY_DIR}/scene-capture-goldens"
        "${CMAKE_CURRENT_BINARY_DIR}/scene-capture-harness-evidence.json")
set_tests_properties(vellum.scene-capture-harness PROPERTIES
    SKIP_RETURN_CODE 77)
