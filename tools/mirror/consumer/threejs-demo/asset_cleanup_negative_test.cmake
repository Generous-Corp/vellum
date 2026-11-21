if(NOT DEFINED DEMO_BIN OR NOT DEFINED STAGING_ROOT OR NOT DEFINED ASSERT_SCRIPT)
    message(FATAL_ERROR "DEMO_BIN, STAGING_ROOT, and ASSERT_SCRIPT are required")
endif()

execute_process(
    COMMAND "${CMAKE_COMMAND}"
        "-DDEMO_BIN=${DEMO_BIN}"
        "-DSTAGING_ROOT=${STAGING_ROOT}"
        -P "${ASSERT_SCRIPT}"
    RESULT_VARIABLE _result OUTPUT_VARIABLE _stdout ERROR_VARIABLE _stderr)
if(_result STREQUAL "0")
    message(FATAL_ERROR "Cleanup assertion unexpectedly accepted the disabled-cleanup demo\n${_stdout}\n${_stderr}")
endif()
message(STATUS "Cleanup assertion rejects the disabled-cleanup demo as expected")
