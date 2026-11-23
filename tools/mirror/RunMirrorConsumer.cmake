# Installs this build, then builds and runs the mirror consumer against the
# installed package alone, proves the Phase-6 debts inert there, and proves
# that one image cannot take two Skia/Dawn providers.
foreach(_required VELLUM_SOURCE_DIR VELLUM_BUILD_DIR VELLUM_MIRROR_DEPS_DIR VELLUM_MIRROR_ORIGIN VELLUM_INSTALL_LIBDIR Python3_EXECUTABLE VELLUM_GPU_RUNTIME_PREFIX)
    if(NOT DEFINED ${_required})
        message(FATAL_ERROR "mirror consumer test: ${_required} was not supplied")
    endif()
endforeach()
if(NOT IS_DIRECTORY "${VELLUM_GPU_RUNTIME_PREFIX}" OR
   NOT EXISTS "${VELLUM_GPU_RUNTIME_PREFIX}/lib/cmake/VellumGpuRuntime/VellumGpuRuntimeConfig.cmake")
    message(FATAL_ERROR "mirror consumer test: VELLUM_GPU_RUNTIME_PREFIX must name an installed VellumGpuRuntime package")
endif()

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
    if(NOT _result STREQUAL "0")
        message(FATAL_ERROR "${label} failed (${_result})\n${_stdout}\n${_stderr}")
    endif()
    message(STATUS "${label}: ok")
    set(_last_stdout "${_stdout}" PARENT_SCOPE)
endfunction()

_run("install" "${CMAKE_COMMAND}" --install "${VELLUM_BUILD_DIR}" --prefix "${_prefix}")
file(READ "${_prefix}/share/vellum/runtime-images.json" _runtime_images)
string(JSON _provider_relative GET "${_runtime_images}" provider)
string(JSON _canvas_relative GET "${_runtime_images}" canvas)
set(_provider "${_prefix}/${_provider_relative}")
set(_canvas "${_prefix}/${_canvas_relative}")
get_filename_component(_provider_name "${_provider}" NAME)
get_filename_component(_canvas_name "${_canvas}" NAME)
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

# Across every installed dylib: no pulp:: definition, vellum:: definitions seen,
# and no exported symbol or Objective-C class defined by two images. The
# forwarding anchor is defined once per mirror unit on purpose. A planted
# duplicate must fail the same census.
set(_census "${Python3_EXECUTABLE}" "-I" "${VELLUM_SOURCE_DIR}/tools/mirror/symbol_census.py"
    --allow _vellum_mirror_forwarding_anchor ${_libraries} ${_installed_libraries})
_run("installed dylib symbol census" ${_census})
execute_process(COMMAND ${_census} --plant
    RESULT_VARIABLE _census_planted OUTPUT_QUIET ERROR_QUIET)
if(NOT _census_planted EQUAL 1)
    message(FATAL_ERROR "symbol census control: a planted duplicate image gave ${_census_planted}, not 1")
endif()
message(STATUS "symbol census control: ok")

# With a Pulp build's static libraries supplied, no installed dylib may export a
# name they define. The Pulp side must define something (the run is blind
# otherwise), and a planted shared name must fail the same check.
if(DEFINED VELLUM_PULP_CENSUS_LIBRARIES AND NOT VELLUM_PULP_CENSUS_LIBRARIES STREQUAL "")
    set(_pulp_census "${Python3_EXECUTABLE}" "-I" "${VELLUM_SOURCE_DIR}/tools/mirror/symbol_census.py"
        --allow _vellum_mirror_forwarding_anchor
        ${_libraries} ${_installed_libraries}
        --pulp-library ${VELLUM_PULP_CENSUS_LIBRARIES})
    _run("installed dylibs against a Pulp build" ${_pulp_census})
    execute_process(COMMAND ${_pulp_census} --plant-pulp
        RESULT_VARIABLE _pulp_planted OUTPUT_QUIET ERROR_QUIET)
    if(NOT _pulp_planted EQUAL 1)
        message(FATAL_ERROR "Pulp intersection control: a planted shared name gave ${_pulp_planted}, not 1")
    endif()
    message(STATUS "Pulp intersection control: ok")
else()
    message(STATUS "installed dylibs against a Pulp build: skipped (no VELLUM_PULP_CENSUS_LIBRARIES)")
endif()

set(_generator)
find_program(_ninja ninja)
if(_ninja)
    set(_generator -G Ninja)
endif()

# GPU device types are not part of the installed surface (gpu_surface_guard.py).
# Installed headers equal their mirror sources byte for byte, except that each
# choke header carries one guard line; a consumer that includes one fails with
# the guard's message, and the consumers above and below still build. Naming
# the types through the forward declaration is supported: a plug-in editor
# includes view/plugin_view_host.hpp without VELLUM_MIRROR_BUILD.
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
    foreach(_case "${_units_root}/render/skia_surface.hpp" "${_units_root}/render/gpu_surface.hpp")
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

# The hosted GPU proof opts into live rendering. The installed host can
# exercise viewport geometry and input even without a native GPU adapter.
set(_run_live_gpu FALSE)
if(DEFINED ENV{VELLUM_MIRROR_LIVE_GPU})
    set(_live_gpu "$ENV{VELLUM_MIRROR_LIVE_GPU}")
    if(_live_gpu MATCHES "^(1|ON|TRUE|YES)$")
        set(_run_live_gpu TRUE)
    elseif(NOT _live_gpu MATCHES "^(0|OFF|FALSE|NO)?$")
        message(FATAL_ERROR
            "VELLUM_MIRROR_LIVE_GPU must be one of 1/0, ON/OFF, TRUE/FALSE, or YES/NO")
    endif()
endif()

find_program(_nm nm REQUIRED)
set(_provider_census "${Python3_EXECUTABLE}" -I
    "${VELLUM_SOURCE_DIR}/tools/mirror/provider_symbol_census.py"
    "${_provider}" "${VELLUM_SOURCE_DIR}/cmake/skia_dawn_provider.exports")
_run("provider export and bind census" ${_provider_census})
execute_process(COMMAND ${_provider_census} --plant
    RESULT_VARIABLE _plant_result OUTPUT_VARIABLE _plant_stdout ERROR_VARIABLE _plant_stderr)
if(NOT _plant_result EQUAL 1 OR NOT _plant_stderr MATCHES "provider has 5 unexpected exports:")
    message(FATAL_ERROR "provider census did not catch the planted exports:\n${_plant_stdout}\n${_plant_stderr}")
endif()
message(STATUS "provider export census negative control: ok")

set(_weak_tint_dir "${_work}/weak-tint")
file(MAKE_DIRECTORY "${_weak_tint_dir}")
file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/weak_tint_main.cpp"
    DESTINATION "${_weak_tint_dir}")
file(WRITE "${_weak_tint_dir}/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumWeakTintConsumer LANGUAGES CXX)
find_package(Vellum CONFIG REQUIRED)
add_executable(vellum-weak-tint-consumer weak_tint_main.cpp)
target_compile_features(vellum-weak-tint-consumer PRIVATE cxx_std_20)
target_link_options(vellum-weak-tint-consumer PRIVATE "LINKER:-export_dynamic")
target_link_libraries(vellum-weak-tint-consumer PRIVATE Vellum::SkiaDawnProvider)
]=])
_run("weak Tint consumer configure" "${CMAKE_COMMAND}"
    -S "${_weak_tint_dir}" -B "${_weak_tint_dir}/build" ${_generator}
    -DCMAKE_BUILD_TYPE=Release "-DCMAKE_PREFIX_PATH=${_prefix}"
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE)
_run("weak Tint consumer build" "${CMAKE_COMMAND}" --build "${_weak_tint_dir}/build")
set(_weak_tint_host "${_weak_tint_dir}/build/vellum-weak-tint-consumer")
_run("weak Tint consumer symbol table" "${_nm}" -m "${_weak_tint_host}")
string(REGEX MATCH "weak external (__ZN4tint[^ \n]+)" _host_match "${_last_stdout}")
if(NOT _host_match)
    message(FATAL_ERROR "weak Tint consumer did not export its conflicting definition")
endif()
set(_weak_tint_name "${CMAKE_MATCH_1}")
_run("provider symbol table" "${_nm}" -jU "${_provider}")
if(NOT _last_stdout MATCHES "(^|\n)${_weak_tint_name}(\n|$)")
    message(FATAL_ERROR "provider does not define the Tint matcher planted in the consumer")
endif()
_run("weak Tint consumer using Dawn Null" "${_weak_tint_host}")
if(_run_live_gpu)
    _run("weak Tint consumer using Metal" "${_weak_tint_host}" --live-gpu)
endif()

# A plug-in editor's view of the host: plugin_view_host.hpp reaches the GPU
# device type only through platform/gpu_fwd.hpp, so it builds, links and runs
# against the installed package without VELLUM_MIRROR_BUILD.
set(_pvh_dir "${_work}/plugin-view-host")
file(MAKE_DIRECTORY "${_pvh_dir}")
file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/plugin_view_host_main.mm"
    DESTINATION "${_pvh_dir}")
file(WRITE "${_pvh_dir}/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumPluginViewHostConsumer LANGUAGES CXX OBJCXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Vellum CONFIG REQUIRED)
add_executable(vellum-plugin-view-host-consumer plugin_view_host_main.mm)
target_link_libraries(vellum-plugin-view-host-consumer PRIVATE Vellum::MirrorView_core)
target_link_libraries(vellum-plugin-view-host-consumer PRIVATE "-framework AppKit")
get_target_property(_defs vellum-plugin-view-host-consumer COMPILE_DEFINITIONS)
if(_defs MATCHES "VELLUM_MIRROR_BUILD")
    message(FATAL_ERROR "the plug-in view host consumer must not define VELLUM_MIRROR_BUILD")
endif()
]=])
_run("plug-in view host consumer configure" "${CMAKE_COMMAND}"
    -S "${_pvh_dir}" -B "${_pvh_dir}/build" ${_generator} -DCMAKE_BUILD_TYPE=Release
    "-DCMAKE_PREFIX_PATH=${_prefix}"
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE)
_run("plug-in view host consumer build" "${CMAKE_COMMAND}" --build "${_pvh_dir}/build")
set(_pvh_args)
if(_run_live_gpu)
    set(_pvh_args --live-gpu)
endif()
_run("plug-in view host consumer run" "${_pvh_dir}/build/vellum-plugin-view-host-consumer" ${_pvh_args})
message(STATUS "${_last_stdout}")

# A native plug-in editor keeps its editor translation units on the installed
# mirror and names GPU types only through gpu_fwd.hpp. A separate translation
# unit includes the device definition from the installed VellumGpuRuntime
# package and links its Device target.
set(_native_gpu_editor_dir "${_work}/native-gpu-editor")
file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/native-gpu-editor/"
    DESTINATION "${_native_gpu_editor_dir}")
string(REPLACE ";" "\\;" _native_gpu_prefix
    "${_prefix};${VELLUM_GPU_RUNTIME_PREFIX}")
execute_process(COMMAND "${CMAKE_COMMAND}"
    -S "${_native_gpu_editor_dir}" -B "${_native_gpu_editor_dir}/build" ${_generator}
    -DCMAKE_BUILD_TYPE=Release
    "-DCMAKE_PREFIX_PATH=${_native_gpu_prefix}"
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE
    RESULT_VARIABLE _native_gpu_configure_result
    OUTPUT_VARIABLE _native_gpu_configure_out
    ERROR_VARIABLE _native_gpu_configure_err)
if(NOT _native_gpu_configure_result EQUAL 0)
    message(FATAL_ERROR "native GPU editor consumer configure failed (${_native_gpu_configure_result})\n"
        "${_native_gpu_configure_out}\n${_native_gpu_configure_err}")
endif()
message(STATUS "native GPU editor consumer configure: ok")
_run("native GPU editor consumer build"
    "${CMAKE_COMMAND}" --build "${_native_gpu_editor_dir}/build")

# The editor-side choke header remains a deliberate compile-time failure.
set(_native_gpu_negative_dir "${_work}/native-gpu-editor-negative")
file(MAKE_DIRECTORY "${_native_gpu_negative_dir}")
file(COPY
    "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/native-gpu-editor/negative/CMakeLists.txt"
    "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/native-gpu-editor/negative_editor.cpp"
    DESTINATION "${_native_gpu_negative_dir}")
_run("native GPU editor negative configure" "${CMAKE_COMMAND}"
    -S "${_native_gpu_negative_dir}" -B "${_native_gpu_negative_dir}/build" ${_generator}
    -DCMAKE_BUILD_TYPE=Release
    "-DCMAKE_PREFIX_PATH=${_prefix}"
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE)
execute_process(COMMAND "${CMAKE_COMMAND}" --build "${_native_gpu_negative_dir}/build"
    RESULT_VARIABLE _native_gpu_negative_result
    OUTPUT_VARIABLE _native_gpu_negative_out ERROR_VARIABLE _native_gpu_negative_err)
if(_native_gpu_negative_result EQUAL 0)
    message(FATAL_ERROR "including <vellum/render/gpu_surface.hpp> unexpectedly compiled")
endif()
string(FIND "${_native_gpu_negative_out}${_native_gpu_negative_err}"
    "not part of the installed mirror's supported surface" _native_gpu_negative_hit)
if(_native_gpu_negative_hit EQUAL -1)
    message(FATAL_ERROR "native GPU editor negative control failed without the installed guard message:\n"
        "${_native_gpu_negative_out}\n${_native_gpu_negative_err}")
endif()
message(STATUS "native GPU editor choke-header negative control: ok")

# The native bootstrap entry points are provider-owned. The installed mirror
# consumer must resolve them from that dylib, and must not carry the Dawn proc
# table calls that the old inline header emitted into plug-in objects. The
# installed SDK intentionally does not ship libdawn_combined.a, so a consumer
# cannot link a second pinned Dawn archive through this package boundary. The
# no-proc-reference check below is the strongest installed-consumer proof, while
# the provider's own whole-archive link remains the sole Dawn definition.
find_program(_nm nm REQUIRED)
execute_process(COMMAND "${_nm}" -gU "${_provider}"
    RESULT_VARIABLE _provider_nm_result OUTPUT_VARIABLE _provider_nm ERROR_VARIABLE _provider_nm_error)
if(NOT _provider_nm_result EQUAL 0 OR
   NOT _provider_nm MATCHES "register_native_dawn_bootstrap" OR
   NOT _provider_nm MATCHES "register_native_dawn_bootstrap_for_bundle" OR
   NOT _provider_nm MATCHES "create_dawn")
    message(FATAL_ERROR
        "the installed provider does not export the native Dawn bootstrap and GpuSurface ABI entry points:\n"
        "${_provider_nm}\n${_provider_nm_error}")
endif()
message(STATUS "provider exports native Dawn bootstrap and GpuSurface ABI entry points: ok")
# nm prints C++ names mangled (__ZN4dawn6native8GetProcsEv), so the pattern
# matches the mangled spelling as well as the demangled one and the C entry
# points. A planted reference proves the pattern sees what nm really prints.
set(_dawn_proc_ref_pattern "dawnProc(Set|Get)|dawn6native8GetProcs|dawn::native::GetProcs")
set(_proc_control_dir "${_work}/dawn-proc-ref-control")
file(MAKE_DIRECTORY "${_proc_control_dir}")
file(WRITE "${_proc_control_dir}/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumDawnProcRefControl LANGUAGES CXX)
add_library(dawn_proc_ref_control STATIC control.cpp)
]=])
file(WRITE "${_proc_control_dir}/control.cpp" [=[
namespace dawn::native { const void* GetProcs(); }
const void* vellum_dawn_proc_ref_control() { return dawn::native::GetProcs(); }
]=])
_run("Dawn proc-table reference control configure" "${CMAKE_COMMAND}"
    -S "${_proc_control_dir}" -B "${_proc_control_dir}/build" ${_generator}
    -DCMAKE_BUILD_TYPE=Release)
_run("Dawn proc-table reference control build" "${CMAKE_COMMAND}"
    --build "${_proc_control_dir}/build")
execute_process(COMMAND "${_nm}" -u "${_proc_control_dir}/build/libdawn_proc_ref_control.a"
    RESULT_VARIABLE _control_nm_result OUTPUT_VARIABLE _control_nm ERROR_VARIABLE _control_nm_error)
if(NOT _control_nm_result EQUAL 0 OR NOT _control_nm MATCHES "${_dawn_proc_ref_pattern}")
    message(FATAL_ERROR
        "negative control: the Dawn proc-table check does not see a planted "
        "dawn::native::GetProcs reference, so a clean result would prove nothing:\n"
        "${_control_nm}\n${_control_nm_error}")
endif()
message(STATUS "Dawn proc-table check sees a planted reference: ok")
execute_process(COMMAND "${_nm}" -u "${_pvh_dir}/build/vellum-plugin-view-host-consumer"
    RESULT_VARIABLE _consumer_nm_result OUTPUT_VARIABLE _consumer_nm ERROR_VARIABLE _consumer_nm_error)
if(NOT _consumer_nm_result EQUAL 0)
    message(FATAL_ERROR "installed plug-in consumer nm failed: ${_consumer_nm_error}")
endif()
if(_consumer_nm MATCHES "${_dawn_proc_ref_pattern}")
    message(FATAL_ERROR
        "installed plug-in consumer still references Dawn proc-table symbols:\n${_consumer_nm}")
endif()
message(STATUS "installed plug-in consumer has no Dawn proc-table references: ok")
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
    IMPORTED_LOCATION "${VELLUM_MIRROR_CANVAS}"
    INTERFACE_INCLUDE_DIRECTORIES "${VELLUM_MIRROR_PREFIX}/mirror/include")
add_executable(vellum-mirror-direct-consumer direct_main.cpp)
target_link_libraries(vellum-mirror-direct-consumer PRIVATE vellum-mirror-direct)
# Retain only the canvas dylib as the explicit link input.
target_link_directories(vellum-mirror-direct-consumer PRIVATE
    "${VELLUM_MIRROR_PREFIX}/${VELLUM_INSTALL_LIBDIR}")
target_link_options(vellum-mirror-direct-consumer PRIVATE
    "LINKER:-needed_library,${VELLUM_MIRROR_CANVAS}")
set_target_properties(vellum-mirror-direct-consumer PROPERTIES
    BUILD_RPATH "${VELLUM_MIRROR_PREFIX}/mirror/lib;${VELLUM_MIRROR_PREFIX}/${VELLUM_INSTALL_LIBDIR}")
]=])
file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/direct_main.cpp"
    DESTINATION "${_work}/direct")
_run("direct consumer configure" "${CMAKE_COMMAND}"
    -S "${_work}/direct" -B "${_work}/direct-build"
    ${_generator} -DCMAKE_BUILD_TYPE=Release
    "-DVELLUM_MIRROR_PREFIX=${_prefix}"
    "-DVELLUM_INSTALL_LIBDIR=${VELLUM_INSTALL_LIBDIR}"
    "-DVELLUM_MIRROR_CANVAS=${_canvas}")
_run("direct consumer build" "${CMAKE_COMMAND}" --build "${_work}/direct-build")
_run("direct consumer run" "${_work}/direct-build/vellum-mirror-direct-consumer"
    "@rpath/${_canvas_name}" "${_provider_name}")

# Keep the ABI assertion explicit so a future change cannot make this fixture
# pass by hiding the re-export behind target transitivity or a second provider
# definition.
find_program(_otool otool REQUIRED)
execute_process(
    COMMAND "${_otool}" -L "${_canvas}"
    RESULT_VARIABLE _mirror_otool_result
    OUTPUT_VARIABLE _mirror_otool
    ERROR_VARIABLE _mirror_otool_error)
string(FIND "${_mirror_otool}" "@rpath/${_provider_name} (" _mirror_provider_link)
if(NOT _mirror_otool_result EQUAL 0 OR
   _mirror_provider_link EQUAL -1 OR
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
    "LINKER:-dylib_file,@rpath/${VELLUM_PROVIDER_NAME}:${VELLUM_PROVIDER_PATH}")
]=])
file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/direct_gpu_main.cpp"
    DESTINATION "${_work}/direct-gpu")
_run("direct GPU consumer configure" "${CMAKE_COMMAND}"
    -S "${_work}/direct-gpu" -B "${_work}/direct-gpu-build"
    ${_generator} -DCMAKE_BUILD_TYPE=Release
    "-DVELLUM_MIRROR_PREFIX=${_prefix}"
    "-DVELLUM_INSTALL_LIBDIR=${VELLUM_INSTALL_LIBDIR}"
    "-DVELLUM_PROVIDER_PATH=${_provider}"
    "-DVELLUM_PROVIDER_NAME=${_provider_name}")
_run("direct GPU consumer build" "${CMAKE_COMMAND}" --build "${_work}/direct-gpu-build")

# A local mirror-consumer run may not have a live GPU even when the installed
# dylib was built with the GPU provider. Require an explicit opt-in for the
# runtime probe so an unavailable device is reported as such rather than being
# mistaken for a passing consumer test. The hosted GPU proof sets this variable
# around ctest; an opted-in runtime failure remains fatal through _run().
if(_run_live_gpu)
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
    "${_provider}"
    "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-gpu.dylib"
    "${_canvas}")

execute_process(
    COMMAND "${_otool}" -L "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libvellum-gpu.dylib"
    RESULT_VARIABLE _gpu_otool_result
    OUTPUT_VARIABLE _gpu_otool
    ERROR_VARIABLE _gpu_otool_error)
string(FIND "${_gpu_otool}" "@rpath/${_provider_name} (" _gpu_provider_link)
if(NOT _gpu_otool_result EQUAL 0 OR
   _gpu_provider_link EQUAL -1 OR
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
set(_duplicate_name "duplicate-${_provider_name}")
set(_duplicate "${_cross_image_dir}/${_duplicate_name}")
file(COPY_FILE "${_provider}" "${_duplicate}")
execute_process(
    COMMAND "${_install_name_tool}" -id
        "@rpath/${_duplicate_name}" "${_duplicate}"
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
        "${_canvas}"
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
    "${_provider}"
    "${_canvas}")
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

# With VELLUM_ENABLE_V8 the install carries the sealed libv8 beside the
# provider. Prove the provider loads it from there, that neither image can
# bind the other's ICU, Abseil or zlib, and that an installed consumer runs
# the pinned V8.
if(VELLUM_ENABLE_V8)
    if(NOT DEFINED VELLUM_V8_VERSION OR VELLUM_V8_VERSION STREQUAL "")
        message(FATAL_ERROR "mirror consumer test: VELLUM_V8_VERSION was not supplied")
    endif()
    set(_v8 "${_prefix}/${VELLUM_INSTALL_LIBDIR}/libv8.dylib")
    if(NOT EXISTS "${_v8}" OR NOT EXISTS "${_prefix}/share/vellum/v8/manifest.json")
        message(FATAL_ERROR "the V8 install lacks lib/libv8.dylib or share/vellum/v8/manifest.json")
    endif()
    execute_process(COMMAND "${_otool}" -L "${_provider}"
        RESULT_VARIABLE _provider_otool_result OUTPUT_VARIABLE _provider_otool)
    if(NOT _provider_otool_result EQUAL 0 OR NOT _provider_otool MATCHES "@rpath/libv8\\.dylib")
        message(FATAL_ERROR "the installed provider does not load @rpath/libv8.dylib:\n${_provider_otool}")
    endif()
    _run("V8 and provider symbol census" "${Python3_EXECUTABLE}"
        "${VELLUM_SOURCE_DIR}/tools/mirror/v8_symbol_census.py"
        --provider "${_provider}" --v8 "${_v8}")
    message(STATUS "${_last_stdout}")
    execute_process(COMMAND "${Python3_EXECUTABLE}"
        "${VELLUM_SOURCE_DIR}/tools/mirror/v8_symbol_census.py"
        --provider "${_provider}" --v8 "${_v8}" --plant
        RESULT_VARIABLE _v8_planted OUTPUT_QUIET ERROR_QUIET)
    if(NOT _v8_planted EQUAL 1)
        message(FATAL_ERROR "V8 symbol census control: a planted Abseil export gave ${_v8_planted}, not 1")
    endif()
    message(STATUS "V8 symbol census control: ok")

    file(MAKE_DIRECTORY "${_work}/v8-consumer")
    file(WRITE "${_work}/v8-consumer/CMakeLists.txt" [=[
cmake_minimum_required(VERSION 3.24)
project(VellumV8Consumer LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Vellum CONFIG REQUIRED)
add_executable(vellum-v8-consumer v8_main.cpp)
target_link_libraries(vellum-v8-consumer PRIVATE Vellum::MirrorView_script)
]=])
    file(COPY "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/v8_main.cpp"
        DESTINATION "${_work}/v8-consumer")
    _run("V8 consumer configure" "${CMAKE_COMMAND}"
        -S "${_work}/v8-consumer" -B "${_work}/v8-consumer-build"
        ${_generator} -DCMAKE_BUILD_TYPE=Release
        "-DCMAKE_PREFIX_PATH=${_prefix}"
        -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE
        -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE)
    _run("V8 consumer build" "${CMAKE_COMMAND}" --build "${_work}/v8-consumer-build")
    _run("V8 consumer run" "${_work}/v8-consumer-build/vellum-v8-consumer" "${VELLUM_V8_VERSION}")
    message(STATUS "${_last_stdout}")
    if(VELLUM_INSTALL_THREEJS)
        _run("Three.js demo configure" "${CMAKE_COMMAND}"
            -S "${VELLUM_SOURCE_DIR}/tools/mirror/consumer/threejs-demo"
            -B "${_work}/threejs-demo" ${_generator} -DCMAKE_BUILD_TYPE=Release
            "-DVellum_DIR=${_prefix}/${VELLUM_INSTALL_LIBDIR}/cmake/Vellum"
            -DCMAKE_FIND_USE_PACKAGE_REGISTRY=FALSE
            -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=FALSE)
        _run("Three.js demo build" "${CMAKE_COMMAND}" --build "${_work}/threejs-demo")
        _run("Three.js demo tests" "${CMAKE_CTEST_COMMAND}"
            --test-dir "${_work}/threejs-demo" --no-tests=error --verbose)
        message(STATUS "${_last_stdout}")
    endif()
endif()
