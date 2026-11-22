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

# macOS plans from the export receipt's Darwin arm64 closures. Every other
# platform, the other Apple systems included, plans from its own closure record
# (tools/mirror/platform_closures.py) and its renderer lock entry;
# mirror_package.py refuses a platform without a verified record, so a platform
# is enabled only by landing one.
include("${CMAKE_CURRENT_LIST_DIR}/VellumMirrorPlatform.cmake")
vellum_mirror_platform_args(_vellum_mirror_platform_args)
if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
    if(NOT VELLUM_HAS_SKIA_DAWN)
        message(FATAL_ERROR
            "Vellum: the landed mirror was exported from the Darwin arm64 Skia/Dawn "
            "configuration; VELLUM_ENABLE_MIRROR needs the locked Skia/Dawn archive")
    endif()
elseif(NOT VELLUM_HAS_SKIA_DAWN)
    message(FATAL_ERROR
        "Vellum: VELLUM_ENABLE_MIRROR on ${CMAKE_SYSTEM_NAME} ${CMAKE_SYSTEM_PROCESSOR} needs a "
        "verified platform closure record and a renderer lock entry for this platform")
endif()
if(NOT CMAKE_BUILD_TYPE STREQUAL "Release")
    message(FATAL_ERROR
        "Vellum: the landed mirror's closures record a Release configuration; "
        "configure with -DCMAKE_BUILD_TYPE=Release")
endif()
enable_language(C)
if(APPLE)
    enable_language(OBJCXX)
endif()
find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)

# Every installed mirror closure gets a short identity. It is derived from the
# source revision, the locked renderer and optional V8 inputs, and the ABI
# switches that change compiled interfaces. Target names stay stable so the
# package export and Pulp staging continue to use the same CMake targets.
execute_process(
    COMMAND git -C "${PROJECT_SOURCE_DIR}" rev-parse HEAD
    OUTPUT_VARIABLE _vellum_runtime_source_revision
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE _vellum_runtime_source_result)
# CMake regular expressions have no {n,m} interval, so the length is checked apart.
string(LENGTH "${_vellum_runtime_source_revision}" _vellum_runtime_source_length)
if(NOT _vellum_runtime_source_result EQUAL 0 OR
   NOT _vellum_runtime_source_revision MATCHES "^[0-9a-fA-F]+$" OR
   _vellum_runtime_source_length LESS 7 OR _vellum_runtime_source_length GREATER 64)
    message(FATAL_ERROR "Vellum: runtime identity needs an authoritative source revision")
endif()
file(SHA256 "${PROJECT_SOURCE_DIR}/gpu-runtime/renderer-lock.json" _vellum_runtime_renderer_pin)
file(SHA256 "${PROJECT_SOURCE_DIR}/provenance/third-party-lock.json" _vellum_runtime_dependency_pin)
set(_vellum_runtime_abi_inputs
    "mirror=${VELLUM_ENABLE_MIRROR}"
    "v8=${VELLUM_ENABLE_V8}"
    "tracing=${VELLUM_ENABLE_TRACING}"
    "graphics=${VELLUM_ENABLE_GRAPHICS}"
    "gpu=${VELLUM_ENABLE_GPU}"
    "asan=${VELLUM_ENABLE_ADDRESS_SANITIZER}"
    "renderer=${_vellum_runtime_renderer_pin}"
    "dependencies=${_vellum_runtime_dependency_pin}")
string(JOIN "|" _vellum_runtime_identity_seed
    "source=${_vellum_runtime_source_revision}" ${_vellum_runtime_abi_inputs})
string(SHA256 _vellum_runtime_identity_hash "${_vellum_runtime_identity_seed}")
string(SUBSTRING "${_vellum_runtime_identity_hash}" 0 12 VELLUM_RUNTIME_IDENTITY_TAG)
set(VELLUM_RUNTIME_IDENTITY_TAG "${VELLUM_RUNTIME_IDENTITY_TAG}" CACHE INTERNAL
    "Identity of the Vellum runtime closure" FORCE)
set(VELLUM_RUNTIME_IDENTITY_SUFFIX "-${VELLUM_RUNTIME_IDENTITY_TAG}")
set(VELLUM_RUNTIME_OBJC_SUFFIX "_${VELLUM_RUNTIME_IDENTITY_TAG}")
message(STATUS "Vellum: runtime closure identity ${VELLUM_RUNTIME_IDENTITY_TAG}")

set(VELLUM_MIRROR_DEPS_DIR "${CMAKE_BINARY_DIR}/_deps/vellum-mirror" CACHE PATH
    "Where the mirror's pinned dependency sources are fetched")
set(_vellum_mirror_generated "${CMAKE_BINARY_DIR}/generated/vellum-mirror")
set(_vellum_mirror_plan_cmake "${_vellum_mirror_generated}/plan.cmake")
set(_vellum_mirror_plan_json "${_vellum_mirror_generated}/plan.json")
# The in-tree gpu-runtime's configuration, so the plan can refuse a closure
# exported against a different GPU_RUNTIME_* interface.
set(_vellum_mirror_gpu_runtime_definitions)
foreach(_vellum_gpu_runtime_target IN ITEMS gpu-runtime-device-api gpu-runtime-bootstrap
        gpu-runtime-device gpu-runtime-skia-log-bridge)
    if(TARGET "${_vellum_gpu_runtime_target}")
        get_target_property(_vellum_gpu_runtime_defs "${_vellum_gpu_runtime_target}"
            INTERFACE_COMPILE_DEFINITIONS)
        if(_vellum_gpu_runtime_defs)
            list(APPEND _vellum_mirror_gpu_runtime_definitions ${_vellum_gpu_runtime_defs})
        endif()
    endif()
endforeach()
list(REMOVE_DUPLICATES _vellum_mirror_gpu_runtime_definitions)
list(JOIN _vellum_mirror_gpu_runtime_definitions "|"
    _vellum_mirror_gpu_runtime_definitions_arg)
execute_process(
    COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tools/mirror/mirror_package.py"
        "--in-tree-definitions=vellum-gpu-runtime=${_vellum_mirror_gpu_runtime_definitions_arg}"
        --repo "${PROJECT_SOURCE_DIR}"
        ${_vellum_mirror_platform_args}
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
    "${PROJECT_SOURCE_DIR}/provenance/mirror/receipts/index.json"
    "${PROJECT_SOURCE_DIR}/tools/mirror/framework_ports.py"
    "${PROJECT_SOURCE_DIR}/tools/mirror/gpu_surface_guard.py"
    "${PROJECT_SOURCE_DIR}/tools/mirror/platform_closures.py"
    "${PROJECT_SOURCE_DIR}/tools/mirror/port_respell.py"
    "${PROJECT_SOURCE_DIR}/tools/mirror/source_compile_options.py"
    "${PROJECT_SOURCE_DIR}/tools/mirror/verify_mirror_rename_identity.py"
    "${PROJECT_SOURCE_DIR}/tools/mirror/VellumMirrorPlatform.cmake"
    "${PROJECT_SOURCE_DIR}/provenance/framework/transfer.json"
    # Its mtime moves when a manifest below is added, which an absent file cannot signal.
    "${PROJECT_SOURCE_DIR}/provenance/framework")
# Planning reads the platform index, each platform's record and its closure
# manifests; editing any of them must retrigger mirror_package.py.
file(GLOB_RECURSE _vellum_mirror_platform_records CONFIGURE_DEPENDS
    "${PROJECT_SOURCE_DIR}/provenance/framework/platforms/*.json")
foreach(_vellum_mirror_platform_record IN LISTS _vellum_mirror_platform_records)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${_vellum_mirror_platform_record}")
endforeach()
# The ports ledger and the unit manifest decide what is compiled. The respell
# rule and its table change the bytes of framework/ sources, so they
# retrigger configure too.
foreach(_vellum_mirror_manifest IN ITEMS provenance/framework/ports.json provenance/framework/units.json
        provenance/framework/respell.json)
    if(EXISTS "${PROJECT_SOURCE_DIR}/${_vellum_mirror_manifest}")
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
            "${PROJECT_SOURCE_DIR}/${_vellum_mirror_manifest}")
    endif()
endforeach()
# The plan names this directory for the binary-data include root.
set(VELLUM_MIRROR_BINARY_DATA_DIR "${CMAKE_CURRENT_BINARY_DIR}/vellum-mirror-binary-data")
include("${_vellum_mirror_plan_cmake}")

# Object paths and __FILE__ must not carry the host's checkout or build path:
# the SDK is built twice in different directories and compared byte for byte.
vellum_mirror_prefix_map_options(_vellum_mirror_prefix_maps
    "${PROJECT_SOURCE_DIR}=vellum"
    "${CMAKE_BINARY_DIR}=vellum-build"
    "${VELLUM_MIRROR_DEPS_DIR}=vellum-mirror-deps"
    "${VELLUM_SKIA_DIR}=vellum-skia")

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
    # The recipe's options are cache entries, so they outlive the dependency's
    # directory. Record each entry's prior state and restore it after the
    # dependency is added: a recipe's BUILD_TESTING=OFF must not switch off
    # Vellum's own tests, which read the same variable.
    set(_vellum_mirror_option_names)
    foreach(_option IN LISTS VELLUM_MIRROR_DEP_${_key}_OPTIONS)
        string(REGEX MATCH "^([^=]+)=(.*)$" _ignored "${_option}")
        set(_name "${CMAKE_MATCH_1}")
        list(APPEND _vellum_mirror_option_names "${_name}")
        if(DEFINED CACHE{${_name}})
            set(_vellum_mirror_saved_${_name} "$CACHE{${_name}}")
            get_property(_vellum_mirror_saved_type_${_name} CACHE "${_name}" PROPERTY TYPE)
        else()
            unset(_vellum_mirror_saved_${_name})
        endif()
        set(${_name} "${CMAKE_MATCH_2}" CACHE INTERNAL "" FORCE)
    endforeach()
    set(BUILD_SHARED_LIBS OFF)
    set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
    set(_binary "${CMAKE_BINARY_DIR}/_deps/vellum-mirror-build/${name}")
    add_subdirectory("${VELLUM_MIRROR_DEP_${_key}_DIR}" "${_binary}" EXCLUDE_FROM_ALL SYSTEM)
    _vellum_mirror_targets_in(_dep_targets "${VELLUM_MIRROR_DEP_${_key}_DIR}")
    foreach(_dep_target IN LISTS _dep_targets)
        get_target_property(_dep_type "${_dep_target}" TYPE)
        if(_dep_type STREQUAL "STATIC_LIBRARY")
            set_property(GLOBAL APPEND PROPERTY VELLUM_MIRROR_DEP_ARCHIVES "${_dep_target}")
        endif()
    endforeach()
    foreach(_name IN LISTS _vellum_mirror_option_names)
        if(DEFINED _vellum_mirror_saved_${_name})
            set(${_name} "${_vellum_mirror_saved_${_name}}"
                CACHE ${_vellum_mirror_saved_type_${_name}} "" FORCE)
        else()
            unset(${_name} CACHE)
        endif()
    endforeach()
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
        if(_key STREQUAL "SDL3_STATIC" AND APPLE)
            foreach(_sdl_class IN ITEMS
                    Cocoa_PasteboardDataProvider SDL3AppDelegate SDL3Application
                    SDL3Cocoa_WindowListener SDL3METAL_PaletteData SDL3METAL_RenderData
                    SDL3METAL_TextureData SDL3MessageBoxPresenter SDL3OpenGLContext
                    SDL3TranslatorResponder SDL3View SDL3Window SDL3_RumbleContext
                    SDL3_RumbleMotor SDL3_cocoametalview
                    SDLCaptureVideoDataOutputSampleBufferDelegate SDLPrivateCameraData
                    SDL_CocoaVideoData SDL_CocoaWindowData)
                target_compile_definitions("${_provided}" PRIVATE
                    "${_sdl_class}=${_sdl_class}_${VELLUM_RUNTIME_IDENTITY_TAG}")
            endforeach()
        endif()
    endforeach()
endfunction()

foreach(_vellum_mirror_dep IN LISTS VELLUM_MIRROR_DEPENDENCIES)
    _vellum_mirror_add_dependency("${_vellum_mirror_dep}")
endforeach()

# Generated binary data comes from the mirrored producer itself.
file(MAKE_DIRECTORY "${VELLUM_MIRROR_BINARY_DATA_DIR}")
include("${VELLUM_MIRROR_SOURCE_DIR}/tools/cmake/PulpEmbedData.cmake")
function(_vellum_mirror_add_binary_data name)
    _vellum_mirror_key(_key "${name}")
    set(_sources)
    foreach(_source IN LISTS VELLUM_MIRROR_BINARY_${_key}_SOURCES)
        list(APPEND _sources "${VELLUM_MIRROR_SOURCE_DIR}/${_source}")
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

# A target declared in provenance/framework/units.json names third-party
# recipe targets in its links but, unlike a closure target, has no recorded
# include directories for them: it compiles with their usage requirements.
function(_vellum_mirror_declared_recipe_usage target)
    _vellum_mirror_key(_tkey "${target}")
    if(NOT VELLUM_MIRROR_TARGET_${_tkey}_DECLARED)
        return()
    endif()
    foreach(_link IN LISTS VELLUM_MIRROR_TARGET_${_tkey}_EXTERNAL_LINKS)
        target_include_directories("${target}" PRIVATE
            "$<TARGET_PROPERTY:${_link},INTERFACE_INCLUDE_DIRECTORIES>")
        target_compile_definitions("${target}" PRIVATE
            "$<TARGET_PROPERTY:${_link},INTERFACE_COMPILE_DEFINITIONS>")
    endforeach()
endfunction()

# Names the mirror images must not export. Third-party code is linked normally
# (one image may need another's copy of nothing: each image carries its own),
# and every symbol the recipe's third-party archives define is left unexported
# at link time, so a mirror dylib exports the vellum:: namespace and the
# documented C entry points and a Pulp build that defines the same names cannot
# interpose or coalesce them. The list is generated from the archives
# (tools/mirror/third_party_unexports.py), so a dependency bump needs no edit;
# tools/mirror/third_party_unexports.txt adds names from the mirror's own
# vendored objects, which are not archives of their own.
get_property(_vellum_dep_archives GLOBAL PROPERTY VELLUM_MIRROR_DEP_ARCHIVES)
list(REMOVE_DUPLICATES _vellum_dep_archives)
set(_vellum_dep_archive_files)
foreach(_archive IN LISTS _vellum_dep_archives)
    list(APPEND _vellum_dep_archive_files "$<TARGET_FILE:${_archive}>")
endforeach()
set(VELLUM_MIRROR_UNEXPORTS "${CMAKE_CURRENT_BINARY_DIR}/vellum-mirror-third-party.unexports")
add_custom_command(OUTPUT "${VELLUM_MIRROR_UNEXPORTS}"
    COMMAND "${Python3_EXECUTABLE}" -I "${PROJECT_SOURCE_DIR}/tools/mirror/third_party_unexports.py"
        --extra "${PROJECT_SOURCE_DIR}/tools/mirror/third_party_unexports.txt"
        --out "${VELLUM_MIRROR_UNEXPORTS}" ${_vellum_dep_archive_files}
    DEPENDS ${_vellum_dep_archives}
        "${PROJECT_SOURCE_DIR}/tools/mirror/third_party_unexports.py"
        "${PROJECT_SOURCE_DIR}/tools/mirror/third_party_unexports.txt"
    COMMAND_EXPAND_LISTS
    VERBATIM)
add_custom_target(vellum-mirror-third-party-unexports DEPENDS "${VELLUM_MIRROR_UNEXPORTS}")

# VELLUM_ENABLE_TRACING: the switch the framework's trace header reads rides on
# the same compile usage every mirrored target and framework test takes.
if(VELLUM_ENABLE_TRACING)
    target_compile_definitions(vellum-tracing INTERFACE PULP_TRACING_ENABLED=1)
    # Perfetto's objects reach the foundation library, which must not carry the
    # build host's paths either.
    target_compile_options(vellum-perfetto PRIVATE ${_vellum_mirror_prefix_maps})
endif()

# Mirrored targets, exactly as the closures and units.json describe them.
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
        # Vellum's value model (<vellum/value/...>) is a Vellum-native header
        # dependency of the framework's parameter bindings, which Pulp's
        # closures cannot name. Headers only: no object of Vellum::Value is
        # compiled into or linked by a mirrored target.
        target_include_directories("${_vellum_mirror_target}" PRIVATE
            "$<TARGET_PROPERTY:vellum-value,INTERFACE_INCLUDE_DIRECTORIES>")
        target_compile_definitions("${_vellum_mirror_target}" PRIVATE
            ${VELLUM_MIRROR_TARGET_${_tkey}_DEFINITIONS})
        # The mirror's own build is the one place the GPU-surface guard of the
        # installed choke headers is lifted (gpu_surface_guard.py). PRIVATE,
        # so it never reaches an installed consumer.
        target_compile_definitions("${_vellum_mirror_target}" PRIVATE VELLUM_MIRROR_BUILD=1)
        if(APPLE)
            target_compile_definitions("${_vellum_mirror_target}" PRIVATE
                PULP_VIEW_OBJC_SUFFIX=${VELLUM_RUNTIME_OBJC_SUFFIX}
                VellumMirrorEnvironmentObserver=VellumMirrorEnvironmentObserver_${VELLUM_RUNTIME_IDENTITY_TAG})
        endif()
        # VELLUM_ENABLE_TRACING: every mirrored target compiles with the same
        # trace switch and Perfetto header, so no two objects disagree on
        # kTracingEnabled. Compile usage only; the one Perfetto library is
        # linked into the foundation unit below.
        if(VELLUM_ENABLE_TRACING)
            target_link_libraries("${_vellum_mirror_target}" PRIVATE vellum-tracing)
        endif()
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
        # A target that links an in-tree dependency compiles against its
        # interface target, so the in-tree INTERFACE definitions (the ones the
        # plan compared with the closure) apply to the mirrored sources.
        foreach(_vellum_interface IN LISTS VELLUM_MIRROR_TARGET_${_tkey}_IN_TREE_INTERFACES)
            target_link_libraries("${_vellum_mirror_target}" PRIVATE "${_vellum_interface}")
        endforeach()
        # Vellum's host owns Dawn's process-global proc table. Pulp's
        # PULP_DAWN_PROCS_HOST_OWNED switch compiles the device library with
        # no native installer, so a mirrored GpuSurface or GpuCompute fails
        # until the host registers one through
        # pulp::render::set_dawn_proc_installer(). The sources are unchanged.
        # A receipt whose pulp-gpu-device forwards to gpu-runtime compiles no
        # installer at all, and gpu-runtime's own build carries Vellum's
        # host-owned setting, so the switch applies only to the older receipts.
        if(_vellum_mirror_target STREQUAL "pulp-gpu-device"
           AND NOT VELLUM_MIRROR_TARGET_${_tkey}_IN_TREE_INTERFACES)
            target_compile_definitions("${_vellum_mirror_target}" PRIVATE
                PULP_DAWN_PROCS_HOST_OWNED=1)
        endif()
        target_compile_options("${_vellum_mirror_target}" PRIVATE
            ${_vellum_mirror_prefix_maps})
        _vellum_mirror_declared_recipe_usage("${_vellum_mirror_target}")
        # Link edges are resolved once, by the unit libraries below: a static
        # target that carried them would copy another unit's objects in.
        add_dependencies("${_vellum_mirror_target}"
            ${VELLUM_MIRROR_TARGET_${_tkey}_INTERNAL_LINKS})
    endforeach()
endforeach()

# Vellum owns the installed GPU plug-in editor host.  It is deliberately an
# addition to the mirrored view-core package, not a copied Pulp-kept source:
# the public header is mirrored, while this implementation is authored here.
if(TARGET pulp-view-core AND APPLE)
    target_sources(pulp-view-core PRIVATE
        "${PROJECT_SOURCE_DIR}/framework/core/view/src/plugin_view_host.cpp"
        "${PROJECT_SOURCE_DIR}/framework/core/view/platform/mac/gpu_plugin_editor_host_mac.mm"
        "${PROJECT_SOURCE_DIR}/framework/core/view/platform/mac/window_host_mac_metal_view.mm")
    target_include_directories(pulp-view-core PRIVATE
        "${PROJECT_SOURCE_DIR}/framework/core/view/include")
endif()

# Windows owns the HWND child editor host. Its Dawn surface is created after
# attachment, when the child has its final parent and DPI context.
if(TARGET pulp-view-core AND WIN32)
    target_sources(pulp-view-core PRIVATE
        "${PROJECT_SOURCE_DIR}/framework/core/view/src/plugin_view_host.cpp"
        "${PROJECT_SOURCE_DIR}/framework/core/view/platform/win/gpu_plugin_editor_host_win.cpp")
    target_include_directories(pulp-view-core PRIVATE
        "${PROJECT_SOURCE_DIR}/framework/core/view/include")
endif()

if(TARGET vellum-skia-dawn-provider)
    set_target_properties(vellum-skia-dawn-provider PROPERTIES
        OUTPUT_NAME "vellum-skia-dawn-provider${VELLUM_RUNTIME_IDENTITY_SUFFIX}")
    target_compile_definitions(vellum-skia-dawn-provider PRIVATE
        PULP_VIEW_OBJC_SUFFIX=${VELLUM_RUNTIME_OBJC_SUFFIX}
        VellumMirrorEnvironmentObserver=VellumMirrorEnvironmentObserver_${VELLUM_RUNTIME_IDENTITY_TAG})
endif()

# Optional V8 (VELLUM_ENABLE_V8). The closures build view-script with QuickJS
# only, so its V8 backend compiles as a stub. This is the one native addition
# to that unit's compile: the backend's PULP_HAS_V8 switch, the provider
# identity the backend reports, and the sealed provider's consumer_defines,
# which arrive through the imported target and are part of V8's ABI. The
# unit's objects live in vellum-skia-dawn-provider, which links libv8.dylib
# and finds it beside itself once installed.
set(VELLUM_MIRROR_HAS_V8 OFF)
if(VELLUM_ENABLE_V8)
    if(NOT TARGET pulp-view-script)
        message(FATAL_ERROR "Vellum: VELLUM_ENABLE_V8 needs the mirror's view-script unit")
    endif()
    include("${VELLUM_MIRROR_SOURCE_DIR}/tools/cmake/VellumV8Provider.cmake")
    vellum_v8_resolve_provider(
        LOCK "${PROJECT_SOURCE_DIR}/provenance/third-party-lock.json"
        RENDERER_LOCK "${PROJECT_SOURCE_DIR}/gpu-runtime/renderer-lock.json"
        WORK_DIR "${CMAKE_BINARY_DIR}/_deps/vellum-v8"
        ARCHIVE "${VELLUM_V8_ARCHIVE}")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${PROJECT_SOURCE_DIR}/provenance/third-party-lock.json")
    target_link_libraries(pulp-view-script PRIVATE vellum-v8)
    target_compile_definitions(pulp-view-script PRIVATE
        PULP_HAS_V8=1
        PULP_V8_PROVIDER_KIND="v8builder"
        PULP_V8_PROVIDER_PATH="@rpath/libv8.dylib"
        PULP_V8_EXPECTED_RUNTIME_VERSION="${VELLUM_V8_VERSION}")
    target_link_libraries(vellum-skia-dawn-provider PRIVATE vellum-v8)
    install(FILES "${VELLUM_V8_LIBRARY}"
        DESTINATION "${CMAKE_INSTALL_LIBDIR}"
        PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE
                    GROUP_READ GROUP_EXECUTE WORLD_READ WORLD_EXECUTE)
    install(FILES "${VELLUM_V8_MANIFEST}"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/vellum/v8")
    set(VELLUM_MIRROR_HAS_V8 ON)
endif()

# A mirrored source can include Vellum's value model: one probe compiled with
# view-core's own include directories and definitions. It fails to compile when
# the value-model include edge above is missing.
if(TARGET pulp-view-core)
    add_library(vellum-mirror-value-include-probe OBJECT
        "${PROJECT_SOURCE_DIR}/tools/mirror/value_include_probe.cpp")
    set_target_properties(vellum-mirror-value-include-probe PROPERTIES
        CXX_STANDARD 20
        CXX_STANDARD_REQUIRED ON)
    target_include_directories(vellum-mirror-value-include-probe PRIVATE
        "$<TARGET_PROPERTY:pulp-view-core,INCLUDE_DIRECTORIES>")
    target_compile_definitions(vellum-mirror-value-include-probe PRIVATE
        "$<TARGET_PROPERTY:pulp-view-core,COMPILE_DEFINITIONS>")
endif()

# Everything below packages and links with Apple-only rules.
vellum_mirror_require_packaging()

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
        OUTPUT_NAME "vellum-mirror-${_vellum_mirror_unit}${VELLUM_RUNTIME_IDENTITY_SUFFIX}"
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
    # Each image links its own copy of the third-party archives it needs: their
    # symbols are unexported, so no image can resolve them from another.
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
        target_link_options(vellum-skia-dawn-provider PRIVATE
            "LINKER:-unexported_symbols_list,${VELLUM_MIRROR_UNEXPORTS}")
        set_property(TARGET vellum-skia-dawn-provider APPEND PROPERTY LINK_DEPENDS
            "${VELLUM_MIRROR_UNEXPORTS}")
        add_dependencies(vellum-skia-dawn-provider vellum-mirror-third-party-unexports)
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
            "${VELLUM_MIRROR_SOURCE_DIR}/core/canvas/include")
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
    # VELLUM_ENABLE_TRACING: the runtime target holds the Perfetto track-event
    # storage, so its unit's library carries the one Perfetto in the process.
    # Later units reach it through their link on this library.
    if(VELLUM_ENABLE_TRACING AND "pulp-runtime" IN_LIST _whole)
        target_link_libraries("${_library}" PRIVATE
            "$<LINK_LIBRARY:WHOLE_ARCHIVE,vellum-perfetto>")
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
        # The units' root namespace comes from the plan: pulp before the
        # framework transfer, vellum after it.
        string(LENGTH "${VELLUM_MIRROR_ROOT_NAMESPACE}" _root_length)
        set(_root "${_root_length}${VELLUM_MIRROR_ROOT_NAMESPACE}")
        file(WRITE "${_exports}"
            "__ZN${_root}*\n__ZNK${_root}*\n__ZTVN${_root}*\n__ZTIN${_root}*\n__ZTSN${_root}*\n"
            "__ZZN${_root}*\n__ZZNK${_root}*\n__ZGVZN${_root}*\n__ZGVZNK${_root}*\n__ZTHN${_root}*\n__ZTWN${_root}*\n")
        foreach(_binary IN LISTS VELLUM_MIRROR_BINARY_DATA_TARGETS)
            _vellum_mirror_key(_bkey "${_binary}")
            string(LENGTH "${VELLUM_MIRROR_BINARY_${_bkey}_NAMESPACE}" _length)
            file(APPEND "${_exports}"
                "__ZN${_length}${VELLUM_MIRROR_BINARY_${_bkey}_NAMESPACE}*\n")
        endforeach()
        target_link_options("${_library}" PRIVATE
            "LINKER:-exported_symbols_list,${_exports}")
        set_property(TARGET "${_library}" APPEND PROPERTY LINK_DEPENDS "${_exports}")
    else()
        # ld takes an export list or an unexport list, not both: the units
        # that have no export list leave the third-party names out.
        target_link_options("${_library}" PRIVATE
            "LINKER:-unexported_symbols_list,${VELLUM_MIRROR_UNEXPORTS}")
        set_property(TARGET "${_library}" APPEND PROPERTY LINK_DEPENDS
            "${VELLUM_MIRROR_UNEXPORTS}")
        add_dependencies("${_library}" vellum-mirror-third-party-unexports)
    endif()
    target_link_options("${_library}" PRIVATE "LINKER:-dead_strip_dylibs")
    target_compile_definitions("${_library}" INTERFACE
        ${VELLUM_MIRROR_UNIT_${_ukey}_DEFINITIONS})
    target_compile_features("${_library}" INTERFACE cxx_std_20)
    target_include_directories("${_library}" INTERFACE
        "$<INSTALL_INTERFACE:${VELLUM_MIRROR_INSTALL_DIR}/include>")
    # A unit's public headers include <vellum/value/...>, so its consumers
    # compile and link against Vellum's value model.
    target_link_libraries("${_library}" INTERFACE Vellum::Value)
    list(APPEND _vellum_mirror_unit_libraries "${_library}")
endforeach()

# Executables declared in provenance/framework/units.json. Each links the
# unit libraries that carry the mirrored targets it names, so it holds one
# copy of every mirrored symbol, as an installed consumer would. They are
# built for tests and tools and are not installed.
foreach(_vellum_mirror_executable IN LISTS VELLUM_MIRROR_EXECUTABLES)
    _vellum_mirror_key(_tkey "${_vellum_mirror_executable}")
    add_executable("${_vellum_mirror_executable}" ${VELLUM_MIRROR_TARGET_${_tkey}_SOURCES})
    set_target_properties("${_vellum_mirror_executable}" PROPERTIES
        CXX_STANDARD 20
        CXX_STANDARD_REQUIRED ON
        OBJCXX_STANDARD 20
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
        BUILD_RPATH "$<TARGET_FILE_DIR:vellum-skia-dawn-provider>")
    target_include_directories("${_vellum_mirror_executable}" PRIVATE
        ${VELLUM_MIRROR_TARGET_${_tkey}_INCLUDE_DIRS}
        "$<TARGET_PROPERTY:vellum-value,INTERFACE_INCLUDE_DIRECTORIES>")
    target_compile_definitions("${_vellum_mirror_executable}" PRIVATE
        ${VELLUM_MIRROR_TARGET_${_tkey}_DEFINITIONS} VELLUM_MIRROR_BUILD=1)
    target_compile_options("${_vellum_mirror_executable}" PRIVATE ${_vellum_mirror_prefix_maps})
    if(VELLUM_MIRROR_TARGET_${_tkey}_OPTION_SOURCE_COUNT GREATER 0)
        math(EXPR _vellum_option_last "${VELLUM_MIRROR_TARGET_${_tkey}_OPTION_SOURCE_COUNT} - 1")
        foreach(_vellum_option_index RANGE ${_vellum_option_last})
            set_source_files_properties(
                "${VELLUM_MIRROR_TARGET_${_tkey}_OPTION_SOURCE_${_vellum_option_index}}"
                PROPERTIES COMPILE_OPTIONS
                "${VELLUM_MIRROR_TARGET_${_tkey}_OPTION_SOURCE_${_vellum_option_index}_OPTIONS}")
        endforeach()
    endif()
    _vellum_mirror_declared_recipe_usage("${_vellum_mirror_executable}")
    set(_vellum_executable_units)
    foreach(_link IN LISTS VELLUM_MIRROR_TARGET_${_tkey}_INTERNAL_LINKS)
        _vellum_mirror_key(_lkey "${_link}")
        list(APPEND _vellum_executable_units "vellum-mirror-${VELLUM_MIRROR_TARGET_${_lkey}_UNIT}")
    endforeach()
    list(REMOVE_DUPLICATES _vellum_executable_units)
    # The unit libraries do not export their third-party code, so an executable
    # links its own copy.
    set(_vellum_executable_third_party ${VELLUM_MIRROR_TARGET_${_tkey}_EXTERNAL_LINKS})
    target_link_libraries("${_vellum_mirror_executable}" PRIVATE
        ${_vellum_executable_units} ${_vellum_executable_third_party}
        ${VELLUM_MIRROR_TARGET_${_tkey}_IN_TREE_INTERFACES})
endforeach()

# Headers: each mirrored unit's public include roots, the rendered build
# info, and the closure-listed pinned headers those public headers include.
foreach(_vellum_mirror_unit IN LISTS VELLUM_MIRROR_UNITS)
    _vellum_mirror_key(_ukey "${_vellum_mirror_unit}")
    foreach(_target IN LISTS VELLUM_MIRROR_UNIT_${_ukey}_TARGETS)
        _vellum_mirror_key(_tkey "${_target}")
        foreach(_include IN LISTS VELLUM_MIRROR_TARGET_${_tkey}_INCLUDE_DIRS)
            if(_include MATCHES "^${VELLUM_MIRROR_SOURCE_DIR}/core/[^/]+/include$")
                list(APPEND _vellum_mirror_public_includes "${_include}")
            endif()
        endforeach()
    endforeach()
endforeach()
list(REMOVE_DUPLICATES _vellum_mirror_public_includes)
# The GPU device types are not part of the installed surface. Each choke
# header (gpu_surface_guard.py) is installed as its guarded copy instead of
# the mirrored file; every other header installs byte for byte.
math(EXPR _vellum_gpu_guard_last "${VELLUM_MIRROR_GPU_GUARD_COUNT} - 1")
foreach(_include IN LISTS _vellum_mirror_public_includes)
    set(_vellum_gpu_guard_excludes)
    if(VELLUM_MIRROR_GPU_GUARD_COUNT GREATER 0)
        foreach(_index RANGE ${_vellum_gpu_guard_last})
            if(VELLUM_MIRROR_GPU_GUARD_${_index}_INCLUDE_DIR STREQUAL _include)
                list(APPEND _vellum_gpu_guard_excludes
                    REGEX "${VELLUM_MIRROR_GPU_GUARD_${_index}_EXCLUDE}" EXCLUDE)
            endif()
        endforeach()
    endif()
    install(DIRECTORY "${_include}/"
        DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/include"
        PATTERN "*.in" EXCLUDE
        ${_vellum_gpu_guard_excludes})
endforeach()
if(VELLUM_MIRROR_GPU_GUARD_COUNT GREATER 0)
    foreach(_index RANGE ${_vellum_gpu_guard_last})
        if(NOT VELLUM_MIRROR_GPU_GUARD_${_index}_INCLUDE_DIR IN_LIST _vellum_mirror_public_includes)
            message(FATAL_ERROR "GPU guard names ${VELLUM_MIRROR_GPU_GUARD_${_index}_INCLUDE_DIR}, "
                "which the mirror does not install")
        endif()
        get_filename_component(_vellum_gpu_guard_dir
            "${VELLUM_MIRROR_GPU_GUARD_${_index}_RELATIVE}" DIRECTORY)
        install(FILES "${VELLUM_MIRROR_GPU_GUARD_${_index}_COPY}"
            DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/include/${_vellum_gpu_guard_dir}")
    endforeach()
    install(DIRECTORY "${VELLUM_MIRROR_GPU_GUARD_CONFIG_DIR}/"
        DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/include")
endif()
install(FILES "${VELLUM_MIRROR_GPU_GUARD_MANIFEST}"
    DESTINATION "${VELLUM_MIRROR_INSTALL_DIR}/share/provenance")
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
