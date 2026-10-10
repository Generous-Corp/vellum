set(_VELLUM_MIRROR_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/mirror")
# Closure manifests are the source of truth for rehearsal units.  The generated
# metadata is intentionally rebuilt at configure time so X-export can add D3
# units without a hand-maintained source list.
find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
file(GLOB _mirror_closure_manifests CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/provenance/mirror/closures/*/*.json")
if(NOT _mirror_closure_manifests)
    message(FATAL_ERROR "Vellum mirror closure manifests are required")
endif()
list(GET _mirror_closure_manifests 0 _mirror_first_manifest)
get_filename_component(_mirror_closure_dir "${_mirror_first_manifest}" DIRECTORY)
set(_mirror_generated_dir "${CMAKE_CURRENT_BINARY_DIR}/generated/mirror")
set(_mirror_generated_units "${_mirror_generated_dir}/units.cmake")
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/packaging/generate_mirror_units.py"
        "${_VELLUM_MIRROR_ROOT}" "${_mirror_closure_dir}" "${_mirror_generated_units}"
    RESULT_VARIABLE _mirror_generate_result)
if(NOT _mirror_generate_result EQUAL 0)
    message(FATAL_ERROR "failed to generate mirror unit metadata")
endif()
include("${_mirror_generated_units}")

option(VELLUM_MIRROR_BUILD_REHEARSAL_UNITS
    "Build every unit and target described by the exported closure manifests" OFF)

# Dry-run mirror package. Sources retain their original namespace by Decision 0010.
file(GLOB_RECURSE _mirror_platform_sources CONFIGURE_DEPENDS
    "${_VELLUM_MIRROR_ROOT}/core/platform/src/*.cpp"
    "${_VELLUM_MIRROR_ROOT}/core/platform/platform/mac/*.mm")
file(GLOB_RECURSE _mirror_canvas_sources CONFIGURE_DEPENDS
    "${_VELLUM_MIRROR_ROOT}/core/canvas/src/*.cpp"
    "${_VELLUM_MIRROR_ROOT}/core/canvas/platform/mac/*.mm")
add_library(vellum-mirror-foundation STATIC ${_mirror_platform_sources})
add_library(Vellum::MirrorFoundation ALIAS vellum-mirror-foundation)
set_target_properties(vellum-mirror-foundation PROPERTIES EXPORT_NAME MirrorFoundation)
target_include_directories(vellum-mirror-foundation PUBLIC
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/core/platform/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/external>
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/external/cpp-httplib>
    $<INSTALL_INTERFACE:include>)
target_compile_features(vellum-mirror-foundation PUBLIC cxx_std_20)
add_library(vellum-mirror-canvas STATIC ${_mirror_canvas_sources})
add_library(Vellum::MirrorCanvas ALIAS vellum-mirror-canvas)
set_target_properties(vellum-mirror-canvas PROPERTIES EXPORT_NAME MirrorCanvas)
target_include_directories(vellum-mirror-canvas PUBLIC
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/core/canvas/include>
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/core/platform/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/external>
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/external/nanosvg>
    $<INSTALL_INTERFACE:include>)
target_compile_features(vellum-mirror-canvas PUBLIC cxx_std_20)
target_link_libraries(vellum-mirror-canvas PUBLIC Vellum::MirrorFoundation)
file(GLOB _mirror_receipts CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/provenance/mirror/receipts/*.json")
list(FILTER _mirror_receipts EXCLUDE REGEX "/index\\.json$")
list(SORT _mirror_receipts)
list(GET _mirror_receipts -1 VELLUM_MIRROR_RECEIPT)

if(VELLUM_MIRROR_BUILD_REHEARSAL_UNITS)
    foreach(_mirror_unit IN LISTS VELLUM_MIRROR_UNITS)
        string(TOUPPER "${_mirror_unit}" _mirror_unit_key)
        string(REPLACE "-" "_" _mirror_unit_key "${_mirror_unit_key}")
        foreach(_mirror_source_target IN LISTS VELLUM_MIRROR_UNIT_${_mirror_unit_key}_TARGETS)
            string(TOUPPER "${_mirror_source_target}" _mirror_target_key)
            string(REPLACE "-" "_" _mirror_target_key "${_mirror_target_key}")
            if(NOT VELLUM_MIRROR_TARGET_${_mirror_target_key}_SOURCES)
                message(FATAL_ERROR "mirror target ${_mirror_source_target} has no mirrored sources")
            endif()
            string(MAKE_C_IDENTIFIER "${_mirror_unit}_${_mirror_source_target}" _mirror_id)
            add_library("vellum-mirror-rehearsal-${_mirror_id}" STATIC
                ${VELLUM_MIRROR_TARGET_${_mirror_target_key}_SOURCES})
            add_library("Vellum::MirrorRehearsal${_mirror_id}" ALIAS
                "vellum-mirror-rehearsal-${_mirror_id}")
            target_include_directories("vellum-mirror-rehearsal-${_mirror_id}" PUBLIC
                $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}>
                $<INSTALL_INTERFACE:include>)
            target_compile_features("vellum-mirror-rehearsal-${_mirror_id}" PUBLIC cxx_std_20)
        endforeach()
    endforeach()
endif()

if(NOT VELLUM_MIRROR_RECEIPT)
    message(FATAL_ERROR "Vellum mirror receipt is required")
endif()
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/canvas/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/platform/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/runtime/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/simd/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(FILES "${VELLUM_MIRROR_RECEIPT}" DESTINATION "${CMAKE_INSTALL_DATADIR}/vellum/provenance/mirror/receipts")
if(VELLUM_BUILD_TESTS AND NOT EMSCRIPTEN)
    add_test(NAME vellum.mirror-receipt COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/provenance/test_mirror_receipt.py" "${VELLUM_MIRROR_RECEIPT}")
    add_test(NAME vellum.mirror-rehearsal-manifest
        COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_SOURCE_DIR}/tools/packaging/test_mirror_rehearsal_manifest.py"
            "${_mirror_closure_dir}" "${_VELLUM_MIRROR_ROOT}")
    add_test(NAME vellum.mirror-install-consumer COMMAND "${CMAKE_COMMAND}" "-DVELLUM_SOURCE_DIR=${CMAKE_SOURCE_DIR}" "-DVELLUM_BUILD_DIR=${CMAKE_BINARY_DIR}" "-DTEST_CONFIG=$<CONFIG>" -P "${CMAKE_SOURCE_DIR}/cmake/RunMirrorInstallConsumer.cmake")
endif()
