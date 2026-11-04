# Locates the locked Dawn build for the device library and checks it against
# renderer-lock.json before anything links it.
#
#   gpu_runtime_find_dawn(
#       PLATFORM <platform>         # a key of the lock's "platforms"
#       [SKIA_DIR <dir>]            # an extracted skia-builder asset
#       [ARCHIVE <zip>]             # or the asset itself; extracted under the build dir
#       [LIBRARY <libdawn_combined.a>]  # or the library alone, as a published
#                                   # gpu-runtime package ships it (no headers:
#                                   # the device API names no Dawn type)
#       [LOCK <renderer-lock.json>]
#       [REQUIRED])
#
# Defines the IMPORTED target gpu-runtime-dawn (Dawn's headers and
# libdawn_combined.a) and sets GPU_RUNTIME_HAS_DAWN in the caller. Without a
# directory or archive it sets GPU_RUNTIME_HAS_DAWN OFF, or stops configure
# when REQUIRED. A digest mismatch always stops configure.
#
# Whichever form is supplied, the check proves it is the Dawn build the runtime
# was compiled against.

include("${CMAKE_CURRENT_LIST_DIR}/GpuRuntimeRendererLock.cmake")

function(gpu_runtime_find_dawn)
    cmake_parse_arguments(ARG "REQUIRED" "PLATFORM;SKIA_DIR;ARCHIVE;LIBRARY;LOCK" "" ${ARGN})
    set(GPU_RUNTIME_HAS_DAWN OFF PARENT_SCOPE)
    if(TARGET gpu-runtime-dawn)
        set(GPU_RUNTIME_HAS_DAWN ON PARENT_SCOPE)
        return()
    endif()
    set(_lock_args PLATFORM "${ARG_PLATFORM}" PREFIX _gr)
    if(ARG_LOCK)
        list(APPEND _lock_args LOCK "${ARG_LOCK}")
    endif()
    gpu_runtime_read_renderer_lock(${_lock_args})

    if(ARG_LIBRARY AND NOT ARG_SKIA_DIR AND NOT ARG_ARCHIVE)
        if(NOT EXISTS "${ARG_LIBRARY}")
            message(FATAL_ERROR "gpu-runtime: Dawn library does not exist: ${ARG_LIBRARY}")
        endif()
        file(SHA256 "${ARG_LIBRARY}" _library_sha256)
        if(NOT _library_sha256 STREQUAL "${_gr_SHA256_libdawn_combined.a}")
            message(FATAL_ERROR
                "gpu-runtime: libdawn_combined.a does not match the lock; expected "
                "${_gr_SHA256_libdawn_combined.a}, got ${_library_sha256}")
        endif()
        add_library(gpu-runtime-dawn STATIC IMPORTED GLOBAL)
        set_target_properties(gpu-runtime-dawn PROPERTIES IMPORTED_LOCATION "${ARG_LIBRARY}")
        if(APPLE)
            set_property(TARGET gpu-runtime-dawn APPEND PROPERTY INTERFACE_LINK_LIBRARIES
                "-framework Foundation" "-framework IOKit" "-framework IOSurface"
                "-framework Metal" "-framework QuartzCore")
        endif()
        set(GPU_RUNTIME_DAWN_LIBRARY "${ARG_LIBRARY}" PARENT_SCOPE)
        set(GPU_RUNTIME_HAS_DAWN ON PARENT_SCOPE)
        return()
    endif()

    set(_dir "${ARG_SKIA_DIR}")
    if(NOT _dir AND ARG_ARCHIVE)
        if(NOT EXISTS "${ARG_ARCHIVE}")
            message(FATAL_ERROR "gpu-runtime: renderer archive does not exist: ${ARG_ARCHIVE}")
        endif()
        file(SHA256 "${ARG_ARCHIVE}" _archive_sha256)
        if(NOT _archive_sha256 STREQUAL _gr_SHA256)
            message(FATAL_ERROR
                "gpu-runtime: renderer archive digest mismatch; the lock says "
                "${_gr_SHA256}, got ${_archive_sha256}")
        endif()
        set(_dir "${CMAKE_BINARY_DIR}/_deps/gpu-runtime-renderer")
        if(NOT EXISTS "${_dir}/.extracted-${_gr_SHA256}")
            file(REMOVE_RECURSE "${_dir}")
            file(MAKE_DIRECTORY "${_dir}")
            file(ARCHIVE_EXTRACT INPUT "${ARG_ARCHIVE}" DESTINATION "${_dir}")
            file(WRITE "${_dir}/.extracted-${_gr_SHA256}" "")
        endif()
    endif()
    if(NOT _dir)
        if(ARG_REQUIRED)
            message(FATAL_ERROR
                "gpu-runtime: the device library needs the locked Dawn build; set "
                "GPU_RUNTIME_SKIA_DIR or GPU_RUNTIME_RENDERER_ARCHIVE")
        endif()
        return()
    endif()

    set(_include "${_dir}/build/include")
    set(_library "${_dir}/${_gr_ARCHIVE_ROOT}/libdawn_combined.a")
    if(NOT EXISTS "${_library}" OR
       NOT EXISTS "${_include}/third_party/externals/dawn/include/dawn/native/DawnNative.h" OR
       NOT EXISTS "${_include}/dawn/dawn_proc_table.h")
        message(FATAL_ERROR
            "gpu-runtime: ${_dir} does not hold the locked Dawn headers and "
            "${_gr_ARCHIVE_ROOT}/libdawn_combined.a")
    endif()
    file(SHA256 "${_library}" _library_sha256)
    if(NOT _library_sha256 STREQUAL "${_gr_SHA256_libdawn_combined.a}")
        message(FATAL_ERROR
            "gpu-runtime: libdawn_combined.a does not match the lock; expected "
            "${_gr_SHA256_libdawn_combined.a}, got ${_library_sha256}")
    endif()

    add_library(gpu-runtime-dawn STATIC IMPORTED GLOBAL)
    set_target_properties(gpu-runtime-dawn PROPERTIES
        IMPORTED_LOCATION "${_library}"
        INTERFACE_INCLUDE_DIRECTORIES
            "${_include};${_include}/third_party/externals/dawn/include")
    if(APPLE)
        set_property(TARGET gpu-runtime-dawn APPEND PROPERTY INTERFACE_LINK_LIBRARIES
            "-framework Foundation" "-framework IOKit" "-framework IOSurface"
            "-framework Metal" "-framework QuartzCore")
    endif()
    string(REGEX REPLACE "^chrome/m" "" _milestone "${_gr_MILESTONE}")
    set(GPU_RUNTIME_SKIA_MILESTONE "${_milestone}" PARENT_SCOPE)
    set(GPU_RUNTIME_DAWN_DIR "${_dir}" PARENT_SCOPE)
    set(GPU_RUNTIME_DAWN_LIBRARY "${_library}" PARENT_SCOPE)
    set(GPU_RUNTIME_HAS_DAWN ON PARENT_SCOPE)
endfunction()
