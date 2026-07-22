if(NOT DEFINED VELLUM_SOURCE_DIR OR
   NOT DEFINED VELLUM_BUILD_DIR OR
   NOT DEFINED VELLUM_INSTALL_CONSUMER_SOURCE OR
   NOT DEFINED VELLUM_INSTALL_LIBDIR)
    message(FATAL_ERROR "install-consumer paths were not supplied")
endif()

set(_work "${VELLUM_BUILD_DIR}/install-consumer-test")
set(_prefix "${_work}/prefix")
set(_consumer_build "${_work}/build")
file(REMOVE_RECURSE "${_work}")

set(_config_args)
if(DEFINED TEST_CONFIG AND NOT TEST_CONFIG STREQUAL "")
    list(APPEND _config_args --config "${TEST_CONFIG}")
endif()

execute_process(
    COMMAND "${CMAKE_COMMAND}" --install "${VELLUM_BUILD_DIR}"
            --prefix "${_prefix}" ${_config_args}
    RESULT_VARIABLE _install_result
    OUTPUT_VARIABLE _install_stdout
    ERROR_VARIABLE _install_stderr)
if(NOT _install_result EQUAL 0)
    message(FATAL_ERROR
        "Vellum SDK install failed (${_install_result})\n"
        "${_install_stdout}\n${_install_stderr}")
endif()

set(_package_dir "${_prefix}/${VELLUM_INSTALL_LIBDIR}/cmake/Vellum")
file(GLOB _package_files "${_package_dir}/*.cmake")
foreach(_package_file IN LISTS _package_files)
    file(READ "${_package_file}" _package_contents)
    string(FIND "${_package_contents}" "${VELLUM_SOURCE_DIR}" _source_reference)
    if(NOT _source_reference EQUAL -1)
        message(FATAL_ERROR
            "installed CMake package references the Vellum source tree: ${_package_file}")
    endif()
endforeach()

execute_process(
    COMMAND "${CMAKE_COMMAND}"
            -S "${VELLUM_INSTALL_CONSUMER_SOURCE}"
            -B "${_consumer_build}"
            "-DVellum_DIR=${_package_dir}"
            "-DCMAKE_BUILD_TYPE=Release"
    RESULT_VARIABLE _configure_result
    OUTPUT_VARIABLE _configure_stdout
    ERROR_VARIABLE _configure_stderr)
if(NOT _configure_result EQUAL 0)
    message(FATAL_ERROR
        "installed-SDK consumer configure failed (${_configure_result})\n"
        "${_configure_stdout}\n${_configure_stderr}")
endif()

execute_process(
    COMMAND "${CMAKE_COMMAND}" --build "${_consumer_build}" ${_config_args}
    RESULT_VARIABLE _build_result
    OUTPUT_VARIABLE _build_stdout
    ERROR_VARIABLE _build_stderr)
if(NOT _build_result EQUAL 0)
    message(FATAL_ERROR
        "installed-SDK consumer build failed (${_build_result})\n"
        "${_build_stdout}\n${_build_stderr}")
endif()

set(_ctest_args --test-dir "${_consumer_build}" --output-on-failure)
if(DEFINED TEST_CONFIG AND NOT TEST_CONFIG STREQUAL "")
    list(APPEND _ctest_args -C "${TEST_CONFIG}")
endif()
execute_process(
    COMMAND "${CMAKE_CTEST_COMMAND}" ${_ctest_args}
    RESULT_VARIABLE _test_result
    OUTPUT_VARIABLE _test_stdout
    ERROR_VARIABLE _test_stderr)
if(NOT _test_result EQUAL 0)
    message(FATAL_ERROR
        "installed-SDK consumer test failed (${_test_result})\n"
        "${_test_stdout}\n${_test_stderr}")
endif()
