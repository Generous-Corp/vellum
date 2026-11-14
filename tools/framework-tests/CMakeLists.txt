# Tests of the transferred framework units, built in-tree against the mirror
# package libraries the SDK installs. The unit libraries carry only their
# installed include path, so the build tree's equivalents are added here: each
# unit's public include roots, the rendered build info and the pinned CHOC
# headers, the same set tools/mirror/VellumMirror.cmake installs.

set(_vellum_framework_test_includes
    ${_vellum_mirror_public_includes}
    "${VELLUM_MIRROR_BUILD_INFO_DIR}"
    "${VELLUM_MIRROR_CHOC_ROOT}")

# vellum_add_framework_test(<name> SOURCES <file>... UNITS <Vellum::Mirror target>...)
# Builds one test executable and registers it as `vellum.framework.<name>`
# with the `framework` label.
function(vellum_add_framework_test name)
    cmake_parse_arguments(PARSE_ARGV 1 _test "" "" "SOURCES;UNITS")
    if(NOT _test_SOURCES OR NOT _test_UNITS)
        message(FATAL_ERROR "vellum_add_framework_test(${name}) needs SOURCES and UNITS")
    endif()
    set(_target "vellum-framework-${name}-test")
    add_executable("${_target}" ${_test_SOURCES})
    target_include_directories("${_target}" PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}" ${_vellum_framework_test_includes})
    target_link_libraries("${_target}" PRIVATE ${_test_UNITS})
    set_target_properties("${_target}" PROPERTIES
        CXX_STANDARD 20
        CXX_STANDARD_REQUIRED ON
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
        BUILD_RPATH "$<TARGET_FILE_DIR:vellum-skia-dawn-provider>")
    add_test(NAME "vellum.framework.${name}" COMMAND "${_target}")
    set_tests_properties("vellum.framework.${name}" PROPERTIES LABELS framework)
    foreach(_source IN LISTS _test_SOURCES)
        get_filename_component(_source "${_source}" NAME)
        set_property(GLOBAL APPEND PROPERTY VELLUM_FRAMEWORK_TEST_SOURCES "${_source}")
    endforeach()
endfunction()

vellum_add_framework_test(widget-bridge
    SOURCES widget_bridge_test.cpp
    UNITS Vellum::MirrorView_script)

vellum_add_framework_test(param-model-binding
    SOURCES param_model_binding_test.cpp
    UNITS Vellum::MirrorView_core Vellum::Value)

vellum_add_framework_test(gpu-capabilities
    SOURCES gpu_capabilities_test.cpp
    UNITS Vellum::MirrorRender_2d)

# The WGSL validator behind vellum_add_wgsl_shaders(): it compiles each shader
# on Dawn's Null adapter. It compiles the render unit's WGSL helper and
# diagnostics itself and links the device layer directly, as the gpu-runtime
# device test does, so it never loads a second copy of the render unit. Nothing
# here installs it. It is a test-side executable, so its source carries the
# test file name and is recorded as registered for the check below.
add_executable(vellum-wgsl-validate wgsl_validate_test.cpp
    "${PROJECT_SOURCE_DIR}/framework/core/render/src/wgsl_compile.cpp"
    "${PROJECT_SOURCE_DIR}/framework/core/render/src/wgsl_diagnostics.cpp")
set_property(GLOBAL APPEND PROPERTY VELLUM_FRAMEWORK_TEST_SOURCES wgsl_validate_test.cpp)
target_include_directories(vellum-wgsl-validate PRIVATE
    "${PROJECT_SOURCE_DIR}/framework/core/render/src" "${PROJECT_SOURCE_DIR}/gpu-runtime/include"
    ${_vellum_framework_test_includes})
target_compile_definitions(vellum-wgsl-validate PRIVATE PULP_GPU_RUNTIME=1 PULP_HAS_DAWN=1 PULP_HAS_WEBGPU=1)
target_link_libraries(vellum-wgsl-validate PRIVATE VellumGpuRuntime::Device VellumGpuRuntime::Dawn)
set_target_properties(vellum-wgsl-validate PROPERTIES
    CXX_STANDARD 20
    CXX_STANDARD_REQUIRED ON
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-skia-dawn-provider>")

# Exit 77 means the host has no Null adapter, which ctest reports as a skip.
# The rejecting case passes only on the positioned diagnostic, so a skip can
# never read as a pass.
set(_vellum_wgsl_fixtures "${PROJECT_SOURCE_DIR}/fixtures/wgsl")
add_test(NAME vellum.framework.wgsl-validate-accepts-valid
    COMMAND vellum-wgsl-validate "${_vellum_wgsl_fixtures}/valid_fill.wgsl")
add_test(NAME vellum.framework.wgsl-validate-rejects-invalid
    COMMAND vellum-wgsl-validate "${_vellum_wgsl_fixtures}/invalid_identifier.wgsl")
add_test(NAME vellum.framework.wgsl-validate-creates-stamp-dir
    COMMAND "${CMAKE_COMMAND}"
        "-DVALIDATOR=$<TARGET_FILE:vellum-wgsl-validate>"
        "-DSHADER=${_vellum_wgsl_fixtures}/valid_fill.wgsl"
        "-DWORK_DIR=${CMAKE_CURRENT_BINARY_DIR}/wgsl-stamp-dir-test"
        -P "${PROJECT_SOURCE_DIR}/cmake/CheckWgslStampDir.cmake")
set_tests_properties(vellum.framework.wgsl-validate-accepts-valid PROPERTIES
    SKIP_RETURN_CODE 77 LABELS "framework;wgsl" TIMEOUT 60)
set_tests_properties(vellum.framework.wgsl-validate-rejects-invalid PROPERTIES
    SKIP_REGULAR_EXPRESSION "WGSL was not validated"
    PASS_REGULAR_EXPRESSION "invalid_identifier\\.wgsl:4:[0-9]+: error: .*colour"
    LABELS "framework;wgsl" TIMEOUT 60)
set_tests_properties(vellum.framework.wgsl-validate-creates-stamp-dir PROPERTIES
    SKIP_REGULAR_EXPRESSION "stamp written: skipped"
    LABELS "framework;wgsl" TIMEOUT 60)

# Every test file here must be registered above. A test file that no
# vellum_add_framework_test() names would build nothing and run nothing while
# the lane stays green, so configure refuses it.
file(GLOB _vellum_framework_test_files RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}"
    CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/*_test.cpp" "${CMAKE_CURRENT_SOURCE_DIR}/*_test.mm")
get_property(_vellum_framework_registered GLOBAL PROPERTY VELLUM_FRAMEWORK_TEST_SOURCES)
set(_vellum_framework_unregistered)
foreach(_file IN LISTS _vellum_framework_test_files)
    if(NOT _file IN_LIST _vellum_framework_registered)
        list(APPEND _vellum_framework_unregistered "${_file}")
    endif()
endforeach()
if(_vellum_framework_unregistered)
    message(FATAL_ERROR "tools/framework-tests: no vellum_add_framework_test() registers "
        "${_vellum_framework_unregistered}")
endif()
