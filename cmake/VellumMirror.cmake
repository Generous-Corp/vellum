set(_VELLUM_MIRROR_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/mirror")

# X-export closure manifests are authoritative. This generated metadata is
# recreated at configure time so new D3 units need no hand-maintained lists.
find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
file(GLOB _mirror_closure_manifests CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/provenance/mirror/closures/*/*.json")
if(NOT _mirror_closure_manifests)
    message(FATAL_ERROR "Vellum mirror closure manifests are required")
endif()
list(GET _mirror_closure_manifests 0 _mirror_first_manifest)
get_filename_component(_mirror_closure_dir "${_mirror_first_manifest}" DIRECTORY)
set(_mirror_generated_units "${CMAKE_CURRENT_BINARY_DIR}/generated/mirror/units.cmake")
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/packaging/generate_mirror_units.py"
        "${_VELLUM_MIRROR_ROOT}" "${_mirror_closure_dir}" "${_mirror_generated_units}"
    RESULT_VARIABLE _mirror_generate_result)
if(NOT _mirror_generate_result EQUAL 0)
    message(FATAL_ERROR "failed to generate mirror unit metadata")
endif()
include("${_mirror_generated_units}")

option(VELLUM_MIRROR_BUILD_REHEARSAL_UNITS
    "Build every target described by the exported closure manifests" OFF)

function(_vellum_mirror_configure_target target unit target_name)
    string(TOUPPER "${unit}_${target_name}" _metadata_key)
    string(REPLACE "-" "_" _metadata_key "${_metadata_key}")
    if(NOT VELLUM_MIRROR_TARGET_${_metadata_key}_SOURCES)
        message(FATAL_ERROR
            "mirror target ${unit}/${target_name} has no mirrored compilable sources")
    endif()
    target_sources("${target}" PRIVATE
        ${VELLUM_MIRROR_TARGET_${_metadata_key}_SOURCES})
    target_include_directories("${target}" PUBLIC
        $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}>
        $<INSTALL_INTERFACE:include>)
    foreach(_mirror_include IN LISTS VELLUM_MIRROR_TARGET_${_metadata_key}_INCLUDE_DIRS)
        target_include_directories("${target}" PUBLIC
            $<BUILD_INTERFACE:${_mirror_include}>)
    endforeach()
    target_compile_definitions("${target}" PRIVATE
        ${VELLUM_MIRROR_TARGET_${_metadata_key}_DEFINITIONS})
    target_compile_features("${target}" PUBLIC cxx_std_20)
endfunction()

# Stable package aliases for the dry-run foundation+canvas rung. Their source
# lists still come from the closure manifests.
add_library(vellum-mirror-foundation STATIC)
add_library(Vellum::MirrorFoundation ALIAS vellum-mirror-foundation)
set_target_properties(vellum-mirror-foundation PROPERTIES EXPORT_NAME MirrorFoundation)
_vellum_mirror_configure_target(vellum-mirror-foundation FOUNDATION pulp-platform)
add_library(vellum-mirror-canvas STATIC)
add_library(Vellum::MirrorCanvas ALIAS vellum-mirror-canvas)
set_target_properties(vellum-mirror-canvas PROPERTIES EXPORT_NAME MirrorCanvas)
_vellum_mirror_configure_target(vellum-mirror-canvas CANVAS pulp-canvas)
target_link_libraries(vellum-mirror-canvas PUBLIC Vellum::MirrorFoundation)

set(_vellum_rehearsal_targets)
if(VELLUM_MIRROR_BUILD_REHEARSAL_UNITS)
    # Manifest order is the export's dependency order. Each target is isolated
    # in its unit namespace, preventing accidental archive/name collisions.
    foreach(_mirror_unit IN LISTS VELLUM_MIRROR_UNITS)
        string(TOUPPER "${_mirror_unit}" _mirror_unit_key)
        string(REPLACE "-" "_" _mirror_unit_key "${_mirror_unit_key}")
        foreach(_mirror_source_target IN LISTS VELLUM_MIRROR_UNIT_${_mirror_unit_key}_TARGETS)
            string(MAKE_C_IDENTIFIER
                "vellum-mirror-rehearsal-${_mirror_unit}-${_mirror_source_target}"
                _mirror_id)
            string(TOUPPER "${_mirror_unit}_${_mirror_source_target}" _mirror_metadata_key)
            string(REPLACE "-" "_" _mirror_metadata_key "${_mirror_metadata_key}")
            add_library("${_mirror_id}" STATIC)
            set_target_properties("${_mirror_id}" PROPERTIES
                EXPORT_NAME "MirrorRehearsal_${_mirror_unit}_${_mirror_source_target}")
            _vellum_mirror_configure_target("${_mirror_id}" "${_mirror_unit}" "${_mirror_source_target}")
            list(APPEND _vellum_rehearsal_targets "${_mirror_id}")
        endforeach()
    endforeach()
endif()

file(GLOB _mirror_receipts CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/provenance/mirror/receipts/*.json")
list(FILTER _mirror_receipts EXCLUDE REGEX "/index\\.json$")
list(SORT _mirror_receipts)
if(NOT _mirror_receipts)
    message(FATAL_ERROR "Vellum mirror receipt is required")
endif()
list(GET _mirror_receipts -1 VELLUM_MIRROR_RECEIPT)

install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/canvas/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/platform/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/runtime/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/simd/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(FILES "${VELLUM_MIRROR_RECEIPT}" DESTINATION "${CMAKE_INSTALL_DATADIR}/vellum/provenance/mirror/receipts")
if(VELLUM_BUILD_TESTS AND NOT EMSCRIPTEN)
    add_test(NAME vellum.mirror-receipt COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/provenance/test_mirror_receipt.py" "${VELLUM_MIRROR_RECEIPT}")
    add_test(NAME vellum.mirror-install-consumer COMMAND "${CMAKE_COMMAND}" "-DVELLUM_SOURCE_DIR=${CMAKE_SOURCE_DIR}" "-DVELLUM_BUILD_DIR=${CMAKE_BINARY_DIR}" "-DTEST_CONFIG=$<CONFIG>" -P "${CMAKE_SOURCE_DIR}/cmake/RunMirrorInstallConsumer.cmake")
    if(VELLUM_MIRROR_BUILD_REHEARSAL_UNITS)
        add_test(NAME vellum.mirror-rehearsal-consumers COMMAND "${CMAKE_COMMAND}" "-DVELLUM_SOURCE_DIR=${CMAKE_SOURCE_DIR}" "-DVELLUM_BUILD_DIR=${CMAKE_BINARY_DIR}" "-DVELLUM_GENERATED_UNITS=${_mirror_generated_units}" "-DTEST_CONFIG=$<CONFIG>" -P "${CMAKE_SOURCE_DIR}/cmake/RunMirrorRehearsalConsumers.cmake")
    endif()
endif()
