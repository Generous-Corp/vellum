if(NOT DEFINED VELLUM_SOURCE_DIR OR
   NOT DEFINED VELLUM_BUILD_DIR OR
   NOT DEFINED VELLUM_SKIA_DIR)
    message(FATAL_ERROR "nested install-consumer paths were not supplied")
endif()

set(_build "${VELLUM_BUILD_DIR}/nested-install-layout-test")
file(REMOVE_RECURSE "${_build}")

execute_process(
    COMMAND "${CMAKE_COMMAND}"
        -S "${VELLUM_SOURCE_DIR}"
        -B "${_build}"
        -DCMAKE_BUILD_TYPE=Release
        -DCMAKE_INSTALL_LIBDIR=lib/vellum
        -DCMAKE_INSTALL_DATADIR=data
        -DVELLUM_REQUIRE_GPU=ON
        "-DVELLUM_SKIA_DIR=${VELLUM_SKIA_DIR}"
    RESULT_VARIABLE _configure_result
    OUTPUT_VARIABLE _configure_stdout
    ERROR_VARIABLE _configure_stderr)
if(NOT _configure_result EQUAL 0)
    message(FATAL_ERROR
        "nested-layout configure failed (${_configure_result})\n"
        "${_configure_stdout}\n${_configure_stderr}")
endif()

set(_config_args)
if(DEFINED TEST_CONFIG AND NOT TEST_CONFIG STREQUAL "")
    list(APPEND _config_args --config "${TEST_CONFIG}")
endif()
execute_process(
    COMMAND "${CMAKE_COMMAND}" --build "${_build}" ${_config_args} --parallel 8
    RESULT_VARIABLE _build_result
    OUTPUT_VARIABLE _build_stdout
    ERROR_VARIABLE _build_stderr)
if(NOT _build_result EQUAL 0)
    message(FATAL_ERROR
        "nested-layout build failed (${_build_result})\n"
        "${_build_stdout}\n${_build_stderr}")
endif()

execute_process(
    COMMAND "${CMAKE_CTEST_COMMAND}"
        --test-dir "${_build}"
        --output-on-failure
        -R "^vellum.install-consumer$"
    RESULT_VARIABLE _test_result
    OUTPUT_VARIABLE _test_stdout
    ERROR_VARIABLE _test_stderr)
if(NOT _test_result EQUAL 0)
    message(FATAL_ERROR
        "nested-layout installed consumer failed (${_test_result})\n"
        "${_test_stdout}\n${_test_stderr}")
endif()
