# Installs the VellumRmlUi component into a fresh prefix, then configures,
# builds and runs a consumer against it with find_package(VellumRmlUi).
if(NOT DEFINED VELLUM_RMLUI_BUILD_DIR OR NOT DEFINED VELLUM_RMLUI_SOURCE_DIR OR
   NOT DEFINED VELLUM_RMLUI_INSTALL_LIBDIR)
    message(FATAL_ERROR "install-consumer paths were not supplied")
endif()

# VELLUM_RMLUI_CONSUMER=native installs the Native component as well and
# builds tests/native_consumer, which uses RmlUi's own API.
set(_native FALSE)
if(DEFINED VELLUM_RMLUI_CONSUMER AND VELLUM_RMLUI_CONSUMER STREQUAL "native")
    set(_native TRUE)
endif()
set(_work "${VELLUM_RMLUI_BUILD_DIR}/install-consumer-test")
if(_native)
    set(_work "${VELLUM_RMLUI_BUILD_DIR}/install-consumer-native-test")
endif()
set(_prefix "${_work}/prefix")
set(_consumer_build "${_work}/build")
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
endfunction()

_run("${CMAKE_COMMAND}" --install "${VELLUM_RMLUI_BUILD_DIR}"
     --prefix "${_prefix}" --component VellumRmlUi ${_config_args})

# RmlUi is private to the shared library: none of its headers or archives,
# and no FreeType, may be part of the Core package.
file(GLOB_RECURSE _leaked
    "${_prefix}/include/RmlUi/*" "${_prefix}/include/freetype2/*"
    "${_prefix}/*/librmlui*" "${_prefix}/*/libfreetype*")
if(_leaked)
    message(FATAL_ERROR "the VellumRmlUi package installed RmlUi or FreeType files: ${_leaked}")
endif()
if(_native)
    _run("${CMAKE_COMMAND}" --install "${VELLUM_RMLUI_BUILD_DIR}"
         --prefix "${_prefix}" --component VellumRmlUiNative ${_config_args})
    # Native carries RmlUi: its headers and archive must now be present.
    if(NOT EXISTS "${_prefix}/include/RmlUi/Core.h")
        message(FATAL_ERROR "the Native component did not install RmlUi's headers")
    endif()
endif()
set(_package_dir "${_prefix}/${VELLUM_RMLUI_INSTALL_LIBDIR}/cmake/VellumRmlUi")
file(GLOB _package_files "${_package_dir}/*.cmake")
foreach(_package_file IN LISTS _package_files)
    file(READ "${_package_file}" _contents)
    string(FIND "${_contents}" "${VELLUM_RMLUI_SOURCE_DIR}" _source_reference)
    if(NOT _source_reference EQUAL -1)
        message(FATAL_ERROR "installed package references the source tree: ${_package_file}")
    endif()
endforeach()

set(_consumer_source "${VELLUM_RMLUI_SOURCE_DIR}/tests/consumer")
set(_consumer_name vellum-rmlui-consumer)
if(_native)
    set(_consumer_source "${VELLUM_RMLUI_SOURCE_DIR}/tests/native_consumer")
    set(_consumer_name vellum-rmlui-native-consumer)
endif()
_run("${CMAKE_COMMAND}" -S "${_consumer_source}" -B "${_consumer_build}"
     "-DVellumRmlUi_DIR=${_package_dir}" "-DCMAKE_BUILD_TYPE=Release")
_run("${CMAKE_COMMAND}" --build "${_consumer_build}" ${_config_args})

set(_candidates
    "${_consumer_build}/${_consumer_name}"
    "${_consumer_build}/${_consumer_name}.exe")
if(DEFINED TEST_CONFIG AND NOT TEST_CONFIG STREQUAL "")
    list(APPEND _candidates "${_consumer_build}/${TEST_CONFIG}/${_consumer_name}.exe")
endif()
foreach(_candidate IN LISTS _candidates)
    if(EXISTS "${_candidate}")
        set(_consumer "${_candidate}")
        break()
    endif()
endforeach()
if(NOT _consumer)
    message(FATAL_ERROR "installed-package consumer was not built")
endif()
_run("${_consumer}")
