# Installs the VellumRmlUi component into one prefix and the VellumRmlUiNative
# package into another, as the Vellum SDK and the RmlUi SDK ship them, then
# configures, builds and runs tests/native_package_consumer against both.
#
# The two prefixes must be disjoint: the Native package carries Native, Metal
# and RmlUi only, and nothing of VellumRmlUi's, and the VellumRmlUi package
# carries nothing of RmlUi's. A control configures the consumer with the
# Native prefix alone, which must fail to find VellumRmlUi, so a consumer that
# passes is one that really found Core across the two prefixes.
foreach(_required VELLUM_RMLUI_BUILD_DIR VELLUM_RMLUI_SOURCE_DIR VELLUM_RMLUI_INSTALL_LIBDIR)
    if(NOT DEFINED ${_required})
        message(FATAL_ERROR "native-package consumer: ${_required} was not supplied")
    endif()
endforeach()

set(_work "${VELLUM_RMLUI_BUILD_DIR}/install-consumer-native-package-test")
set(_main "${_work}/vellum-sdk")
set(_native "${_work}/rmlui-sdk")
file(REMOVE_RECURSE "${_work}")

set(_config_args)
if(DEFINED TEST_CONFIG AND NOT TEST_CONFIG STREQUAL "")
    list(APPEND _config_args --config "${TEST_CONFIG}")
endif()

function(_run)
    execute_process(COMMAND ${ARGN}
        RESULT_VARIABLE _result OUTPUT_VARIABLE _stdout ERROR_VARIABLE _stderr)
    if(NOT _result EQUAL 0)
        message(FATAL_ERROR "command failed (${_result}): ${ARGN}\n${_stdout}\n${_stderr}")
    endif()
    message(STATUS "${_stdout}")
endfunction()

_run("${CMAKE_COMMAND}" --install "${VELLUM_RMLUI_BUILD_DIR}"
     --prefix "${_main}" --component VellumRmlUi ${_config_args})
_run("${CMAKE_COMMAND}" --install "${VELLUM_RMLUI_BUILD_DIR}"
     --prefix "${_native}" --component VellumRmlUiNative ${_config_args})

# What each side must and must not carry.
set(_libdir "${VELLUM_RMLUI_INSTALL_LIBDIR}")
foreach(_file
        "${_libdir}/cmake/VellumRmlUiNative/VellumRmlUiNativeConfig.cmake"
        "${_libdir}/cmake/VellumRmlUiNative/VellumRmlUiNativeConfigVersion.cmake"
        "${_libdir}/cmake/VellumRmlUiNative/VellumRmlUiNativeTargets.cmake"
        "${_libdir}/cmake/VellumRmlUiNative/VellumRmlUiMetalTargets.cmake"
        "${_libdir}/cmake/VellumRmlUiNative/VellumRmlUiRmlUiTargets.cmake"
        "include/RmlUi/Core.h"
        "include/vellum/rmlui/native.hpp"
        "include/vellum/rmlui/metal_render_interface.hpp"
        "share/doc/VellumRmlUiNative/RmlUi-LICENSE.txt"
        "share/doc/VellumRmlUiNative/NOTICE-Native.md")
    if(NOT EXISTS "${_native}/${_file}")
        message(FATAL_ERROR "the VellumRmlUiNative package is missing ${_file}")
    endif()
endforeach()
if(EXISTS "${_native}/${_libdir}/cmake/VellumRmlUi")
    message(FATAL_ERROR "the VellumRmlUiNative package installed files into VellumRmlUi's package directory")
endif()
file(GLOB_RECURSE _core_in_native "${_native}/*/libvellum-rmlui-core*")
if(_core_in_native)
    message(FATAL_ERROR "the VellumRmlUiNative package carries VellumRmlUi::Core: ${_core_in_native}")
endif()
file(GLOB_RECURSE _rmlui_in_main
    "${_main}/include/RmlUi/*" "${_main}/include/freetype2/*"
    "${_main}/*/librmlui*" "${_main}/*/libfreetype*")
if(_rmlui_in_main)
    message(FATAL_ERROR "the VellumRmlUi package installed RmlUi or FreeType files: ${_rmlui_in_main}")
endif()
if(EXISTS "${_main}/include/vellum/rmlui/native.hpp")
    message(FATAL_ERROR "the VellumRmlUi package installed the Native-only header native.hpp")
endif()

# Nothing is shipped twice: no relative path exists in both prefixes.
file(GLOB_RECURSE _main_files RELATIVE "${_main}" "${_main}/*")
file(GLOB_RECURSE _native_files RELATIVE "${_native}" "${_native}/*")
set(_both ${_main_files})
list(FILTER _both INCLUDE REGEX ".")
foreach(_file IN LISTS _main_files)
    if(NOT _file IN_LIST _native_files)
        list(REMOVE_ITEM _both "${_file}")
    endif()
endforeach()
if(_both)
    message(FATAL_ERROR "files installed by both packages: ${_both}")
endif()
list(LENGTH _main_files _main_count)
list(LENGTH _native_files _native_count)
if(_main_count EQUAL 0 OR _native_count EQUAL 0)
    message(FATAL_ERROR "an install produced no files (${_main_count} and ${_native_count})")
endif()

# On macOS every installed library object must be built for at most the
# deployment target the package records.
if(CMAKE_HOST_APPLE)
    set(VELLUM_RMLUI_CHECK_PREFIX "${_native}")
    set(VELLUM_RMLUI_CHECK_PACKAGE_DIR "${_native}/${_libdir}/cmake/VellumRmlUiNative")
    include("${CMAKE_CURRENT_LIST_DIR}/CheckDeploymentTarget.cmake")
endif()
file(GLOB _package_files "${_native}/${_libdir}/cmake/VellumRmlUiNative/*.cmake")
foreach(_package_file IN LISTS _package_files)
    file(READ "${_package_file}" _contents)
    string(FIND "${_contents}" "${VELLUM_RMLUI_SOURCE_DIR}" _source_reference)
    if(NOT _source_reference EQUAL -1)
        message(FATAL_ERROR "installed package references the source tree: ${_package_file}")
    endif()
endforeach()

set(_consumer_source "${CMAKE_CURRENT_LIST_DIR}/native_package_consumer")

# Control: the Native prefix alone cannot provide Core.
execute_process(COMMAND "${CMAKE_COMMAND}" -S "${_consumer_source}" -B "${_work}/control"
        "-DCMAKE_PREFIX_PATH=${_native}"
    RESULT_VARIABLE _control_result OUTPUT_VARIABLE _control_out ERROR_VARIABLE _control_err)
if(_control_result EQUAL 0)
    message(FATAL_ERROR "the consumer configured without the VellumRmlUi prefix; the cross-prefix lookup proves nothing")
endif()
string(FIND "${_control_out}${_control_err}" "VellumRmlUi" _named)
if(_named EQUAL -1)
    message(FATAL_ERROR "the control failed for a reason other than the missing VellumRmlUi package:\n${_control_err}")
endif()

_run("${CMAKE_COMMAND}" -S "${_consumer_source}" -B "${_work}/build"
     "-DCMAKE_PREFIX_PATH=${_native}\;${_main}")
_run("${CMAKE_COMMAND}" --build "${_work}/build" ${_config_args})
foreach(_consumer vellum-rmlui-native-metal-consumer vellum-rmlui-core-consumer)
    file(GLOB_RECURSE _binary "${_work}/build/*${_consumer}")
    list(FILTER _binary EXCLUDE REGEX "\\.(o|d|dir)$")
    if(NOT _binary)
        message(FATAL_ERROR "the consumer build produced no ${_consumer}")
    endif()
    list(GET _binary 0 _binary)
    _run("${_binary}")
endforeach()
