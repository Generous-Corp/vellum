if(NOT VELLUM_SOURCE_DIR OR NOT VELLUM_BUILD_DIR)
    message(FATAL_ERROR "cross-configure test paths were not supplied")
endif()

file(READ "${VELLUM_SOURCE_DIR}/CMakeLists.txt" _top_level)
string(REGEX MATCH "if\\(TARGET vellum-js-bytecode\\)[^\n]*\n[ \t]+set_target_properties\\(vellum-js-bytecode"
    _guarded_properties "${_top_level}")
if(NOT _guarded_properties)
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
message(STATUS "cross-compiling bytecode configure completed without a generator target")
