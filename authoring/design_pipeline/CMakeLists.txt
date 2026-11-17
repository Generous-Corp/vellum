if(NOT TARGET Vellum::MirrorDesign_import)
    return()
endif()

add_library(vellum-design-pipeline STATIC src/design_pipeline.cpp)
add_library(Vellum::DesignPipeline ALIAS vellum-design-pipeline)
set_target_properties(vellum-design-pipeline PROPERTIES
    EXPORT_NAME DesignPipeline
    POSITION_INDEPENDENT_CODE ON
    CXX_STANDARD 20
    CXX_STANDARD_REQUIRED ON
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET})
target_include_directories(vellum-design-pipeline
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        ${_vellum_mirror_public_includes}
        "${VELLUM_MIRROR_BUILD_INFO_DIR}"
        "${VELLUM_MIRROR_CHOC_ROOT}")
target_link_libraries(vellum-design-pipeline PUBLIC Vellum::MirrorDesign_import)

if(VELLUM_BUILD_TESTS)
    add_executable(vellum-design-pipeline-test tests/design_pipeline_test.cpp)
    target_link_libraries(vellum-design-pipeline-test PRIVATE Vellum::DesignPipeline)
    target_compile_definitions(vellum-design-pipeline-test PRIVATE
        VELLUM_DESIGN_FIXTURE_DIR="${PROJECT_SOURCE_DIR}/fixtures/design-ir")
    set_target_properties(vellum-design-pipeline-test PROPERTIES
        CXX_STANDARD 20
        CXX_STANDARD_REQUIRED ON
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
        BUILD_RPATH "$<TARGET_FILE_DIR:vellum-skia-dawn-provider>")
    add_test(NAME vellum.design-pipeline COMMAND vellum-design-pipeline-test)
endif()
