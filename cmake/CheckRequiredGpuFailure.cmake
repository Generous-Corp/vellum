# A required-GPU startup failure must be a failure, not CTest's skip code 77.
# This helper checks the exact exit code and diagnostic instead of WILL_FAIL,
# which would also accept the old skip behavior.
#   cmake -DTEST_EXECUTABLE=<exe> -DTEST_ENVIRONMENT=<key=value>
#         -DEXPECTED_OUTPUT=<text> -P CheckRequiredGpuFailure.cmake
foreach(_var TEST_EXECUTABLE TEST_ENVIRONMENT EXPECTED_OUTPUT)
    if(NOT DEFINED ${_var} OR "${${_var}}" STREQUAL "")
        message(FATAL_ERROR "CheckRequiredGpuFailure: ${_var} is required")
    endif()
endforeach()

execute_process(
    COMMAND "${CMAKE_COMMAND}" -E env "${TEST_ENVIRONMENT}" "${TEST_EXECUTABLE}"
    RESULT_VARIABLE _rc
    OUTPUT_VARIABLE _out
    ERROR_VARIABLE _err)
if(NOT _rc EQUAL 1)
    message(FATAL_ERROR
        "required-GPU failure exited ${_rc}, expected 1; output:\n${_out}${_err}")
endif()
if(NOT "${_out}${_err}" MATCHES "${EXPECTED_OUTPUT}")
    message(FATAL_ERROR
        "required-GPU failure lacked '${EXPECTED_OUTPUT}'; output:\n${_out}${_err}")
endif()
message(STATUS "required-GPU failure returned 1 with the expected diagnostic")
