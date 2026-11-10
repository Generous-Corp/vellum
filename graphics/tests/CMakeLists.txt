add_library(vellum-scene-capture-harness STATIC
    support/scene_capture_harness.cpp)
target_include_directories(vellum-scene-capture-harness PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(vellum-scene-capture-harness PUBLIC
    Vellum::Gpu Vellum::DawnHeaders)
target_compile_definitions(vellum-scene-capture-harness PRIVATE
    VELLUM_CAPTURE_GOLDEN_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/capture"
    VELLUM_CAPTURE_BUILD_GOLDEN_ROOT="${CMAKE_CURRENT_BINARY_DIR}")
set_target_properties(vellum-scene-capture-harness PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET})

add_executable(vellum-scene-capture-harness-test
    scene_capture_harness_test.cpp)
target_link_libraries(vellum-scene-capture-harness-test PRIVATE
    vellum-scene-capture-harness)
set_target_properties(vellum-scene-capture-harness-test PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")

set(_vellum_scene_capture_goldens
    "${CMAKE_CURRENT_BINARY_DIR}/scene-capture-goldens")
add_test(NAME vellum.scene-capture-harness
    COMMAND vellum-scene-capture-harness-test checks
        "${CMAKE_CURRENT_BINARY_DIR}/scene-capture-helper-goldens"
        "${CMAKE_CURRENT_BINARY_DIR}/scene-capture-harness-evidence.json")
add_test(NAME vellum.scene-capture-harness.record
    COMMAND vellum-scene-capture-harness-test record
        "${_vellum_scene_capture_goldens}")
add_test(NAME vellum.scene-capture-harness.compare
    COMMAND vellum-scene-capture-harness-test compare
        "${_vellum_scene_capture_goldens}")
set_tests_properties(vellum.scene-capture-harness.record PROPERTIES
    FIXTURES_SETUP vellum_scene_capture_goldens)
set_tests_properties(vellum.scene-capture-harness.compare PROPERTIES
    FIXTURES_REQUIRED vellum_scene_capture_goldens)

add_executable(vellum-partial-present-test
    partial_present_test.cpp)
target_link_libraries(vellum-partial-present-test PRIVATE
    vellum-scene-capture-harness)
set_target_properties(vellum-partial-present-test PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
add_test(NAME vellum.gpu.partial-present
    COMMAND vellum-partial-present-test)

add_executable(vellum-render-cache-gpu-test
    render_cache_gpu_test.cpp)
target_link_libraries(vellum-render-cache-gpu-test PRIVATE
    vellum-scene-capture-harness)
set_target_properties(vellum-render-cache-gpu-test PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
add_test(NAME vellum.gpu.render-cache
    COMMAND vellum-render-cache-gpu-test)

add_executable(vellum-partial-present-bench
    partial_present_bench.mm)
target_link_libraries(vellum-partial-present-bench PRIVATE
    vellum-scene-capture-harness
    "-framework QuartzCore")
set_target_properties(vellum-partial-present-bench PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
add_test(NAME vellum.gpu.partial-present-native
    COMMAND vellum-partial-present-bench --check-native)
set_tests_properties(vellum.gpu.partial-present-native PROPERTIES
    SKIP_RETURN_CODE 77)

add_executable(vellum-layer-cache-gpu-test
    layer_cache_gpu_test.cpp)
target_link_libraries(vellum-layer-cache-gpu-test PRIVATE
    vellum-scene-capture-harness)
# The test reaches SkiaDawnSurfaceTestAccess, which is not installed.
target_include_directories(vellum-layer-cache-gpu-test PRIVATE
    ${PROJECT_SOURCE_DIR}/graphics/src)
set_target_properties(vellum-layer-cache-gpu-test PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
add_test(NAME vellum.gpu.layer-cache
    COMMAND vellum-layer-cache-gpu-test)
set_tests_properties(vellum.gpu.layer-cache PROPERTIES
    SKIP_RETURN_CODE 77)

add_executable(vellum-layer-text-extent-test layer_text_extent_test.cpp)
target_link_libraries(vellum-layer-text-extent-test PRIVATE Vellum::Gpu)
target_include_directories(vellum-layer-text-extent-test PRIVATE
    ${PROJECT_SOURCE_DIR}/graphics/src)
set_target_properties(vellum-layer-text-extent-test PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
add_test(NAME vellum.layer-text-extent
    COMMAND vellum-layer-text-extent-test "${PROJECT_SOURCE_DIR}/assets/fonts")
