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

# The view-script unit runs the sealed V8 provider (VELLUM_ENABLE_V8). Without
# V8 the same test is the control: it must still run to completion and fail
# exactly its seven V8 cases while its QuickJS case passes, which proves the
# checks see a build that does not link V8.
vellum_add_framework_test(v8-engine
    SOURCES v8_engine_test.cpp
    UNITS Vellum::MirrorView_script Vellum::MirrorCanvas)
if(VELLUM_ENABLE_V8)
    target_compile_definitions(vellum-framework-v8-engine-test PRIVATE
        VELLUM_TEST_EXPECTED_V8_VERSION="${VELLUM_V8_VERSION}")
else()
    set_tests_properties(vellum.framework.v8-engine PROPERTIES
        PASS_REGULAR_EXPRESSION "PASS quickjs_is_not_reported_as_v8\n.*8 cases, 7 failed")
endif()

# The scripted WebGPU binding against a headless Dawn device (the Null
# adapter). The provider keeps GpuSurface::create_dawn hidden, so the test links
# the device library itself, which brings the one Dawn this executable uses. It
# needs the GPU backend; the test exits 77 and is skipped when no adapter is
# available.
if(TARGET vellum-dawn-headers AND TARGET VellumGpuRuntime::Device)
    vellum_add_framework_test(webgpu-error-scopes
        SOURCES webgpu_error_scopes_test.cpp
        UNITS Vellum::MirrorView_script Vellum::MirrorRender_2d Vellum::DawnHeaders
               VellumGpuRuntime::Device)
    # The test builds a headless device through the surface's own header.
    target_include_directories(vellum-framework-webgpu-error-scopes-test PRIVATE
        "${PROJECT_SOURCE_DIR}/gpu-runtime/include")
    set_tests_properties(vellum.framework.webgpu-error-scopes PROPERTIES SKIP_RETURN_CODE 77)
else()
    set_property(GLOBAL APPEND PROPERTY VELLUM_FRAMEWORK_TEST_SOURCES
        "webgpu_error_scopes_test.cpp")
endif()

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
# The compile definitions the render unit's sources are built with, so the
# helper sources compiled here see the same device layer.
target_compile_definitions(vellum-wgsl-validate PRIVATE
    "$<TARGET_PROPERTY:vellum-mirror-render-2d,INTERFACE_COMPILE_DEFINITIONS>")
target_link_libraries(vellum-wgsl-validate PRIVATE VellumGpuRuntime::Device VellumGpuRuntime::Dawn)
set_target_properties(vellum-wgsl-validate PROPERTIES
    CXX_STANDARD 20
    CXX_STANDARD_REQUIRED ON
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-skia-dawn-provider>")

# Exit 77 means the host has no Null adapter, which ctest reports as a skip.
# The rejecting case passes only on exit 1 with the positioned diagnostic, so
# a skip can never read as a pass.
set(_vellum_wgsl_fixtures "${PROJECT_SOURCE_DIR}/fixtures/wgsl")
add_test(NAME vellum.framework.wgsl-validate-accepts-valid
    COMMAND vellum-wgsl-validate "${_vellum_wgsl_fixtures}/valid_fill.wgsl")
add_test(NAME vellum.framework.wgsl-validate-rejects-invalid
    COMMAND "${CMAKE_COMMAND}"
        "-DVALIDATOR=$<TARGET_FILE:vellum-wgsl-validate>"
        "-DSHADER=${_vellum_wgsl_fixtures}/invalid_identifier.wgsl"
        "-DEXPECT=invalid_identifier[.]wgsl:4:[0-9]+: error: .*colour"
        -P "${PROJECT_SOURCE_DIR}/cmake/CheckWgslRejects.cmake")
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
    LABELS "framework;wgsl" TIMEOUT 60)
set_tests_properties(vellum.framework.wgsl-validate-creates-stamp-dir PROPERTIES
    SKIP_REGULAR_EXPRESSION "stamp written: skipped"
    LABELS "framework;wgsl" TIMEOUT 60)

vellum_add_framework_test(widget-bridge-param-model
    SOURCES widget_bridge_param_model_test.cpp
    UNITS Vellum::MirrorView_script Vellum::Value)

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
