# Installs this build of gpu-runtime into a scratch prefix, then configures,
# builds and runs a consumer that finds it with find_package().
#
#   cmake -DBUILD_DIR=<gpu-runtime build> -DSOURCE_DIR=<gpu-runtime source>
#         -DWORK_DIR=<scratch> -DVERSION=<x.y.z> -DGENERATOR=<generator>
#         -P install_consumer_test.cmake
cmake_minimum_required(VERSION 3.24)

file(REMOVE_RECURSE "${WORK_DIR}")
set(_prefix "${WORK_DIR}/prefix")
set(_consumer "${WORK_DIR}/consumer")

include("${SOURCE_DIR}/cmake/GpuRuntimeRendererLock.cmake")
gpu_runtime_read_renderer_lock(PLATFORM macos-arm64 PREFIX source
    LOCK "${SOURCE_DIR}/renderer-lock.json")

function(_run)
    execute_process(COMMAND ${ARGN} RESULT_VARIABLE _result
        OUTPUT_VARIABLE _output ERROR_VARIABLE _output)
    if(NOT _result EQUAL 0)
        message(FATAL_ERROR "failed (${_result}): ${ARGN}\n${_output}")
    endif()
endfunction()

_run("${CMAKE_COMMAND}" --install "${BUILD_DIR}" --prefix "${_prefix}")
_run("${CMAKE_COMMAND}" -S "${SOURCE_DIR}/tests/install-consumer" -B "${_consumer}"
    -G "${GENERATOR}"
    "-DCMAKE_PREFIX_PATH=${_prefix}"
    "-DEXPECTED_VERSION=${VERSION}"
    "-DEXPECTED_SHA256=${source_SHA256}"
    "-DCONSUMER_SOURCE=${SOURCE_DIR}/tests/device_api_consumer.cpp")
_run("${CMAKE_COMMAND}" --build "${_consumer}")
_run("${_consumer}/gpu-runtime-install-consumer")
message(STATUS "gpu-runtime install consumer: ok")
