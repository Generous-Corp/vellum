# Installs this build, then builds and runs the mirror consumer against the
# installed package alone, proves the Phase-6 debts inert there, and proves
# that one image cannot take two Skia/Dawn providers.
foreach(_required VELLUM_SOURCE_DIR VELLUM_BUILD_DIR VELLUM_MIRROR_DEPS_DIR VELLUM_MIRROR_ORIGIN VELLUM_INSTALL_LIBDIR Python3_EXECUTABLE)
    if(NOT DEFINED ${_required})
        message(FATAL_ERROR "mirror consumer test: ${_required} was not supplied")
    endif()
endforeach()

# A view unit makes the archive-backed symbol-floor proof mandatory.  Keep the
# value explicit at the test boundary so CI can supply the provenance-pinned
# Pulp artifact and local runs fail closed instead of silently using a stale
# checkout output.
if(DEFINED VELLUM_VIEW_AUDIO_ARCHIVE AND
   NOT VELLUM_VIEW_AUDIO_ARCHIVE STREQUAL "" AND
   NOT EXISTS "${VELLUM_VIEW_AUDIO_ARCHIVE}")
    message(FATAL_ERROR "mirror consumer test: VELLUM_VIEW_AUDIO_ARCHIVE does not exist")
endif()
set(_view_audio_args)
if(DEFINED VELLUM_VIEW_AUDIO_ARCHIVE AND
   NOT VELLUM_VIEW_AUDIO_ARCHIVE STREQUAL "")
    set(_view_audio_args --view-audio-archive "${VELLUM_VIEW_AUDIO_ARCHIVE}")
endif()

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

# Every installed library must load on the minimum macOS it declares.
file(GLOB _installed_libraries "${_prefix}/${VELLUM_INSTALL_LIBDIR}/*.dylib")
_run("libc++ availability" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/tools/mirror/check_libcxx_availability.py"
    ${_libraries} ${_installed_libraries})
# One gpu-runtime coordinator and one Dawn proc table across every installed
# image, the mirror's units included (mirror_package links the in-tree
# gpu-runtime only into vellum-skia-dawn-provider).
_run("gpu-runtime census" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/tools/mirror/gpu_runtime_census.py"
    ${_libraries} ${_installed_libraries})

# framework/'s units share the vellum namespace with native code: no symbol
# anywhere under vellum:: may be defined both by a mirror library and by a native
# archive (a silent ODR violation across images). Both sides must be seen
# (exit 3 otherwise), and a planted shared name must be caught.
file(GLOB _native_archives "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-*.a")
if(NOT _native_archives)
    message(FATAL_ERROR "the install carries no native libvellum-*.a archive to intersect")
endif()
set(_intersection "${Python3_EXECUTABLE}" "${VELLUM_SOURCE_DIR}/tools/mirror/mirror_symbol_intersection.py"
    --old pulp --new vellum --namespace vellum
    --mirror ${_libraries} --native ${_native_archives})
_run("framework and native symbol intersection" ${_intersection})
execute_process(COMMAND ${_intersection} --plant RESULT_VARIABLE _planted OUTPUT_QUIET ERROR_QUIET)
if(NOT _planted EQUAL 1)
    message(FATAL_ERROR "symbol intersection control: a planted shared name gave ${_planted}, not 1")
endif()
message(STATUS "symbol intersection control: ok")

set(_generator)
find_program(_ninja ninja)
if(_ninja)
    set(_generator -G Ninja)
endif()

# GPU device types are not part of the installed surface (gpu_surface_guard.py).
# Installed headers equal their mirror sources byte for byte, except that each
# choke header carries one guard line; a consumer that includes one fails with
# the guard's message, and the consumers above and below still build.
set(_gpu_guard_manifest "${_prefix}/mirror/share/provenance/gpu-guarded-headers.txt")
if(NOT EXISTS "${_gpu_guard_manifest}")
    message(FATAL_ERROR "the install carries no GPU guard manifest")
endif()
file(STRINGS "${_gpu_guard_manifest}" _gpu_guarded)
set(_gpu_guard_args)
if(_gpu_guarded)
    set(_gpu_guard_args --require-guard)
endif()
_run("installed headers match the mirror" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/tools/mirror/gpu_surface_guard.py" check
    --include-dir "${_prefix}/mirror/include" --repo "${VELLUM_SOURCE_DIR}" ${_gpu_guard_args})
if(_gpu_guarded)
    # The installed units' include root: vellum/ after the framework transfer,
    # pulp/ before it.
    set(_units_root "pulp")
    if(EXISTS "${_prefix}/mirror/include/vellum/view/window_host.hpp")
        set(_units_root "vellum")
    endif()
    foreach(_case "${_units_root}/view/window_host.hpp" "${_units_root}/render/gpu_surface.hpp")
        string(MAKE_C_IDENTIFIER "${_case}" _case_key)
        set(_case_dir "${_work}/gpu-guard-${_case_key}")
        file(MAKE_DIRECTORY "${_case_dir}")
        file(WRITE "${_case_dir}/consumer.cpp" "#include <${_case}>\nint main() { return 0; }\n")
        file(WRITE "${_case_dir}/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumMirrorGpuGuardConsumer LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Vellum CONFIG REQUIRED)
add_library(gpu-guard-consumer OBJECT consumer.cpp)
target_link_libraries(gpu-guard-consumer PRIVATE Vellum::MirrorCanvas)
]=])
        _run("GPU guard consumer configure (${_case})" "${CMAKE_COMMAND}"
            -S "${_case_dir}" -B "${_case_dir}/build" ${_generator} -DCMAKE_BUILD_TYPE=Release
            "-DCMAKE_PREFIX_PATH=${_prefix}"
            -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE
            -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE)
        execute_process(COMMAND "${CMAKE_COMMAND}" --build "${_case_dir}/build"
            RESULT_VARIABLE _guard_result OUTPUT_VARIABLE _guard_out ERROR_VARIABLE _guard_err)
        if(_guard_result EQUAL 0)
            message(FATAL_ERROR "an installed consumer including <${_case}> compiled; the GPU guard is not in force")
        endif()
        string(FIND "${_guard_out}${_guard_err}" "not part of the installed mirror's supported surface" _guard_hit)
        if(_guard_hit EQUAL -1)
            message(FATAL_ERROR "an installed consumer including <${_case}> failed, "
                "but not with the GPU guard's message:\n${_guard_out}\n${_guard_err}")
        endif()
        message(STATUS "GPU guard refuses <${_case}>: ok")
    endforeach()
endif()
_run("debt probe" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/tools/mirror/verify_mirror_inertness.py"
    --prefix "${_prefix}" --install-libdir "${VELLUM_INSTALL_LIBDIR}"
    ${_view_audio_args}
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

# Keep a raw installed-consumer contract check separate from CMake target
# transitivity. The consumer force-loads only MirrorCanvas, then resolves and
# calls Color::from_hsv through its re-exported provider. This validates the
# installed LC_REEXPORT_DYLIB path on older AppleClang/dyld combinations that
# cannot statically bind a C++ symbol through an indirect re-export.
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
# Retain only libvellum-mirror-canvas.dylib as the explicit link input; the
# installed prefix also supplies the runtime search paths below.
target_link_directories(vellum-mirror-direct-consumer PRIVATE
    "${VELLUM_MIRROR_PREFIX}/${VELLUM_INSTALL_LIBDIR}")
target_link_options(vellum-mirror-direct-consumer PRIVATE
    "LINKER:-needed_library,${VELLUM_MIRROR_PREFIX}/mirror/lib/libvellum-mirror-canvas.dylib")
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

# Keep the ABI assertion explicit so a future change cannot make this fixture
# pass by hiding the re-export behind target transitivity or a second provider
# definition.
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
target_include_directories(vellum-gpu-direct-consumer PRIVATE
    "${VELLUM_MIRROR_PREFIX}/include/vellum/dawn")
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

# A local mirror-consumer run may not have a live GPU even when the installed
# dylib was built with the GPU provider. Require an explicit opt-in for the
# runtime probe so an unavailable device is reported as such rather than being
# mistaken for a passing consumer test. The hosted GPU proof sets this variable
# around ctest; an opted-in runtime failure remains fatal through _run().
set(_run_direct_gpu FALSE)
if(DEFINED ENV{VELLUM_MIRROR_LIVE_GPU})
    set(_live_gpu "$ENV{VELLUM_MIRROR_LIVE_GPU}")
    if(_live_gpu MATCHES "^(1|ON|TRUE|YES)$")
        set(_run_direct_gpu TRUE)
    elseif(NOT _live_gpu MATCHES "^(0|OFF|FALSE|NO)?$")
        message(FATAL_ERROR
            "VELLUM_MIRROR_LIVE_GPU must be one of 1/0, ON/OFF, TRUE/FALSE, or YES/NO")
    endif()
endif()
if(_run_direct_gpu)
    _run("direct GPU consumer run" "${_work}/direct-gpu-build/vellum-gpu-direct-consumer")
else()
    message(STATUS
        "direct GPU consumer run: unavailable (set VELLUM_MIRROR_LIVE_GPU=1 on a live GPU host)")
endif()

# Exercise the installed frontend paths in a fresh process and census the
# images dyld actually mapped. Loading the raw GPU frontend beside the
# intermediate MirrorCanvas frontend must still resolve to one provider image.
file(MAKE_DIRECTORY "${_work}/runtime-census")
file(WRITE "${_work}/runtime-census/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumRuntimeImageCensus LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(VELLUM_MIRROR_PREFIX "" CACHE PATH "Installed Vellum prefix")
add_executable(vellum-runtime-image-census runtime_image_census_main.cpp)
set_target_properties(vellum-runtime-image-census PROPERTIES
    BUILD_RPATH "${VELLUM_MIRROR_PREFIX}/lib;${VELLUM_MIRROR_PREFIX}/mirror/lib")
]=])
file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/runtime_image_census_main.cpp"
    DESTINATION "${_work}/runtime-census")
_run("runtime image census configure" "${CMAKE_COMMAND}"
    -S "${_work}/runtime-census" -B "${_work}/runtime-census-build"
    ${_generator} -DCMAKE_BUILD_TYPE=Release
    "-DVELLUM_MIRROR_PREFIX=${_prefix}")
_run("runtime image census build" "${CMAKE_COMMAND}"
    --build "${_work}/runtime-census-build")
_run("runtime image census"
    "${_work}/runtime-census-build/vellum-runtime-image-census"
    "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-skia-dawn-provider.dylib"
    "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-gpu.dylib"
    "${_prefix}/mirror/lib/libvellum-mirror-canvas.dylib")

execute_process(
    COMMAND "${_otool}" -L "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-gpu.dylib"
    RESULT_VARIABLE _gpu_otool_result
    OUTPUT_VARIABLE _gpu_otool
    ERROR_VARIABLE _gpu_otool_error)
if(NOT _gpu_otool_result EQUAL 0 OR
   NOT _gpu_otool MATCHES "libvellum-skia-dawn-provider\\.dylib" OR
   NOT _gpu_otool MATCHES "reexport")
    message(FATAL_ERROR
        "GPU frontend does not carry the provider re-export contract:\n"
        "${_gpu_otool}\n${_gpu_otool_error}")
endif()
message(STATUS "GPU provider re-export metadata: ok")

# Exercise the image-boundary case that CMake's compatible-interface property
# cannot see. Copy the real pinned provider to a distinct install name, load
# both images into one process, then run the symbol census with the duplicate
# as an expected negative control. This proves the guard catches a provider
# introduced through an intermediate/raw dylib path.
find_program(_install_name_tool install_name_tool REQUIRED)
set(_cross_image_dir "${_work}/cross-image")
file(MAKE_DIRECTORY "${_cross_image_dir}")
set(_provider "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-skia-dawn-provider.dylib")
set(_duplicate "${_cross_image_dir}/libvellum-skia-dawn-provider-duplicate.dylib")
file(COPY "${_provider}" DESTINATION "${_cross_image_dir}")
file(RENAME
    "${_cross_image_dir}/libvellum-skia-dawn-provider.dylib"
    "${_duplicate}")
execute_process(
    COMMAND "${_install_name_tool}" -id
        "@rpath/libvellum-skia-dawn-provider-duplicate.dylib" "${_duplicate}"
    RESULT_VARIABLE _duplicate_id_result
    OUTPUT_VARIABLE _duplicate_id_stdout
    ERROR_VARIABLE _duplicate_id_stderr)
if(NOT _duplicate_id_result EQUAL 0)
    message(FATAL_ERROR
        "provider duplicate install-name rewrite failed (${_duplicate_id_result})\n"
        "${_duplicate_id_stdout}\n${_duplicate_id_stderr}")
endif()
set(_cross_source "${_cross_image_dir}/main.cpp")
file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/cross_image_main.cpp"
    DESTINATION "${_cross_image_dir}")
file(RENAME
    "${_cross_image_dir}/cross_image_main.cpp"
    "${_cross_source}")
file(WRITE "${_cross_image_dir}/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumCrossImageProviderConsumer LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_executable(vellum-cross-image-consumer main.cpp)
set_target_properties(vellum-cross-image-consumer PROPERTIES
    BUILD_RPATH "@CROSS_RPATH@")
]=])
file(READ "${_cross_image_dir}/CMakeLists.txt" _cross_cmake)
string(REPLACE "@CROSS_RPATH@"
    "${_prefix}/${VELLUM_INSTALL_LIBDIR};${_prefix}/mirror/lib;${_cross_image_dir}"
    _cross_cmake "${_cross_cmake}")
file(WRITE "${_cross_image_dir}/CMakeLists.txt" "${_cross_cmake}")
_run("cross-image consumer configure" "${CMAKE_COMMAND}"
    -S "${_cross_image_dir}" -B "${_work}/cross-image-build"
    ${_generator} -DCMAKE_BUILD_TYPE=Release)
_run("cross-image consumer build" "${CMAKE_COMMAND}"
    --build "${_work}/cross-image-build")
_run("cross-image consumer load" "${_work}/cross-image-build/vellum-cross-image-consumer"
    "${_provider}" "${_duplicate}")

execute_process(
    COMMAND "${Python3_EXECUTABLE}"
        "${VELLUM_SOURCE_DIR}/graphics/tests/test_dawn_provider_topology.py"
        "${_work}/consumer/vellum-mirror-consumer"
        "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-gpu.dylib"
        "${_provider}"
        "${_prefix}/mirror/lib/libvellum-mirror-canvas.dylib"
        "${_duplicate}"
    RESULT_VARIABLE _duplicate_census_result
    OUTPUT_VARIABLE _duplicate_census_stdout
    ERROR_VARIABLE _duplicate_census_stderr)
if(_duplicate_census_result EQUAL 0 OR
   NOT _duplicate_census_stderr MATCHES "defines a second|incorrect dawn::native::GetProcs")
    message(FATAL_ERROR
        "provider census accepted a second image:\n"
        "${_duplicate_census_stdout}\n${_duplicate_census_stderr}")
endif()
message(STATUS "cross-image provider census negative control: ok")

_run("shared provider symbol ownership" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/graphics/tests/test_dawn_provider_topology.py"
    "${_work}/consumer/vellum-mirror-consumer"
    "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-gpu.dylib"
    "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-skia-dawn-provider.dylib"
    "${_prefix}/mirror/lib/libvellum-mirror-canvas.dylib")
_run("Phase-6 debt inertness" "${Python3_EXECUTABLE}"
    "${VELLUM_SOURCE_DIR}/tools/mirror/verify_mirror_inertness.py"
    --prefix "${_prefix}" --install-libdir "${VELLUM_INSTALL_LIBDIR}"
    ${_view_audio_args}
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
