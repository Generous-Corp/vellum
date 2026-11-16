add_library(vellum-scene-capture-harness STATIC
    support/scene_capture_harness.cpp)
target_include_directories(vellum-scene-capture-harness PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(vellum-scene-capture-harness PUBLIC
    Vellum::Gpu Vellum::DawnHeaders)
target_compile_definitions(vellum-scene-capture-harness PRIVATE
    VELLUM_CAPTURE_GOLDEN_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/capture"
    VELLUM_CAPTURE_IMAGE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/images"
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

add_executable(vellum-animation-gpu-test
    animation_gpu_test.cpp)
target_link_libraries(vellum-animation-gpu-test PRIVATE
    vellum-scene-capture-harness)
# The test reaches SkiaDawnSurfaceTestAccess and the animation rules, which
# are not installed.
target_include_directories(vellum-animation-gpu-test PRIVATE
    ${PROJECT_SOURCE_DIR}/graphics/src)
set_target_properties(vellum-animation-gpu-test PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
add_test(NAME vellum.gpu.animation
    COMMAND vellum-animation-gpu-test)

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

add_executable(vellum-image-gpu-test
    image_gpu_test.cpp)
target_link_libraries(vellum-image-gpu-test PRIVATE
    vellum-scene-capture-harness)
target_compile_definitions(vellum-image-gpu-test PRIVATE
    VELLUM_CAPTURE_IMAGE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/fixtures/images")
set_target_properties(vellum-image-gpu-test PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
add_test(NAME vellum.gpu.image-node
    COMMAND vellum-image-gpu-test)

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

add_executable(vellum-effect-gpu-test
    effect_gpu_test.cpp)
target_link_libraries(vellum-effect-gpu-test PRIVATE
    vellum-scene-capture-harness)
# The test reaches SkiaDawnSurfaceTestAccess, which is not installed.
target_include_directories(vellum-effect-gpu-test PRIVATE
    ${PROJECT_SOURCE_DIR}/graphics/src)
set_target_properties(vellum-effect-gpu-test PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
add_test(NAME vellum.gpu.effects
    COMMAND vellum-effect-gpu-test)
set_tests_properties(vellum.gpu.effects PROPERTIES
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

# Vector assets: the static rendered vector fixtures, materialized and drawn
# on the GPU, with pixel oracles and damage sweeps. The same binary prints
# the canvas shell's plans for the browser oracle.
if(VELLUM_ENABLE_AUTHORING AND APPLE)
    add_executable(vellum-vector-asset-gpu-test vector_asset_gpu_test.cpp)
    target_link_libraries(vellum-vector-asset-gpu-test PRIVATE
        vellum-scene-capture-harness vellum-authoring)
    target_include_directories(vellum-vector-asset-gpu-test PRIVATE
        ${PROJECT_SOURCE_DIR}/authoring/src
        ${PROJECT_SOURCE_DIR}/web/src)
    target_compile_definitions(vellum-vector-asset-gpu-test PRIVATE
        VELLUM_VECTOR_FIXTURE_DIR="${PROJECT_SOURCE_DIR}/fixtures/vector-assets")
    set_target_properties(vellum-vector-asset-gpu-test PROPERTIES
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
        BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>;$<TARGET_FILE_DIR:vellum-authoring>")
    add_test(NAME vellum.gpu.vector-assets
        COMMAND vellum-vector-asset-gpu-test)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME vellum.web.vector-canvas-oracle
        COMMAND "${Python3_EXECUTABLE}"
            "${PROJECT_SOURCE_DIR}/web/tests/run_vector_canvas_oracle.py"
            --plans "$<TARGET_FILE:vellum-vector-asset-gpu-test>"
            --shell "${PROJECT_SOURCE_DIR}/web/shell")
    set_tests_properties(vellum.web.vector-canvas-oracle PROPERTIES SKIP_RETURN_CODE 77)
    find_program(VELLUM_NODE_EXECUTABLE NAMES node)
    if(VELLUM_NODE_EXECUTABLE)
        add_test(NAME vellum.web.vector-canvas-builder
            COMMAND "${VELLUM_NODE_EXECUTABLE}"
                "${PROJECT_SOURCE_DIR}/web/tests/vector_canvas_builder_test.mjs")
    endif()
endif()
add_executable(vellum-plot-gpu-test
    plot_gpu_test.cpp)
target_link_libraries(vellum-plot-gpu-test PRIVATE
    vellum-scene-capture-harness)
# The test reaches SkiaDawnSurfaceTestAccess and the plot hooks, which are not
# installed.
target_include_directories(vellum-plot-gpu-test PRIVATE
    ${PROJECT_SOURCE_DIR}/graphics/src)
set_target_properties(vellum-plot-gpu-test PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
add_test(NAME vellum.gpu.plots
    COMMAND vellum-plot-gpu-test)
set_tests_properties(vellum.gpu.plots PROPERTIES
    SKIP_RETURN_CODE 77)
