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
    # What the component redistributes must ship with its notices: RmlUi's
    # MIT notice always, and the FreeType credit, FTL election and FTL text
    # whenever FreeType's archive is in the package.
    set(_docs "${_prefix}/share/doc/VellumRmlUi")
    set(_required_notice_lines
        "Copyright (c) 2019-2025 The RmlUi Team, and contributors"
        "MIT License")
    if(NOT EXISTS "${_docs}/RmlUi-LICENSE.txt")
        message(FATAL_ERROR "the Native component did not install RmlUi's licence file")
    endif()
    file(GLOB_RECURSE _freetype_archives "${_prefix}/*/libfreetype*" "${_prefix}/*/freetype*.lib")
    # The glob is cross-checked against the build's font option, so a renamed
    # archive cannot make the FreeType notice check pass by finding nothing.
    if(VELLUM_RMLUI_FONTS AND NOT _freetype_archives)
        message(FATAL_ERROR "fonts are on but the Native package carries no FreeType archive")
    endif()
    if(_freetype_archives)
        list(APPEND _required_notice_lines
            "Portions of this software are copyright © 2024 The FreeType Project"
            "under the FreeType License (FTL)"
            "the GPLv2 alternative")
        if(NOT EXISTS "${_docs}/FreeType-FTL.TXT")
            message(FATAL_ERROR "the Native package ships FreeType without FreeType-FTL.TXT")
        endif()
    endif()
    if(NOT EXISTS "${_docs}/NOTICE-Native.md")
        message(FATAL_ERROR "the Native component did not install NOTICE-Native.md")
    endif()
    file(READ "${_docs}/NOTICE-Native.md" _notice)
    foreach(_line IN LISTS _required_notice_lines)
        string(FIND "${_notice}" "${_line}" _at)
        if(_at EQUAL -1)
            message(FATAL_ERROR "NOTICE-Native.md is missing a required entry: ${_line}")
        endif()
    endforeach()
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
