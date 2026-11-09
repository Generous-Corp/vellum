if(NOT DEFINED VELLUM_BUILD_DIR OR NOT DEFINED VELLUM_SWIFT_PROOF_PREFIX)
    message(FATAL_ERROR "build directory and prefix were not supplied")
endif()
file(REMOVE_RECURSE "${VELLUM_SWIFT_PROOF_PREFIX}")
set(_config_args)
if(DEFINED TEST_CONFIG AND NOT TEST_CONFIG STREQUAL "")
    list(APPEND _config_args --config "${TEST_CONFIG}")
endif()
execute_process(
    COMMAND "${CMAKE_COMMAND}" --install "${VELLUM_BUILD_DIR}"
            --prefix "${VELLUM_SWIFT_PROOF_PREFIX}" ${_config_args}
    RESULT_VARIABLE _install_result
    OUTPUT_VARIABLE _install_stdout
    ERROR_VARIABLE _install_stderr)
if(NOT _install_result EQUAL 0)
    message(FATAL_ERROR
        "Vellum SDK install for the Swift host proof failed (${_install_result})\n"
        "${_install_stdout}\n${_install_stderr}")
endif()
