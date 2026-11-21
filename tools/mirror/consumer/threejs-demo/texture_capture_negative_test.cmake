if(NOT DEFINED DEMO_BIN OR NOT DEFINED CAPTURE_PATH OR NOT DEFINED PYTHON3_EXECUTABLE)
    message(FATAL_ERROR "DEMO_BIN, CAPTURE_PATH, and PYTHON3_EXECUTABLE are required")
endif()

file(REMOVE "${CAPTURE_PATH}")
execute_process(
    COMMAND "${DEMO_BIN}" --demo gltf-box-untextured --capture "${CAPTURE_PATH}"
    RESULT_VARIABLE _result OUTPUT_VARIABLE _stdout ERROR_VARIABLE _stderr
    TIMEOUT 60)
if(_result STREQUAL "77")
    message("SKIP: No real Dawn adapter available for texture negative control")
    return()
endif()
if(NOT _result STREQUAL "0")
    message(FATAL_ERROR "Untextured demo failed (${_result})\n${_stdout}\n${_stderr}")
endif()
execute_process(
    COMMAND "${PYTHON3_EXECUTABLE}" -I
        "${CMAKE_CURRENT_LIST_DIR}/texture_capture_test.py" "${CAPTURE_PATH}"
    RESULT_VARIABLE _texture_result OUTPUT_VARIABLE _texture_stdout
    ERROR_VARIABLE _texture_stderr)
if(_texture_result STREQUAL "0")
    message(FATAL_ERROR "Texture assertion unexpectedly accepted the untextured demo\n${_texture_stdout}\n${_texture_stderr}")
endif()
message(STATUS "Texture assertion rejects the untextured demo as expected")
