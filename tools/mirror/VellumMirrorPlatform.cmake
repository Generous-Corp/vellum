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

# Sets <out_var> to "<System>|<processor>" for every platform the mirror may
# package. macOS is declared by the landed export receipt index. Every other
# platform is declared by a platform record that provenance/framework/platforms
# lists in its index.json, whose file exists, carries the closure schema and
# names the key it is listed under. VELLUM_MIRROR_PLATFORM_RECORD_ROOT
# replaces that directory.
function(vellum_mirror_declared_platforms out_var)
    set(_records_root "${_vellum_mirror_platform_module_dir}/../../provenance/framework/platforms")
    if(DEFINED VELLUM_MIRROR_PLATFORM_RECORD_ROOT)
        set(_records_root "${VELLUM_MIRROR_PLATFORM_RECORD_ROOT}")
    endif()
    set(_declared)
    if(EXISTS "${_vellum_mirror_platform_module_dir}/../../provenance/mirror/receipts/index.json")
        list(APPEND _declared "Darwin|arm64")
    endif()
    if(EXISTS "${_records_root}/index.json")
        file(READ "${_records_root}/index.json" _index_json)
        string(JSON _listed ERROR_VARIABLE _listed_error GET "${_index_json}" platforms)
        if(NOT _listed_error)
            string(JSON _count LENGTH "${_listed}")
            if(_count GREATER 0)
                math(EXPR _last "${_count} - 1")
                foreach(_i RANGE ${_last})
                    string(JSON _key MEMBER "${_listed}" ${_i})
                    string(JSON _relative GET "${_listed}" "${_key}")
                    get_filename_component(_record "${_relative}" NAME)
                    set(_record "${_records_root}/${_key}/${_record}")
                    if(NOT EXISTS "${_record}")
                        continue()
                    endif()
                    file(READ "${_record}" _record_json)
                    string(JSON _schema ERROR_VARIABLE _e1 GET "${_record_json}" schema)
                    string(JSON _platform ERROR_VARIABLE _e2 GET "${_record_json}" platform)
                    string(JSON _system ERROR_VARIABLE _e3
                        GET "${_record_json}" configure identity system)
                    string(JSON _processor ERROR_VARIABLE _e4
                        GET "${_record_json}" configure identity processor)
                    if(_e1 OR _e2 OR _e3 OR _e4)
                        continue()
                    endif()
                    if(_schema STREQUAL "vellum.framework-platform-closure.v1" AND
                       _platform STREQUAL _key)
                        list(APPEND _declared "${_system}|${_processor}")
                    endif()
                endforeach()
            endif()
        endif()
    endif()
    set(${out_var} "${_declared}" PARENT_SCOPE)
endfunction()

function(vellum_mirror_require_packaging)
    vellum_mirror_declared_platforms(_declared)
    set(_declared_systems)
    foreach(_platform IN LISTS _declared)
        string(REGEX REPLACE "\\|.*$" "" _system "${_platform}")
        list(APPEND _declared_systems "${_system}")
    endforeach()
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
