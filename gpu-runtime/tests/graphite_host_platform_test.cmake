# The Graphite host qualification checks the Skia library against the renderer
# lock row of the platform being configured. Each case runs in a child script so
# a configure error is observable.
#   cmake -DMODULE=<GpuRuntimeGraphiteHost.cmake> -P graphite_host_platform_test.cmake
if(NOT MODULE)
    message(FATAL_ERROR "MODULE is required")
endif()

if(CASE_SYSTEM)
    include("${MODULE}")
    gpu_runtime_graphite_lock_platform("${CASE_SYSTEM}" "${CASE_PROCESSOR}" "" picked)
    message(STATUS "picked=${picked}")
    return()
endif()

function(run_case system processor expect_ok expected)
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -DMODULE=${MODULE}
            -DCASE_SYSTEM=${system} -DCASE_PROCESSOR=${processor}
            -P "${CMAKE_CURRENT_LIST_FILE}"
        RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
    if(expect_ok)
        if(NOT rc EQUAL 0 OR NOT out MATCHES "picked=${expected}\n")
            message(FATAL_ERROR "${system}/${processor}: expected row ${expected}, got rc=${rc} ${out}${err}")
        endif()
    else()
        if(rc EQUAL 0 OR NOT err MATCHES "no row for ${system}/${processor}")
            message(FATAL_ERROR "${system}/${processor}: expected a configure error naming the platform, got rc=${rc} ${out}${err}")
        endif()
    endif()
endfunction()

run_case(Darwin arm64 TRUE macos-arm64)
run_case(Linux x86_64 TRUE linux-x64)
run_case(Windows AMD64 TRUE windows-x64)
# An Intel Mac has no row of its own, so it is refused rather than compared
# against another platform's digest.
run_case(Darwin x86_64 FALSE "")
run_case(FreeBSD amd64 FALSE "")
message(STATUS "graphite host platform selection ok")
