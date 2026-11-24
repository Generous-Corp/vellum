# The platform arguments tools/mirror/mirror_package.py plans from.
#
# Only macOS (CMAKE_SYSTEM_NAME Darwin) plans from the export receipt's Darwin
# arm64 closures. Every other system, the other Apple systems included, passes
# its configure to mirror_package.py, which plans it only from a verified
# platform closure record (tools/mirror/platform_closures.py) and refuses it
# without one.
function(vellum_mirror_platform_args out_var)
    if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")
        set(${out_var} --platform macos-arm64 PARENT_SCOPE)
    else()
        set(${out_var}
            --system "${CMAKE_SYSTEM_NAME}" --processor "${CMAKE_SYSTEM_PROCESSOR}"
            --apple-sdk "${CMAKE_OSX_SYSROOT}" PARENT_SCOPE)
    endif()
endfunction()

# Packaging and linking the mirror libraries has a small Apple-specific tail
# (frameworks, symbol-list controls, re-export, and dead-strip). The closure
# planner already refuses systems without a verified platform record, so the
# mirror can use the ordinary shared-library rules on declared non-Darwin
# systems.
set(_vellum_mirror_platform_module_dir "${CMAKE_CURRENT_LIST_DIR}")
function(vellum_mirror_require_packaging)
    set(_records_root "${_vellum_mirror_platform_module_dir}/../../provenance/framework/platforms")
    if(DEFINED VELLUM_MIRROR_PLATFORM_RECORD_ROOT)
        set(_records_root "${VELLUM_MIRROR_PLATFORM_RECORD_ROOT}")
    endif()
    set(_declared_systems Darwin)
    file(GLOB _records "${_records_root}/*/record.json")
    foreach(_record IN LISTS _records)
        file(READ "${_record}" _record_json)
        string(JSON _record_system ERROR_VARIABLE _record_error
            GET "${_record_json}" configure identity system)
        if(NOT _record_error)
            list(APPEND _declared_systems "${_record_system}")
        endif()
    endforeach()
    list(REMOVE_DUPLICATES _declared_systems)
    if(CMAKE_SYSTEM_NAME IN_LIST _declared_systems)
        return()
    endif()
    message(FATAL_ERROR
        "Vellum: mirror packaging for ${CMAKE_SYSTEM_NAME} requires a verified "
        "platform closure record")
endfunction()

# -ffile-prefix-map is a GNU and Clang front-end option. MSVC and clang-cl do
# not accept it, so there the paths are left unmapped and the flag is not
# emitted. Arguments are <from>=<to> pairs.
function(vellum_mirror_prefix_map_options out_var)
    set(_ok TRUE)
    foreach(_lang IN ITEMS C CXX)
        if(NOT CMAKE_${_lang}_COMPILER_ID MATCHES "^(GNU|Clang|AppleClang)$"
                OR CMAKE_${_lang}_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
            set(_ok FALSE)
        endif()
    endforeach()
    set(_options)
    if(_ok)
        foreach(_pair IN LISTS ARGN)
            list(APPEND _options "-ffile-prefix-map=${_pair}")
        endforeach()
    endif()
    set(${out_var} "${_options}" PARENT_SCOPE)
endfunction()
