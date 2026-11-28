if(NOT VELLUM_SOURCE_DIR OR NOT VELLUM_BUILD_DIR)
    message(FATAL_ERROR "cross-configure test paths were not supplied")
endif()

file(READ "${VELLUM_SOURCE_DIR}/CMakeLists.txt" _top_level)
string(FIND "${_top_level}" [=[if(_vellum_js_bytecode_generator AND TARGET "${_vellum_js_bytecode_generator}")]=]
    _guarded_properties)
if(_guarded_properties LESS 0)
    message(FATAL_ERROR
        "the build-tree bytecode generator properties are not guarded for cross-compiling")
endif()

set(_work "${VELLUM_BUILD_DIR}/mirror-js-bytecode-cross-configure")
execute_process(
    COMMAND "${CMAKE_COMMAND}" -S "${VELLUM_SOURCE_DIR}/tools/mirror/js-bytecode-cross"
            -B "${_work}"
            "-DVELLUM_SOURCE_DIR=${VELLUM_SOURCE_DIR}"
            -DCMAKE_SYSTEM_NAME=Generic
            "-DCMAKE_CXX_COMPILER=${VELLUM_CXX_COMPILER}"
    RESULT_VARIABLE _result
    OUTPUT_VARIABLE _stdout
    ERROR_VARIABLE _stderr)
if(NOT _result EQUAL 0)
    message(FATAL_ERROR "cross-compiling bytecode configure failed:\n${_stdout}\n${_stderr}")
endif()
if(NOT _stdout MATCHES "cross-compiling; scripts will compile from source")
    message(FATAL_ERROR "cross-configure did not exercise the placeholder-blob path:\n${_stdout}")
endif()
execute_process(
    COMMAND "${CMAKE_COMMAND}" --build "${_work}"
    RESULT_VARIABLE _build_result
    OUTPUT_VARIABLE _build_stdout
    ERROR_VARIABLE _build_stderr)
if(NOT _build_result EQUAL 0)
    message(FATAL_ERROR "cross-configure build failed:\n${_build_stdout}\n${_build_stderr}")
endif()
set(_blob "${_work}/cross_probe_vjsbc/bytecode_fixture.js.vjsbc")
file(WRITE "${_blob}" "native-bytecode")
execute_process(
    COMMAND "${CMAKE_COMMAND}" -S "${VELLUM_SOURCE_DIR}/tools/mirror/js-bytecode-cross"
            -B "${_work}"
            "-DVELLUM_SOURCE_DIR=${VELLUM_SOURCE_DIR}"
            -DCMAKE_SYSTEM_NAME=Generic
            "-DCMAKE_CXX_COMPILER=${VELLUM_CXX_COMPILER}"
    RESULT_VARIABLE _reconfigure_result
    OUTPUT_VARIABLE _reconfigure_stdout
    ERROR_VARIABLE _reconfigure_stderr)
if(NOT _reconfigure_result EQUAL 0)
    message(FATAL_ERROR "cross-configure reconfigure failed:\n${_reconfigure_stdout}\n${_reconfigure_stderr}")
endif()
execute_process(
    COMMAND "${CMAKE_COMMAND}" --build "${_work}"
    RESULT_VARIABLE _rebuild_result
    OUTPUT_VARIABLE _rebuild_stdout
    ERROR_VARIABLE _rebuild_stderr)
if(NOT _rebuild_result EQUAL 0)
    message(FATAL_ERROR "cross-configure rebuild failed:\n${_rebuild_stdout}\n${_rebuild_stderr}")
endif()
file(READ "${_blob}" _blob_contents)
if(NOT _blob_contents STREQUAL "-")
    message(FATAL_ERROR "cross-configure rebuild retained stale bytecode: '${_blob_contents}'")
endif()
message(STATUS "cross-compiling bytecode configure completed without a generator target")
