if(NOT DEFINED DEMO_BIN OR NOT DEFINED STAGING_ROOT)
    message(FATAL_ERROR "DEMO_BIN and STAGING_ROOT are required")
endif()

file(REMOVE_RECURSE "${STAGING_ROOT}")
file(MAKE_DIRECTORY "${STAGING_ROOT}")
execute_process(
    COMMAND "${DEMO_BIN}" --gltf-asset-smoke "${STAGING_ROOT}"
    RESULT_VARIABLE _result OUTPUT_VARIABLE _stdout ERROR_VARIABLE _stderr
    TIMEOUT 20)
if(NOT _result STREQUAL "0")
    message(FATAL_ERROR "Asset smoke failed (${_result})\n${_stdout}\n${_stderr}")
endif()
file(GLOB _leftovers LIST_DIRECTORIES true "${STAGING_ROOT}/gltf-box.*")
if(_leftovers)
    message(FATAL_ERROR "Asset smoke left per-run fixtures: ${_leftovers}")
endif()
message(STATUS "Asset smoke removed its per-run fixture")
