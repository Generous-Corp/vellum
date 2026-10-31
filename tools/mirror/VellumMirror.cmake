# Builds the landed mirror (Decision 0010) into the Vellum package.
#
# tools/mirror/mirror_package.py proves the working tree's mirror/ equals the
# newest landed export receipt, fetches and digest-checks every pinned input
# the closures name, produces the generated inputs, and writes the target plan
# this file instantiates. The mirrored targets keep their closure names inside
# the build graph; the package exposes them only as two shared libraries and
# the `Vellum::MirrorFoundation` / `Vellum::MirrorCanvas` identities, installed
# under the package's mirror/ subtree.
#
# GPU and canvas resolve Skia/Dawn through the same installed provider. Mirror
# libraries forward their public namespace, while the provider owns the
# mirrored implementation objects and pinned Skia/Dawn definitions.

if(NOT APPLE OR NOT VELLUM_HAS_SKIA_DAWN)
    message(FATAL_ERROR
        "Vellum: the landed mirror was exported from the Darwin arm64 Skia/Dawn "
        "configuration; VELLUM_ENABLE_MIRROR needs the locked Skia/Dawn archive")
endif()
if(NOT CMAKE_BUILD_TYPE STREQUAL "Release")
    message(FATAL_ERROR
        "Vellum: the landed mirror's closures record a Release configuration; "
        "configure with -DCMAKE_BUILD_TYPE=Release")
endif()
enable_language(C)
enable_language(OBJCXX)
find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)

set(VELLUM_MIRROR_DEPS_DIR "${CMAKE_BINARY_DIR}/_deps/vellum-mirror" CACHE PATH
    "Where the mirror's pinned dependency sources are fetched")
set(_vellum_mirror_generated "${CMAKE_BINARY_DIR}/generated/vellum-mirror")
set(_vellum_mirror_plan_cmake "${_vellum_mirror_generated}/plan.cmake")
set(_vellum_mirror_plan_json "${_vellum_mirror_generated}/plan.json")
execute_process(
    COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tools/mirror/mirror_package.py"
        --repo "${PROJECT_SOURCE_DIR}"
        --deps-dir "${VELLUM_MIRROR_DEPS_DIR}"
        --generated-dir "${_vellum_mirror_generated}"
        --skia-dir "${VELLUM_SKIA_DIR}"
        --skia-asset-sha256 "${_vellum_expected_archive_sha256}"
        --cmake-output "${_vellum_mirror_plan_cmake}"
        --json-output "${_vellum_mirror_plan_json}"
    RESULT_VARIABLE _vellum_mirror_plan_result
    OUTPUT_VARIABLE _vellum_mirror_plan_output
    ERROR_VARIABLE _vellum_mirror_plan_error)
if(NOT _vellum_mirror_plan_result EQUAL 0)
    message(FATAL_ERROR
        "Vellum: the landed mirror cannot be packaged:\n${_vellum_mirror_plan_error}")
endif()
message(STATUS "${_vellum_mirror_plan_output}")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${PROJECT_SOURCE_DIR}/tools/mirror/mirror_package.py"
    "${PROJECT_SOURCE_DIR}/provenance/mirror/receipts/index.json")
# The plan names this directory for the binary-data include root.
set(VELLUM_MIRROR_BINARY_DATA_DIR "${CMAKE_CURRENT_BINARY_DIR}/vellum-mirror-binary-data")
include("${_vellum_mirror_plan_cmake}")

# Object paths and __FILE__ must not carry the host's checkout or build path:
# the SDK is built twice in different directories and compared byte for byte.
set(_vellum_mirror_prefix_maps
    "-ffile-prefix-map=${PROJECT_SOURCE_DIR}=vellum"
    "-ffile-prefix-map=${CMAKE_BINARY_DIR}=vellum-build"
    "-ffile-prefix-map=${VELLUM_MIRROR_DEPS_DIR}=vellum-mirror-deps"
    "-ffile-prefix-map=${VELLUM_SKIA_DIR}=vellum-skia")

function(_vellum_mirror_key out value)
    string(MAKE_C_IDENTIFIER "${value}" _key)
    string(TOUPPER "${_key}" _key)
    set(${out} "${_key}" PARENT_SCOPE)
endfunction()

function(_vellum_mirror_targets_in out directory)
    get_property(_targets DIRECTORY "${directory}" PROPERTY BUILDSYSTEM_TARGETS)
    get_property(_children DIRECTORY "${directory}" PROPERTY SUBDIRECTORIES)
    foreach(_child IN LISTS _children)
        _vellum_mirror_targets_in(_child_targets "${_child}")
        list(APPEND _targets ${_child_targets})
    endforeach()
    set(${out} ${_targets} PARENT_SCOPE)
endfunction()

# Third-party link targets, built by the Vellum recipe from the pinned source.
function(_vellum_mirror_add_dependency name)
    _vellum_mirror_key(_key "${name}")
    foreach(_option IN LISTS VELLUM_MIRROR_DEP_${_key}_OPTIONS)
        string(REGEX MATCH "^([^=]+)=(.*)$" _ignored "${_option}")
        set(${CMAKE_MATCH_1} "${CMAKE_MATCH_2}" CACHE INTERNAL "" FORCE)
    endforeach()
    set(BUILD_SHARED_LIBS OFF)
    set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
    set(_binary "${CMAKE_BINARY_DIR}/_deps/vellum-mirror-build/${name}")
    add_subdirectory("${VELLUM_MIRROR_DEP_${_key}_DIR}" "${_binary}" EXCLUDE_FROM_ALL SYSTEM)
    _vellum_mirror_targets_in(_built "${VELLUM_MIRROR_DEP_${_key}_DIR}")
    foreach(_target IN LISTS _built)
        get_target_property(_type "${_target}" TYPE)
        if(_type MATCHES "^(STATIC_LIBRARY|OBJECT_LIBRARY)$")
            target_compile_options("${_target}" PRIVATE ${_vellum_mirror_prefix_maps})
            set_target_properties("${_target}" PROPERTIES POSITION_INDEPENDENT_CODE ON)
        endif()
    endforeach()
    foreach(_provided IN LISTS VELLUM_MIRROR_DEP_${_key}_PROVIDES)
        if(NOT TARGET "${_provided}")
            message(FATAL_ERROR
                "Vellum: the ${name} recipe did not produce link target ${_provided}")
        endif()
        # Appended after the dependency's directory-level flags, so a recipe
        # option such as -frtti overrides the project's own -fno-rtti.
        if(NOT MSVC)
            foreach(_option IN LISTS VELLUM_MIRROR_DEP_${_key}_CXX_OPTIONS)
                target_compile_options("${_provided}" PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:${_option}>")
            endforeach()
        endif()
    endforeach()
endfunction()

foreach(_vellum_mirror_dep IN LISTS VELLUM_MIRROR_DEPENDENCIES)
    _vellum_mirror_add_dependency("${_vellum_mirror_dep}")
endforeach()

# Generated binary data comes from the mirrored producer itself.
file(MAKE_DIRECTORY "${VELLUM_MIRROR_BINARY_DATA_DIR}")
include("${PROJECT_SOURCE_DIR}/mirror/tools/cmake/PulpEmbedData.cmake")
function(_vellum_mirror_add_binary_data name)
    _vellum_mirror_key(_key "${name}")
    set(_sources)
    foreach(_source IN LISTS VELLUM_MIRROR_BINARY_${_key}_SOURCES)
        list(APPEND _sources "${PROJECT_SOURCE_DIR}/mirror/${_source}")
    endforeach()
    # The producer writes next to CMAKE_CURRENT_BINARY_DIR; give it its own.
    set(CMAKE_CURRENT_BINARY_DIR "${VELLUM_MIRROR_BINARY_DATA_DIR}")
    cmake_language(CALL "pulp_add_binary_data" "${name}"
        SOURCES ${_sources}
        NAMESPACE "${VELLUM_MIRROR_BINARY_${_key}_NAMESPACE}")
    file(SHA256 "${VELLUM_MIRROR_BINARY_DATA_DIR}/${name}_data.hpp" _digest)
    if(NOT _digest STREQUAL VELLUM_MIRROR_BINARY_${_key}_HEADER_SHA256)
        message(FATAL_ERROR
            "Vellum: ${name}_data.hpp from the mirrored producer does not match the closure digest")
    endif()
    set_target_properties("${name}" PROPERTIES POSITION_INDEPENDENT_CODE ON)
    target_compile_options("${name}" PRIVATE ${_vellum_mirror_prefix_maps})
endfunction()
foreach(_vellum_mirror_binary IN LISTS VELLUM_MIRROR_BINARY_DATA_TARGETS)
    _vellum_mirror_add_binary_data("${_vellum_mirror_binary}")
endforeach()

# Mirrored targets, exactly as the closures describe them.
foreach(_vellum_mirror_unit IN LISTS VELLUM_MIRROR_UNITS)
    _vellum_mirror_key(_vellum_mirror_unit_key "${_vellum_mirror_unit}")
    foreach(_vellum_mirror_target IN LISTS VELLUM_MIRROR_UNIT_${_vellum_mirror_unit_key}_TARGETS)
        if(_vellum_mirror_target IN_LIST VELLUM_MIRROR_BINARY_DATA_TARGETS)
            continue()
        endif()
        _vellum_mirror_key(_tkey "${_vellum_mirror_target}")
        add_library("${_vellum_mirror_target}" STATIC
            ${VELLUM_MIRROR_TARGET_${_tkey}_SOURCES})
        set_target_properties("${_vellum_mirror_target}" PROPERTIES
            POSITION_INDEPENDENT_CODE ON
            CXX_STANDARD 20
            CXX_STANDARD_REQUIRED ON
            OBJCXX_STANDARD 20)
        target_include_directories("${_vellum_mirror_target}" PRIVATE
            ${VELLUM_MIRROR_TARGET_${_tkey}_INCLUDE_DIRS})
        target_compile_definitions("${_vellum_mirror_target}" PRIVATE
            ${VELLUM_MIRROR_TARGET_${_tkey}_DEFINITIONS})
        # Per-source options Pulp's own CMake sets on these sources (the plan
        # reads them from the mirrored CMake; source_compile_options.py).
        if(VELLUM_MIRROR_TARGET_${_tkey}_OPTION_SOURCE_COUNT GREATER 0)
            math(EXPR _vellum_option_last
                "${VELLUM_MIRROR_TARGET_${_tkey}_OPTION_SOURCE_COUNT} - 1")
            foreach(_vellum_option_index RANGE ${_vellum_option_last})
                set_source_files_properties(
                    "${VELLUM_MIRROR_TARGET_${_tkey}_OPTION_SOURCE_${_vellum_option_index}}"
                    PROPERTIES COMPILE_OPTIONS
                    "${VELLUM_MIRROR_TARGET_${_tkey}_OPTION_SOURCE_${_vellum_option_index}_OPTIONS}")
            endforeach()
        endif()
        # Vellum's host owns Dawn's process-global proc table. Pulp's
        # PULP_DAWN_PROCS_HOST_OWNED switch compiles the device library with
        # no native installer, so a mirrored GpuSurface or GpuCompute fails
        # until the host registers one through
        # pulp::render::set_dawn_proc_installer(). The sources are unchanged.
        if(_vellum_mirror_target STREQUAL "pulp-gpu-device")
            target_compile_definitions("${_vellum_mirror_target}" PRIVATE
                PULP_DAWN_PROCS_HOST_OWNED=1)
        endif()
        target_compile_options("${_vellum_mirror_target}" PRIVATE
            ${_vellum_mirror_prefix_maps})
        # Link edges are resolved once, by the unit libraries below: a static
        # target that carried them would copy another unit's objects in.
        add_dependencies("${_vellum_mirror_target}"
            ${VELLUM_MIRROR_TARGET_${_tkey}_INTERNAL_LINKS})
    endforeach()
endforeach()

# ── Package libraries ──────────────────────────────────────────────────────
# One shared library per unit, each holding every object of its mirrored
# targets, so a consumer image has exactly one definition of each mirrored
# symbol however many units it links.
set(VELLUM_MIRROR_INSTALL_DIR "mirror")
set(_vellum_mirror_unit_libraries)
foreach(_vellum_mirror_unit IN LISTS VELLUM_MIRROR_UNITS)
    _vellum_mirror_key(_ukey "${_vellum_mirror_unit}")
    set(_library "vellum-mirror-${_vellum_mirror_unit}")
    string(MAKE_C_IDENTIFIER "${_vellum_mirror_unit}" _export_suffix)
    string(SUBSTRING "${_export_suffix}" 0 1 _first)
    string(SUBSTRING "${_export_suffix}" 1 -1 _rest)
    string(TOUPPER "${_first}" _first)
    add_library("${_library}" SHARED "${PROJECT_SOURCE_DIR}/tools/mirror/unit_anchor.cpp")
    add_library("Vellum::Mirror${_first}${_rest}" ALIAS "${_library}")
    set_target_properties("${_library}" PROPERTIES
        EXPORT_NAME "Mirror${_first}${_rest}"
        MACOSX_RPATH ON
        BUILD_WITH_INSTALL_RPATH ON
        INSTALL_RPATH "@loader_path;@loader_path/../../${CMAKE_INSTALL_LIBDIR}"
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET})
    set(_whole)
    set(_external)
    set(_skia OFF)
    foreach(_target IN LISTS VELLUM_MIRROR_UNIT_${_ukey}_TARGETS)
        list(APPEND _whole "${_target}")
        _vellum_mirror_key(_tkey "${_target}")
        foreach(_link IN LISTS VELLUM_MIRROR_TARGET_${_tkey}_INTERNAL_LINKS)
            if(NOT _link IN_LIST VELLUM_MIRROR_UNIT_${_ukey}_TARGETS)
                # A mirrored target of an earlier unit: link that unit's library.
                foreach(_other IN LISTS VELLUM_MIRROR_UNITS)
                    _vellum_mirror_key(_okey "${_other}")
                    if(_link IN_LIST VELLUM_MIRROR_UNIT_${_okey}_TARGETS)
                        list(APPEND _external "vellum-mirror-${_other}")
                    endif()
                endforeach()
            endif()
        endforeach()
        foreach(_link IN LISTS VELLUM_MIRROR_TARGET_${_tkey}_EXTERNAL_LINKS)
            list(APPEND _external "${_link}")
        endforeach()
        if(VELLUM_MIRROR_TARGET_${_tkey}_SKIA)
            set(_skia ON)
        endif()
    endforeach()
    list(REMOVE_DUPLICATES _external)
    # Units link earlier units' libraries, not their objects.
    set(_unit_links)
    set(_third_party)
    foreach(_link IN LISTS _external)
        if(_link MATCHES "^vellum-mirror-")
            list(APPEND _unit_links "${_link}")
        else()
            list(APPEND _third_party "${_link}")
        endif()
    endforeach()
    # Third-party archives an earlier unit already carries are resolved
    # through that unit, never linked a second time.
    foreach(_earlier IN LISTS _unit_links)
        get_target_property(_carried "${_earlier}" VELLUM_MIRROR_CARRIED_ARCHIVES)
        if(_carried)
            list(REMOVE_ITEM _third_party ${_carried})
        endif()
    endforeach()
    # Publish the closure shape before a later Skia unit resolves an internal
    # dependency on this library.  The provider must consume the static
    # objects of earlier Skia units, rather than their shared frontends: those
    # frontends themselves link the provider and would create a shared-library
    # cycle at CMake generate time.
    set_target_properties("${_library}" PROPERTIES
        VELLUM_MIRROR_WHOLE_TARGETS "${_whole}"
        VELLUM_MIRROR_IS_SKIA "${_skia}")
    if(_skia)
        # The provider group owns every Skia-consuming mirror object. Keeping
        # these objects beside the pinned archives prevents private Skia
        # symbols from crossing a dylib boundary.
        set(_provider_whole ${_whole})
        set(_provider_unit_links)
        foreach(_provider_link IN LISTS _unit_links)
            get_target_property(_provider_link_is_skia
                "${_provider_link}" VELLUM_MIRROR_IS_SKIA)
            if(_provider_link_is_skia)
                get_target_property(_provider_link_whole
                    "${_provider_link}" VELLUM_MIRROR_WHOLE_TARGETS)
                list(APPEND _provider_whole ${_provider_link_whole})
            else()
                list(APPEND _provider_unit_links "${_provider_link}")
            endif()
        endforeach()
        list(REMOVE_DUPLICATES _provider_whole)
        list(REMOVE_DUPLICATES _provider_unit_links)
        target_link_libraries(vellum-skia-dawn-provider PRIVATE
            "$<LINK_LIBRARY:WHOLE_ARCHIVE,${_provider_whole}>")
        if(_provider_unit_links)
            target_link_libraries(vellum-skia-dawn-provider PRIVATE
                ${_provider_unit_links})
        endif()
        target_link_libraries(vellum-skia-dawn-provider PRIVATE
            ${_third_party}
            "-framework Accelerate"
            "-framework AppKit"
            "-framework CoreServices"
            "-framework Security"
            "-framework SystemConfiguration"
            "-framework UniformTypeIdentifiers"
            "-framework UserNotifications"
            bsm)
        # Retain a concrete provider binding in this otherwise empty frontend.
        # Loading the frontend must also load its re-exported provider.
        target_sources("${_library}" PRIVATE
            "${PROJECT_SOURCE_DIR}/tools/mirror/canvas_provider_anchor.cpp")
        target_include_directories("${_library}" PRIVATE
            "${PROJECT_SOURCE_DIR}/mirror/core/canvas/include")
        target_compile_features("${_library}" PRIVATE cxx_std_20)
        # MirrorCanvas forwards its public mirrored symbols from the shared
        # provider while retaining a standalone direct-link contract.
        target_link_libraries("${_library}" PUBLIC
            Vellum::SkiaDawnProvider ${_unit_links})
        target_link_options("${_library}" PRIVATE
            "LINKER:-reexport_library,$<TARGET_FILE:vellum-skia-dawn-provider>")
    else()
        target_link_libraries("${_library}" PRIVATE
            "$<LINK_LIBRARY:WHOLE_ARCHIVE,${_whole}>"
            ${_third_party})
        if(_unit_links)
            target_link_libraries("${_library}" PUBLIC ${_unit_links})
        endif()
    endif()
    set_target_properties("${_library}" PROPERTIES
        VELLUM_MIRROR_CARRIED_ARCHIVES "${_third_party}")
    target_link_libraries("${_library}" PRIVATE
        "-framework Accelerate"
        "-framework AppKit"
        "-framework CoreFoundation"
        "-framework CoreGraphics"
        "-framework CoreServices"
        "-framework CoreText"
        "-framework Foundation"
        "-framework IOKit"
        "-framework Security"
        "-framework SystemConfiguration"
        "-framework UniformTypeIdentifiers"
        "-framework UserNotifications"
        bsm)
    if(_skia)
        # Export only the mirrored namespaces this unit defines.
        set(_exports "${CMAKE_CURRENT_BINARY_DIR}/vellum-mirror-${_vellum_mirror_unit}.exports")
        file(WRITE "${_exports}"
            "__ZN4pulp*\n__ZNK4pulp*\n__ZTVN4pulp*\n__ZTIN4pulp*\n__ZTSN4pulp*\n"
            "__ZZN4pulp*\n__ZZNK4pulp*\n__ZGVZN4pulp*\n__ZGVZNK4pulp*\n__ZTHN4pulp*\n__ZTWN4pulp*\n")
        foreach(_binary IN LISTS VELLUM_MIRROR_BINARY_DATA_TARGETS)
            _vellum_mirror_key(_bkey "${_binary}")
            string(LENGTH "${VELLUM_MIRROR_BINARY_${_bkey}_NAMESPACE}" _length)
            file(APPEND "${_exports}"
                "__ZN${_length}${VELLUM_MIRROR_BINARY_${_bkey}_NAMESPACE}*\n")
        endforeach()
        target_link_options("${_library}" PRIVATE
            "LINKER:-exported_symbols_list,${_exports}")
        set_property(TARGET "${_library}" APPEND PROPERTY LINK_DEPENDS "${_exports}")
    endif()
    target_link_options("${_library}" PRIVATE "LINKER:-dead_strip_dylibs")
    target_compile_definitions("${_library}" INTERFACE
        ${VELLUM_MIRROR_UNIT_${_ukey}_DEFINITIONS})
    target_compile_features("${_library}" INTERFACE cxx_std_20)
    target_include_directories("${_library}" INTERFACE
        "$<INSTALL_INTERFACE:${VELLUM_MIRROR_INSTALL_DIR}/include>")
    list(APPEND _vellum_mirror_unit_libraries "${_library}")
endforeach()

# Headers: each mirrored unit's public include roots, the rendered build
# info, and the closure-listed pinned headers those public headers include.
foreach(_vellum_mirror_unit IN LISTS VELLUM_MIRROR_UNITS)
    _vellum_mirror_key(_ukey "${_vellum_mirror_unit}")
    foreach(_target IN LISTS VELLUM_MIRROR_UNIT_${_ukey}_TARGETS)
        _vellum_mirror_key(_tkey "${_target}")
        foreach(_include IN LISTS VELLUM_MIRROR_TARGET_${_tkey}_INCLUDE_DIRS)
            if(_include MATCHES "^${PROJECT_SOURCE_DIR}/mirror/core/[^/]+/include$")
                list(APPEND _vellum_mirror_public_includes "${_include}")
            endif()
        endforeach()
    endforeach()
endforeach()
list(REMOVE_DUPLICATES _vellum_mirror_public_includes)
foreach(_include IN LISTS _vellum_mirror_public_includes)
    install(DIRECTORY "${_include}/"
        DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/include"
        PATTERN "*.in" EXCLUDE)
endforeach()
install(DIRECTORY "${VELLUM_MIRROR_BUILD_INFO_DIR}/"
    DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/include")
foreach(_header IN LISTS VELLUM_MIRROR_CHOC_HEADERS)
    get_filename_component(_directory "${_header}" DIRECTORY)
    install(FILES "${VELLUM_MIRROR_CHOC_ROOT}/${_header}"
        DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/include/${_directory}")
endforeach()

install(TARGETS ${_vellum_mirror_unit_libraries}
    EXPORT VellumMirrorTargets
    LIBRARY DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/lib")
install(EXPORT VellumMirrorTargets
    NAMESPACE Vellum::
    DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/lib/cmake/VellumMirror")

# Provenance: the receipt and closures the package was built from, plus the
# build plan's verified identity.
install(FILES "${PROJECT_SOURCE_DIR}/${VELLUM_MIRROR_RECEIPT}"
    DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/share/provenance")
foreach(_closure IN LISTS VELLUM_MIRROR_CLOSURES)
    install(FILES "${PROJECT_SOURCE_DIR}/${_closure}"
        DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/share/provenance/closures")
endforeach()
foreach(_dependency IN LISTS VELLUM_MIRROR_LICENSED_DEPENDENCIES)
    _vellum_mirror_key(_key "${_dependency}")
    install(FILES ${VELLUM_MIRROR_LICENSE_${_key}_FILES}
        DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/share/licenses/${_dependency}")
endforeach()
set(VELLUM_PACKAGE_HAS_MIRROR ON)
