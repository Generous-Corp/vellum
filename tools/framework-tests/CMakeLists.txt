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
    # A tracing build compiles every mirrored object with the trace switch, so a
    # test that includes framework headers must agree with it.
    if(TARGET vellum-tracing)
        target_link_libraries("${_target}" PRIVATE vellum-tracing)
    endif()
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

vellum_add_framework_test(react-materialization
    SOURCES react_materialization_test.cpp
    UNITS Vellum::MirrorView_script)

vellum_add_framework_test(param-model-binding
    SOURCES param_model_binding_test.cpp
    UNITS Vellum::MirrorView_core Vellum::Value)

vellum_add_framework_test(knob-fine-drag
    SOURCES knob_fine_drag_test.cpp
    UNITS Vellum::MirrorView_core)

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

# The Three.js bridge tests run the pinned payload (VELLUM_THREEJS_DIR) on V8,
# which they need: QuickJS cannot parse the builds. Module loading needs no
# device. The scene test checks behaviour on any adapter, falling back to
# Dawn's Null adapter; the render test reads pixels back and exits 77 (skipped)
# without a rendering adapter.
set(_vellum_threejs_tests threejs_modules_test.cpp threejs_scene_test.cpp threejs_render_test.cpp)
if(VELLUM_ENABLE_V8)
    vellum_add_framework_test(threejs-modules
        SOURCES threejs_modules_test.cpp
        UNITS Vellum::MirrorView_script)
    target_compile_definitions(vellum-framework-threejs-modules-test PRIVATE
        VELLUM_TEST_THREEJS_DIR="${VELLUM_THREEJS_DIR}")
    if(TARGET vellum-dawn-headers AND TARGET VellumGpuRuntime::Device)
        foreach(_vellum_threejs_name IN ITEMS scene render)
            set(_vellum_threejs_target "vellum-framework-threejs-${_vellum_threejs_name}-test")
            vellum_add_framework_test(threejs-${_vellum_threejs_name}
                SOURCES threejs_${_vellum_threejs_name}_test.cpp
                UNITS Vellum::MirrorView_script Vellum::MirrorRender_2d Vellum::DawnHeaders
                       VellumGpuRuntime::Device)
            target_include_directories("${_vellum_threejs_target}" PRIVATE
                "${PROJECT_SOURCE_DIR}/gpu-runtime/include")
            target_compile_definitions("${_vellum_threejs_target}" PRIVATE
                VELLUM_TEST_THREEJS_DIR="${VELLUM_THREEJS_DIR}")
            set_tests_properties(vellum.framework.threejs-${_vellum_threejs_name} PROPERTIES
                SKIP_RETURN_CODE 77)
        endforeach()
    else()
        set_property(GLOBAL APPEND PROPERTY VELLUM_FRAMEWORK_TEST_SOURCES
            threejs_scene_test.cpp threejs_render_test.cpp)
    endif()
else()
    set_property(GLOBAL APPEND PROPERTY VELLUM_FRAMEWORK_TEST_SOURCES ${_vellum_threejs_tests})
endif()

vellum_add_framework_test(gpu-capabilities
    SOURCES gpu_capabilities_test.cpp
    UNITS Vellum::MirrorRender_2d)

# Several draws in one scripted WebGPU render pass, read back through Skia on a
# real Dawn adapter. The test links the device library for the headless
# surface, as the other GPU tests here do, and exits 77 (skipped) without a
# rendering adapter.
if(TARGET vellum-dawn-headers AND TARGET VellumGpuRuntime::Device)
    vellum_add_framework_test(webgpu-multi-draw
        SOURCES webgpu_multi_draw_test.cpp
        UNITS Vellum::MirrorView_script Vellum::MirrorRender_2d Vellum::DawnHeaders
               VellumGpuRuntime::Device)
    target_include_directories(vellum-framework-webgpu-multi-draw-test PRIVATE
        "${PROJECT_SOURCE_DIR}/gpu-runtime/include")
    set_tests_properties(vellum.framework.webgpu-multi-draw PROPERTIES SKIP_RETURN_CODE 77)
else()
    set_property(GLOBAL APPEND PROPERTY VELLUM_FRAMEWORK_TEST_SOURCES webgpu_multi_draw_test.cpp)
endif()

# The frame-level render pass manager is header-only; any render unit gives the
# test the include root.
vellum_add_framework_test(render-pass
    SOURCES render_pass_test.cpp
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
if(TARGET vellum-tracing)
    target_link_libraries(vellum-wgsl-validate PRIVATE vellum-tracing)
endif()
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

# One native Perfetto session holding the framework's and the renderer's tracks
# (VELLUM_ENABLE_TRACING). It renders on a real surface and exits 77, a skip,
# when the host has no adapter.
if(VELLUM_ENABLE_TRACING AND TARGET vellum-scene-capture-harness)
    vellum_add_framework_test(native-trace
        SOURCES native_trace_test.cpp
        UNITS Vellum::MirrorFoundation Vellum::MirrorRender_2d vellum-scene-capture-harness)
    target_include_directories(vellum-framework-native-trace-test PRIVATE
        "${PROJECT_SOURCE_DIR}/graphics/tests")
    set_tests_properties(vellum.framework.native-trace PROPERTIES SKIP_RETURN_CODE 77)
else()
    set_property(GLOBAL APPEND PROPERTY VELLUM_FRAMEWORK_TEST_SOURCES
        "native_trace_test.cpp")
endif()

vellum_add_framework_test(widget-bridge-param-model
    SOURCES widget_bridge_param_model_test.cpp
    UNITS Vellum::MirrorView_script Vellum::Value)

# Linux host tests: the SDL window host on an X server and the AT-SPI tree over
# a D-Bus session. They need the framework units compiled for Linux, so they
# register only there; their sources count as registered everywhere. Run them
# under `xvfb-run -a dbus-run-session -- ctest -L linux-host`: without a display
# or a session bus a case exits 77 and ctest reports a skip. Each has a control
# that must also pass (an unexported path, and a run with the resize and the
# click left out).
set(_vellum_linux_host_sources linux_atspi_tree_test.cpp linux_sdl_window_x11_test.cpp)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND TARGET Vellum::MirrorView_core)
    find_package(X11 REQUIRED COMPONENTS Xtst)
    vellum_add_framework_test(linux-atspi-tree
        SOURCES linux_atspi_tree_test.cpp
        UNITS Vellum::MirrorView_core)
    vellum_add_framework_test(linux-sdl-window-x11
        SOURCES linux_sdl_window_x11_test.cpp
        UNITS Vellum::MirrorView_core)
    target_link_libraries(vellum-framework-linux-sdl-window-x11-test PRIVATE X11::X11 X11::Xtst)
    add_test(NAME vellum.framework.linux-sdl-window-x11-control
        COMMAND vellum-framework-linux-sdl-window-x11-test --control)
    # The AT-SPI test's client call is bounded: a call that never returns ends the
    # process with a named failure instead of hanging. This control runs such a
    # call and passes only when the bound fires.
    add_test(NAME vellum.framework.linux-atspi-tree-hang-control
        COMMAND vellum-framework-linux-atspi-tree-test --control-hang)
    set_tests_properties(vellum.framework.linux-atspi-tree-hang-control PROPERTIES
        PASS_REGULAR_EXPRESSION "bounded client call expired" LABELS "framework;linux-host" TIMEOUT 90)
    foreach(_linux_host_test IN ITEMS linux-atspi-tree linux-sdl-window-x11
            linux-sdl-window-x11-control)
        set_tests_properties(vellum.framework.${_linux_host_test} PROPERTIES
            SKIP_RETURN_CODE 77 LABELS "framework;linux-host" TIMEOUT 90)
    endforeach()
else()
    set_property(GLOBAL APPEND PROPERTY VELLUM_FRAMEWORK_TEST_SOURCES ${_vellum_linux_host_sources})
endif()

vellum_add_framework_test(scripted-ui-param-model
    SOURCES scripted_ui_param_model_test.cpp
    UNITS Vellum::MirrorView_script Vellum::Value)

vellum_add_framework_test(host-param-surface-model
    SOURCES host_param_surface_model_test.cpp
    UNITS Vellum::MirrorView_core Vellum::Value)

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
