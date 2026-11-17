add_library(vellum-authoring SHARED
    src/js_application.mm
    src/rendered_tree_materializer.mm
    src/service_bridge.cpp
    src/tree_dump.cpp)
add_library(Vellum::Authoring ALIAS vellum-authoring)

set_target_properties(vellum-authoring PROPERTIES
    EXPORT_NAME Authoring
    MACOSX_RPATH ON
    INSTALL_RPATH "@loader_path")

target_include_directories(vellum-authoring
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)
target_compile_features(vellum-authoring PUBLIC cxx_std_20)
target_compile_options(vellum-authoring PRIVATE -fobjc-arc)
target_link_libraries(vellum-authoring
    PUBLIC Vellum::Graphics
    PRIVATE
        "-framework Foundation"
        "-framework JavaScriptCore")

if(VELLUM_BUILD_TESTS)
    add_executable(vellum-clock-and-source-test
        tests/clock_and_source_test.cpp)
    target_include_directories(vellum-clock-and-source-test PRIVATE src)
    target_link_libraries(vellum-clock-and-source-test PRIVATE Vellum::Authoring)
    add_test(NAME vellum.clock-and-source COMMAND vellum-clock-and-source-test)

    add_executable(vellum-service-bridge-test
        tests/service_bridge_test.cpp)
    target_link_libraries(vellum-service-bridge-test PRIVATE Vellum::Authoring)
    add_test(NAME vellum.service-bridge COMMAND vellum-service-bridge-test)

    add_executable(vellum-rendered-tree-materializer-test
        tests/rendered_tree_materializer_test.cpp)
    target_include_directories(vellum-rendered-tree-materializer-test
        PRIVATE src)
    target_compile_definitions(vellum-rendered-tree-materializer-test PRIVATE
        VELLUM_VECTOR_FIXTURE_DIR="${PROJECT_SOURCE_DIR}/fixtures/vector-assets")
    target_link_libraries(
        vellum-rendered-tree-materializer-test PRIVATE Vellum::Authoring)
    add_test(
        NAME vellum.rendered-tree-materializer
        COMMAND vellum-rendered-tree-materializer-test)

    if(TARGET vellum-scene-capture-harness)
        add_executable(vellum-token-theme-gpu-test
            tests/token_theme_gpu_test.cpp)
        target_include_directories(vellum-token-theme-gpu-test PRIVATE src)
        target_link_libraries(vellum-token-theme-gpu-test PRIVATE
            Vellum::Authoring vellum-scene-capture-harness)
        set_target_properties(vellum-token-theme-gpu-test PROPERTIES
            OSX_ARCHITECTURES arm64
            MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
            BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")
        add_test(NAME vellum.gpu.token-theme
            COMMAND vellum-token-theme-gpu-test)
        set_tests_properties(vellum.gpu.token-theme PROPERTIES
            SKIP_RETURN_CODE 77)
    endif()
endif()

add_subdirectory(design_pipeline)
