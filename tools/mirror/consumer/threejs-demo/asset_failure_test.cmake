# A regular file as an ancestor makes the staging directory unwritable even
# for a privileged runner.
execute_process(
    COMMAND "${DEMO_BIN}" --gltf-asset-smoke "${DEMO_BIN}/assets"
    RESULT_VARIABLE _result ERROR_VARIABLE _stderr
    TIMEOUT 20)
if(NOT _result STREQUAL "1" OR NOT _stderr MATCHES "Cannot create glTF asset directory")
    message(FATAL_ERROR "Expected a clean asset staging failure, got ${_result}\n${_stderr}")
endif()
