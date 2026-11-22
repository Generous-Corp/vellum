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

# Packaging and linking the mirror libraries uses Apple-only properties and
# linker flags (frameworks, exported and unexported symbol lists, reexport,
# dead-strip). The portable shared-library path uses the same unit boundaries
# without those flags.
function(vellum_mirror_require_packaging)
    if(CMAKE_SYSTEM_NAME STREQUAL "Darwin" OR CMAKE_SYSTEM_NAME STREQUAL "Linux"
       OR CMAKE_SYSTEM_NAME STREQUAL "Windows")
        return()
    endif()
    message(FATAL_ERROR "Vellum: mirror packaging is unsupported on ${CMAKE_SYSTEM_NAME}")
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
