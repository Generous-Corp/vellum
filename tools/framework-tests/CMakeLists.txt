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

vellum_add_framework_test(threejs-payload
    SOURCES threejs_payload_test.cpp
    UNITS Vellum::MirrorView_script)
target_compile_definitions(vellum-framework-threejs-payload-test PRIVATE
    VELLUM_THREEJS_DIR="${VELLUM_THREEJS_DIR}")

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
