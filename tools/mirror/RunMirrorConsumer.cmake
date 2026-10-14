# Installs this build, then builds and runs the mirror consumer against the
# installed package alone, proves the Phase-6 debts inert there, and proves
# that one image cannot take two Skia/Dawn providers.
foreach(_required VELLUM_SOURCE_DIR VELLUM_BUILD_DIR VELLUM_MIRROR_DEPS_DIR VELLUM_MIRROR_ORIGIN Python3_EXECUTABLE)
    if(NOT DEFINED ${_required})
        message(FATAL_ERROR "mirror consumer test: ${_required} was not supplied")
    endif()
endforeach()

set(_work "${VELLUM_BUILD_DIR}/mirror-consumer-test")
set(_prefix "${_work}/prefix")
file(REMOVE_RECURSE "${_work}")

function(_run label)
    execute_process(COMMAND ${ARGN}
        RESULT_VARIABLE _result OUTPUT_VARIABLE _stdout ERROR_VARIABLE _stderr)
    if(NOT _result EQUAL 0)
        message(FATAL_ERROR "${label} failed (${_result})\n${_stdout}\n${_stderr}")
    endif()
    message(STATUS "${label}: ok")
    set(_last_stdout "${_stdout}" PARENT_SCOPE)
endfunction()

_run("install" "${CMAKE_COMMAND}" --install "${VELLUM_BUILD_DIR}" --prefix "${_prefix}")
file(GLOB_RECURSE _package_files "${_prefix}/*.cmake")
foreach(_package_file IN LISTS _package_files)
    file(READ "${_package_file}" _contents)
    foreach(_forbidden "${VELLUM_SOURCE_DIR}" "${VELLUM_BUILD_DIR}")
        string(FIND "${_contents}" "${_forbidden}" _found)
        if(NOT _found EQUAL -1)
            message(FATAL_ERROR "${_package_file} names a build-host path: ${_forbidden}")
        endif()
    endforeach()
endforeach()

# The libraries ship to other machines: no build-host path may be inside.
file(GLOB _libraries "${_prefix}/mirror/lib/*.dylib")
if(NOT _libraries)
    message(FATAL_ERROR "the install carries no mirror libraries")
endif()
foreach(_library IN LISTS _libraries)
    foreach(_forbidden "${VELLUM_SOURCE_DIR}" "${VELLUM_BUILD_DIR}" "${VELLUM_MIRROR_DEPS_DIR}")
        file(STRINGS "${_library}" _hits REGEX "${_forbidden}" LIMIT_COUNT 1)
        if(_hits)
            message(FATAL_ERROR "${_library} embeds the build-host path ${_forbidden}")
        endif()
    endforeach()
endforeach()
message(STATUS "mirror libraries carry no build-host path: ok")

set(_generator)
find_program(_ninja ninja)
if(_ninja)
    set(_generator -G Ninja)
endif()
_run("consumer configure" "${CMAKE_COMMAND}"
    -S "${VELLUM_SOURCE_DIR}/tools/mirror/consumer" -B "${_work}/consumer"
    ${_generator} -DCMAKE_BUILD_TYPE=Release
    "-DCMAKE_PREFIX_PATH=${_prefix}"
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE)
_run("consumer build" "${CMAKE_COMMAND}" --build "${_work}/consumer")
_run("consumer run" "${_work}/consumer/vellum-mirror-consumer" "${VELLUM_MIRROR_ORIGIN}")
_run("Phase-6 debt inertness" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/tools/mirror/verify_mirror_inertness.py"
    --prefix "${_prefix}" --consumer-build "${_work}/consumer"
    --image "${_work}/consumer/vellum-mirror-consumer"
    --output "${_work}/inertness.json")

execute_process(COMMAND "${CMAKE_COMMAND}"
    -S "${VELLUM_SOURCE_DIR}/tools/mirror/provider-conflict" -B "${_work}/conflict"
    ${_generator} -DCMAKE_BUILD_TYPE=Release "-DCMAKE_PREFIX_PATH=${_prefix}"
    RESULT_VARIABLE _conflict OUTPUT_VARIABLE _conflict_stdout ERROR_VARIABLE _conflict_stderr)
if(_conflict EQUAL 0)
    message(FATAL_ERROR "a consumer linking two Skia/Dawn providers configured successfully")
endif()
string(FIND "${_conflict_stderr}" "VELLUM_SKIA_DAWN_PROVIDER" _named)
if(_named EQUAL -1)
    message(FATAL_ERROR "the two-provider consumer failed for another reason:\n${_conflict_stderr}")
endif()
message(STATUS "two-provider consumer refused: ok")
