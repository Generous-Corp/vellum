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
# Defines the IMPORTED target VellumGpuRuntime::Dawn (Dawn's headers, when
# available, and libdawn_combined.a) and sets GPU_RUNTIME_HAS_DAWN and
# GPU_RUNTIME_DAWN_LIBRARY in the caller. Without a directory, archive or
# library it sets GPU_RUNTIME_HAS_DAWN OFF, or stops configure when REQUIRED.
# A digest mismatch always stops configure, and so does an existing
# VellumGpuRuntime::Dawn that was checked against a different lock digest:
# the target is never reused on trust.

include("${CMAKE_CURRENT_LIST_DIR}/GpuRuntimeRendererLock.cmake")

function(_gpu_runtime_check_dawn_library library expected)
    if(NOT EXISTS "${library}")
        message(FATAL_ERROR "gpu-runtime: Dawn library does not exist: ${library}")
    endif()
    file(SHA256 "${library}" _actual)
    if(NOT _actual STREQUAL expected)
        message(FATAL_ERROR
            "gpu-runtime: libdawn_combined.a does not match the lock; expected "
            "${expected}, got ${_actual}")
    endif()
endfunction()

function(_gpu_runtime_define_dawn library include_dirs digest)
    add_library(VellumGpuRuntime::Dawn STATIC IMPORTED GLOBAL)
    set_target_properties(VellumGpuRuntime::Dawn PROPERTIES
        IMPORTED_LOCATION "${library}"
        GPU_RUNTIME_DAWN_SHA256 "${digest}")
    if(include_dirs)
        set_target_properties(VellumGpuRuntime::Dawn PROPERTIES
            INTERFACE_INCLUDE_DIRECTORIES "${include_dirs}")
    endif()
    if(APPLE)
        set_property(TARGET VellumGpuRuntime::Dawn APPEND PROPERTY INTERFACE_LINK_LIBRARIES
            "-framework Foundation" "-framework IOKit" "-framework IOSurface"
            "-framework Metal" "-framework QuartzCore")
    endif()
endfunction()

function(gpu_runtime_find_dawn)
    cmake_parse_arguments(ARG "REQUIRED" "PLATFORM;SKIA_DIR;ARCHIVE;LIBRARY;LOCK" "" ${ARGN})
    set(GPU_RUNTIME_HAS_DAWN OFF PARENT_SCOPE)
    set(_lock_args PLATFORM "${ARG_PLATFORM}" PREFIX _gr)
    if(ARG_LOCK)
        list(APPEND _lock_args LOCK "${ARG_LOCK}")
    endif()
    gpu_runtime_read_renderer_lock(${_lock_args})
    set(_expected "${_gr_SHA256_libdawn_combined.a}")
    string(REGEX REPLACE "^chrome/m" "" _milestone "${_gr_MILESTONE}")
    set(GPU_RUNTIME_SKIA_MILESTONE "${_milestone}" PARENT_SCOPE)

    if(TARGET VellumGpuRuntime::Dawn)
        get_target_property(_recorded VellumGpuRuntime::Dawn GPU_RUNTIME_DAWN_SHA256)
        get_target_property(_location VellumGpuRuntime::Dawn IMPORTED_LOCATION)
        if(NOT _recorded STREQUAL _expected)
            message(FATAL_ERROR
                "gpu-runtime: VellumGpuRuntime::Dawn already exists and was not checked "
                "against this lock (recorded ${_recorded}, expected ${_expected})")
        endif()
        _gpu_runtime_check_dawn_library("${_location}" "${_expected}")
        set(GPU_RUNTIME_DAWN_LIBRARY "${_location}" PARENT_SCOPE)
        set(GPU_RUNTIME_HAS_DAWN ON PARENT_SCOPE)
        return()
    endif()

    if(ARG_LIBRARY AND NOT ARG_SKIA_DIR AND NOT ARG_ARCHIVE)
        _gpu_runtime_check_dawn_library("${ARG_LIBRARY}" "${_expected}")
        _gpu_runtime_define_dawn("${ARG_LIBRARY}" "" "${_expected}")
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
    if(NOT EXISTS "${_include}/third_party/externals/dawn/include/dawn/native/DawnNative.h" OR
       NOT EXISTS "${_include}/dawn/dawn_proc_table.h")
        message(FATAL_ERROR
            "gpu-runtime: ${_dir} does not hold the locked Dawn headers and "
            "${_gr_ARCHIVE_ROOT}/libdawn_combined.a")
    endif()
    _gpu_runtime_check_dawn_library("${_library}" "${_expected}")
    _gpu_runtime_define_dawn("${_library}"
        "${_include};${_include}/third_party/externals/dawn/include" "${_expected}")
    set(GPU_RUNTIME_DAWN_DIR "${_dir}" PARENT_SCOPE)
    set(GPU_RUNTIME_DAWN_LIBRARY "${_library}" PARENT_SCOPE)
    set(GPU_RUNTIME_HAS_DAWN ON PARENT_SCOPE)
endfunction()
