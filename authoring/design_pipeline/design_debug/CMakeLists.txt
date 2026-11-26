# `vellum design-debug`: replays a design chat turn headlessly and writes
# before/after/diff captures and a report. The runner's widget bridge and
# offscreen capture are macOS-only, like the Pulp tool it replaces.
if(NOT TARGET Vellum::MirrorView_script OR NOT APPLE)
    return()
endif()

set(_vellum_design_debug_includes
    ${_vellum_mirror_public_includes}
    "${VELLUM_MIRROR_BUILD_INFO_DIR}"
    "${VELLUM_MIRROR_CHOC_ROOT}")

add_executable(vellum-design-debug src/design_debug_main.cpp)
target_compile_features(vellum-design-debug PRIVATE cxx_std_20)
target_include_directories(vellum-design-debug PRIVATE ${_vellum_design_debug_includes})
target_link_libraries(vellum-design-debug PRIVATE Vellum::MirrorView_script)
set_target_properties(vellum-design-debug PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    INSTALL_RPATH "@loader_path/../mirror/${CMAKE_INSTALL_LIBDIR};@loader_path/../${CMAKE_INSTALL_LIBDIR}")
install(TARGETS vellum-design-debug RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

if(VELLUM_BUILD_TESTS)
    # The runner's helpers and a full recorded-response run against a
    # synthetic script. No test calls a model: every run passes --response-file.
    add_executable(vellum-design-debug-contracts-test tests/design_debug_contracts_test.cpp)
    target_include_directories(vellum-design-debug-contracts-test PRIVATE
        ${_vellum_design_debug_includes}
        "${CMAKE_CURRENT_SOURCE_DIR}/../tests")
    target_link_libraries(vellum-design-debug-contracts-test PRIVATE Vellum::MirrorView_script)
    set_target_properties(vellum-design-debug-contracts-test PROPERTIES
        CXX_STANDARD 20
        CXX_STANDARD_REQUIRED ON
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
        BUILD_RPATH "$<TARGET_FILE_DIR:vellum-skia-dawn-provider>")
    add_test(NAME vellum.design-debug-contracts COMMAND vellum-design-debug-contracts-test)
    set_tests_properties(vellum.design-debug-contracts PROPERTIES
        ENVIRONMENT "VELLUM_AI_CLI=sh -c 'exit 97'")
endif()
