if(NOT VELLUM_SOURCE_DIR OR NOT VELLUM_BUILD_DIR)
    message(FATAL_ERROR "collision test paths were not supplied")
endif()

foreach(_kind IN ITEMS basename case)
    set(_work "${VELLUM_BUILD_DIR}/mirror-js-bytecode-collision-${_kind}")
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -S "${VELLUM_SOURCE_DIR}/tools/mirror/js-bytecode-collision"
                -B "${_work}" "-DVELLUM_SOURCE_DIR=${VELLUM_SOURCE_DIR}"
                "-DCOLLISION_KIND=${_kind}"
        RESULT_VARIABLE _result
        OUTPUT_VARIABLE _stdout
        ERROR_VARIABLE _stderr)
    if(_result EQUAL 0)
        message(FATAL_ERROR "${_kind} collision unexpectedly configured successfully")
    endif()
    set(_output "${_stdout}\n${_stderr}")
    if(NOT _output MATCHES "duplicate generated")
        message(FATAL_ERROR "${_kind} collision failed for an unexpected reason:\n${_output}")
    endif()
endforeach()
message(STATUS "duplicate source basenames, including case-only names, rejected as expected")
