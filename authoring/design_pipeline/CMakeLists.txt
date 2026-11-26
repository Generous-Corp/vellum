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

add_executable(vellum-source-adapter src/source_adapter_main.cpp)
target_compile_features(vellum-source-adapter PRIVATE cxx_std_20)
target_link_libraries(vellum-source-adapter PRIVATE Vellum::DesignPipeline)
set_target_properties(vellum-source-adapter PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    INSTALL_RPATH "@loader_path/../mirror/${CMAKE_INSTALL_LIBDIR};@loader_path/../${CMAKE_INSTALL_LIBDIR}")
install(TARGETS vellum-source-adapter RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

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

    # Keep one DesignIR fixture set across the native, bridge-JavaScript,
    # baked-C++, and SwiftUI lowerings.
    add_executable(vellum-emitter-conformance-gen
        tests/emitter_conformance_gen.cpp)
    target_include_directories(vellum-emitter-conformance-gen PRIVATE
        ${_vellum_mirror_public_includes}
        "${VELLUM_MIRROR_BUILD_INFO_DIR}"
        "${VELLUM_MIRROR_CHOC_ROOT}")
    target_link_libraries(vellum-emitter-conformance-gen PRIVATE Vellum::MirrorDesign_import)
    set(_vellum_conformance_dir "${CMAKE_CURRENT_BINARY_DIR}/emitter_conformance")
    set(_vellum_conformance_sources)
    set(_vellum_conformance_stems)
    foreach(_spec
            "form_controls_08|ir|fixtures/emitter-conformance/form-controls-08.ir.json"
            "spectr|ir|fixtures/emitter-conformance/spectr.ir.json"
            "kelvin|ir|fixtures/emitter-conformance/kelvin.ir.json"
            "keyed_list_18|ir|fixtures/emitter-conformance/keyed-list-18.ir.json"
            "fidelity_scene|figma-plugin|fixtures/emitter-conformance/fidelity-scene-no-display.pulp.json")
        string(REPLACE "|" ";" _parts "${_spec}")
        list(GET _parts 0 _stem)
        list(GET _parts 1 _kind)
        list(GET _parts 2 _input)
        list(APPEND _vellum_conformance_stems ${_stem})
        set(_outputs
            "${_vellum_conformance_dir}/${_stem}.hpp"
            "${_vellum_conformance_dir}/${_stem}.cpp"
            "${_vellum_conformance_dir}/${_stem}_entry.cpp")
        add_custom_command(OUTPUT ${_outputs}
            COMMAND vellum-emitter-conformance-gen ${_kind} "${PROJECT_SOURCE_DIR}/${_input}"
                    "vellum::test::conformance::${_stem}" "${_vellum_conformance_dir}" ${_stem}
            DEPENDS vellum-emitter-conformance-gen "${PROJECT_SOURCE_DIR}/${_input}"
            COMMENT "Generating the Vellum emitter conformance fixture ${_stem}"
            VERBATIM)
        list(APPEND _vellum_conformance_sources
            "${_vellum_conformance_dir}/${_stem}.cpp"
            "${_vellum_conformance_dir}/${_stem}_entry.cpp")
    endforeach()
    add_library(vellum-emitter-conformance-baked OBJECT ${_vellum_conformance_sources})
    # The generated sources include the mirrored view headers, which
    # Vellum::MirrorDesign_import carries only at their installed path; add the
    # build tree's include roots as vellum-design-pipeline does.
    target_include_directories(vellum-emitter-conformance-baked PUBLIC
        "${_vellum_conformance_dir}"
        ${_vellum_mirror_public_includes}
        "${VELLUM_MIRROR_BUILD_INFO_DIR}"
        "${VELLUM_MIRROR_CHOC_ROOT}")
    target_link_libraries(vellum-emitter-conformance-baked PUBLIC Vellum::MirrorDesign_import)
    add_executable(vellum-design-emitter-conformance
        tests/design_emitter_conformance_test.cpp)
    target_link_libraries(vellum-design-emitter-conformance
        PRIVATE vellum-emitter-conformance-baked Vellum::MirrorDesign_import)
    # The generated fixture set, so the test can prove it compares every
    # fixture the build generates and links.
    string(REPLACE ";" "," _vellum_conformance_stem_csv "${_vellum_conformance_stems}")
    target_compile_definitions(vellum-design-emitter-conformance PRIVATE
        VELLUM_REPO_ROOT="${PROJECT_SOURCE_DIR}"
        VELLUM_CONFORMANCE_STEMS="${_vellum_conformance_stem_csv}")
    set_target_properties(vellum-design-emitter-conformance PROPERTIES
        BUILD_RPATH "$<TARGET_FILE_DIR:vellum-skia-dawn-provider>")
    add_test(NAME vellum.design-emitter-conformance
        COMMAND vellum-design-emitter-conformance)
endif()

# `vellum import --mode baked --emit cpp|swiftui` runs this tool.
add_executable(vellum-design-emit src/design_emit_main.cpp src/design_emit_assets.cpp)
target_compile_features(vellum-design-emit PRIVATE cxx_std_20)
target_link_libraries(vellum-design-emit PRIVATE Vellum::DesignPipeline)
set_target_properties(vellum-design-emit PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    INSTALL_RPATH "@loader_path/../mirror/${CMAKE_INSTALL_LIBDIR};@loader_path/../${CMAKE_INSTALL_LIBDIR}")
install(TARGETS vellum-design-emit RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

if(VELLUM_BUILD_TESTS)
    # The CLI emit path, through the checkout CLI and the built emitter.
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME vellum.cli-design-emit
        COMMAND "${Python3_EXECUTABLE}" cli/tests/test_design_emit.py
        WORKING_DIRECTORY "${PROJECT_SOURCE_DIR}")
    set_tests_properties(vellum.cli-design-emit PROPERTIES
        ENVIRONMENT "VELLUM_DESIGN_EMIT=$<TARGET_FILE:vellum-design-emit>")

    # Compile and run what the emitter writes for a Figma plugin export, so a
    # generated pair that no longer builds against the framework fails here.
    set(_vellum_emit_check_dir "${CMAKE_CURRENT_BINARY_DIR}/design_emit_check")
    set(_vellum_emit_check_input
        "${PROJECT_SOURCE_DIR}/fixtures/emitter-conformance/fidelity-scene-no-display.pulp.json")
    add_custom_command(
        OUTPUT "${_vellum_emit_check_dir}/emitted_ui.cpp" "${_vellum_emit_check_dir}/emitted_ui.hpp"
        COMMAND vellum-design-emit --source-type figma-plugin --input "${_vellum_emit_check_input}"
                --emit cpp --output "${_vellum_emit_check_dir}/emitted_ui.cpp"
        DEPENDS vellum-design-emit "${_vellum_emit_check_input}"
        COMMENT "Emitting baked C++ through vellum-design-emit"
        VERBATIM)
    add_executable(vellum-design-emit-compile-test
        tests/design_emit_compile_test.cpp "${_vellum_emit_check_dir}/emitted_ui.cpp")
    target_include_directories(vellum-design-emit-compile-test PRIVATE
        "${_vellum_emit_check_dir}"
        ${_vellum_mirror_public_includes}
        "${VELLUM_MIRROR_BUILD_INFO_DIR}"
        "${VELLUM_MIRROR_CHOC_ROOT}")
    target_link_libraries(vellum-design-emit-compile-test PRIVATE Vellum::MirrorDesign_import)
    set_target_properties(vellum-design-emit-compile-test PROPERTIES
        CXX_STANDARD 20
        CXX_STANDARD_REQUIRED ON
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
        BUILD_RPATH "$<TARGET_FILE_DIR:vellum-skia-dawn-provider>")
    add_test(NAME vellum.design-emit-compile COMMAND vellum-design-emit-compile-test)
endif()
# `vellum design-debug`, kept inside this folder because it consumes the mirrored
# view, script and state units the way the pipeline does.
add_subdirectory(design_debug)
