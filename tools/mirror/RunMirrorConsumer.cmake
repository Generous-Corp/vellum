# Installs this build, then builds and runs the mirror consumer against the
# installed package alone, proves the Phase-6 debts inert there, and proves
# that one image cannot take two Skia/Dawn providers.
foreach(_required VELLUM_SOURCE_DIR VELLUM_BUILD_DIR VELLUM_MIRROR_DEPS_DIR VELLUM_MIRROR_ORIGIN VELLUM_INSTALL_LIBDIR Python3_EXECUTABLE)
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
_run("debt probe" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/tools/mirror/verify_mirror_inertness.py"
    --prefix "${_prefix}" --install-libdir "${VELLUM_INSTALL_LIBDIR}"
    --write-debt-probe "${_work}/debt_probe.cpp")
_run("consumer configure" "${CMAKE_COMMAND}"
    -S "${VELLUM_SOURCE_DIR}/tools/mirror/consumer" -B "${_work}/consumer"
    ${_generator} -DCMAKE_BUILD_TYPE=Release
    "-DVELLUM_MIRROR_DEBT_PROBE=${_work}/debt_probe.cpp"
    "-DCMAKE_PREFIX_PATH=${_prefix}"
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE)
_run("consumer build" "${CMAKE_COMMAND}" --build "${_work}/consumer")
_run("consumer run" "${_work}/consumer/vellum-mirror-consumer" "${VELLUM_MIRROR_ORIGIN}")

# Keep a raw linker consumer as a separate contract check. It names only the
# installed MirrorCanvas dylib as a link input, so CMake target transitivity
# cannot mask a missing export. AppleClang's linker resolves an
# LC_REEXPORT_DYLIB's @rpath dependency differently across toolchain
# generations: older ld versions do not use an executable's runtime rpath to
# locate an indirect re-export while linking. Map the installed provider only
# for link-time resolution; MirrorCanvas remains the sole explicit input and
# the resulting image still carries the re-export relationship.
file(MAKE_DIRECTORY "${_work}/direct")
file(WRITE "${_work}/direct/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumMirrorDirectConsumer LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(VELLUM_MIRROR_PREFIX "" CACHE PATH "Installed Vellum prefix")
set(VELLUM_INSTALL_LIBDIR "lib" CACHE STRING "Installed Vellum library directory")
add_library(vellum-mirror-direct SHARED IMPORTED)
set_target_properties(vellum-mirror-direct PROPERTIES
    IMPORTED_LOCATION "${VELLUM_MIRROR_PREFIX}/mirror/lib/libvellum-mirror-canvas.dylib"
    INTERFACE_INCLUDE_DIRECTORIES "${VELLUM_MIRROR_PREFIX}/mirror/include")
add_executable(vellum-mirror-direct-consumer direct_main.cpp)
target_link_libraries(vellum-mirror-direct-consumer PRIVATE vellum-mirror-direct)
# This emits -L for the provider at link time while retaining only
# libvellum-mirror-canvas.dylib as the explicit link input.
target_link_directories(vellum-mirror-direct-consumer PRIVATE
    "${VELLUM_MIRROR_PREFIX}/${VELLUM_INSTALL_LIBDIR}")
target_link_options(vellum-mirror-direct-consumer PRIVATE
    "LINKER:-dylib_file,@rpath/libvellum-skia-dawn-provider.dylib:${VELLUM_MIRROR_PREFIX}/${VELLUM_INSTALL_LIBDIR}/libvellum-skia-dawn-provider.dylib")
set_target_properties(vellum-mirror-direct-consumer PROPERTIES
    BUILD_RPATH "${VELLUM_MIRROR_PREFIX}/mirror/lib;${VELLUM_MIRROR_PREFIX}/${VELLUM_INSTALL_LIBDIR}")
]=])
file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/direct_main.cpp"
    DESTINATION "${_work}/direct")
_run("direct consumer configure" "${CMAKE_COMMAND}"
    -S "${_work}/direct" -B "${_work}/direct-build"
    ${_generator} -DCMAKE_BUILD_TYPE=Release
    "-DVELLUM_MIRROR_PREFIX=${_prefix}"
    "-DVELLUM_INSTALL_LIBDIR=${VELLUM_INSTALL_LIBDIR}")
_run("direct consumer build" "${CMAKE_COMMAND}" --build "${_work}/direct-build")
_run("direct consumer run" "${_work}/direct-build/vellum-mirror-direct-consumer")

# The link-time mapping above must not turn the frontend into an ordinary
# provider dependency. Keep the ABI assertion explicit so a future change
# cannot make this fixture pass by hiding the re-export behind target
# transitivity or a second provider definition.
find_program(_otool otool REQUIRED)
execute_process(
    COMMAND "${_otool}" -L "${_prefix}/mirror/lib/libvellum-mirror-canvas.dylib"
    RESULT_VARIABLE _mirror_otool_result
    OUTPUT_VARIABLE _mirror_otool
    ERROR_VARIABLE _mirror_otool_error)
if(NOT _mirror_otool_result EQUAL 0 OR
   NOT _mirror_otool MATCHES "libvellum-skia-dawn-provider\\.dylib" OR
   NOT _mirror_otool MATCHES "reexport")
    message(FATAL_ERROR
        "MirrorCanvas does not carry the provider re-export contract:\n"
        "${_mirror_otool}\n${_mirror_otool_error}")
endif()
message(STATUS "MirrorCanvas provider re-export metadata: ok")

# The legacy GPU frontend has the same raw-link contract. Build a consumer
# against only its installed dylib; running it would require a live GPU.
file(MAKE_DIRECTORY "${_work}/direct-gpu")
file(WRITE "${_work}/direct-gpu/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumGpuDirectConsumer LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(VELLUM_MIRROR_PREFIX "" CACHE PATH "Installed Vellum prefix")
set(VELLUM_INSTALL_LIBDIR "" CACHE STRING "Installed Vellum library directory")
add_library(vellum-gpu-direct SHARED IMPORTED)
set_target_properties(vellum-gpu-direct PROPERTIES
    IMPORTED_LOCATION "${VELLUM_MIRROR_PREFIX}/${VELLUM_INSTALL_LIBDIR}/libvellum-gpu.dylib"
    INTERFACE_INCLUDE_DIRECTORIES "${VELLUM_MIRROR_PREFIX}/include")
add_executable(vellum-gpu-direct-consumer direct_gpu_main.cpp)
target_link_libraries(vellum-gpu-direct-consumer PRIVATE vellum-gpu-direct)
target_link_directories(vellum-gpu-direct-consumer PRIVATE
    "${VELLUM_MIRROR_PREFIX}/${VELLUM_INSTALL_LIBDIR}")
target_link_options(vellum-gpu-direct-consumer PRIVATE
    "LINKER:-dylib_file,@rpath/libvellum-skia-dawn-provider.dylib:${VELLUM_MIRROR_PREFIX}/${VELLUM_INSTALL_LIBDIR}/libvellum-skia-dawn-provider.dylib")
]=])
file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/direct_gpu_main.cpp"
    DESTINATION "${_work}/direct-gpu")
_run("direct GPU consumer configure" "${CMAKE_COMMAND}"
    -S "${_work}/direct-gpu" -B "${_work}/direct-gpu-build"
    ${_generator} -DCMAKE_BUILD_TYPE=Release
    "-DVELLUM_MIRROR_PREFIX=${_prefix}"
    "-DVELLUM_INSTALL_LIBDIR=${VELLUM_INSTALL_LIBDIR}")
_run("direct GPU consumer build" "${CMAKE_COMMAND}" --build "${_work}/direct-gpu-build")

_run("shared provider symbol ownership" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/graphics/tests/test_dawn_provider_topology.py"
    "${_work}/consumer/vellum-mirror-consumer"
    "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-gpu.dylib"
    "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-skia-dawn-provider.dylib"
    "${_prefix}/mirror/lib/libvellum-mirror-canvas.dylib")
_run("Phase-6 debt inertness" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/tools/mirror/verify_mirror_inertness.py"
    --prefix "${_prefix}" --install-libdir "${VELLUM_INSTALL_LIBDIR}"
    --consumer-build "${_work}/consumer"
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
