# The WGSL validator must exit 1 and print a positioned diagnostic for a broken
# shader.
#   cmake -DVALIDATOR=<exe> -DSHADER=<file.wgsl> -DEXPECT=<regex> -P CheckWgslRejects.cmake
# Exit 77 (no Null adapter) is reported as "WGSL was not validated" so the
# test registers a skip; a skip can never read as a pass.
foreach(_var VALIDATOR SHADER EXPECT)
    if(NOT ${_var})
        message(FATAL_ERROR "CheckWgslRejects: ${_var} is required")
    endif()
endforeach()
execute_process(
    COMMAND "${VALIDATOR}" "${SHADER}"
    RESULT_VARIABLE _rc
    OUTPUT_VARIABLE _out
    ERROR_VARIABLE _err)
if(_rc EQUAL 77)
    message(STATUS "WGSL was not validated: no Null adapter")
    return()
endif()
if(NOT _rc EQUAL 1)
    message(FATAL_ERROR "validator exited ${_rc}, expected 1 for a broken shader:\n${_out}${_err}")
endif()
if(NOT "${_err}" MATCHES "${EXPECT}")
    message(FATAL_ERROR "validator output lacks the positioned diagnostic '${EXPECT}':\n${_out}${_err}")
endif()
message(STATUS "rejected as expected")
