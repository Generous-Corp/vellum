# Reads gpu-runtime/renderer-lock.json, the one place the Skia/Dawn milestone
# and the skia-builder digests are written. Build code that needs the pin calls
# gpu_runtime_read_renderer_lock() instead of carrying its own copy.
#
#   gpu_runtime_read_renderer_lock(
#       PLATFORM <platform>      # a key of "platforms", e.g. macos-arm64
#       PREFIX   <prefix>        # names of the variables set in the caller
#       [LOCK    <path>])        # defaults to this module's renderer-lock.json
#
# Sets, in the caller's scope:
#   <prefix>_MILESTONE          the skia-builder release tag (chrome/mNNN)
#   <prefix>_ASSET              the release asset's file name
#   <prefix>_URL                the asset's download URL
#   <prefix>_SHA256             the asset's SHA-256
#   <prefix>_ARCHIVE_ROOT       the static-library directory inside the asset
#   <prefix>_ARCHIVES           the locked static-library names
#   <prefix>_SHA256_<archive>   each library's SHA-256, keyed by its file name
#                               (for example <prefix>_SHA256_libskia.a)
#
# Every value is validated; a malformed lock stops configure.

# In the source tree the lock sits one directory up; an installed package
# keeps it next to this module.
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/renderer-lock.json")
    set(_GPU_RUNTIME_RENDERER_LOCK_DEFAULT "${CMAKE_CURRENT_LIST_DIR}/renderer-lock.json")
else()
    set(_GPU_RUNTIME_RENDERER_LOCK_DEFAULT "${CMAKE_CURRENT_LIST_DIR}/../renderer-lock.json")
endif()
set(_GPU_RUNTIME_RENDERER_LOCK_SCHEMA "gpu-runtime.renderer-lock.v1")

function(_gpu_runtime_lock_get out json)
    string(JSON _value ERROR_VARIABLE _error GET "${json}" ${ARGN})
    if(_error)
        string(REPLACE ";" "." _path "${ARGN}")
        message(FATAL_ERROR
            "gpu-runtime: renderer lock has no ${_path}: ${_error}")
    endif()
    set(${out} "${_value}" PARENT_SCOPE)
endfunction()

function(_gpu_runtime_lock_require_sha256 value label)
    if(NOT value MATCHES "^[0-9a-f]+$")
        message(FATAL_ERROR
            "gpu-runtime: renderer lock ${label} is not a lowercase SHA-256: ${value}")
    endif()
    string(LENGTH "${value}" _length)
    if(NOT _length EQUAL 64)
        message(FATAL_ERROR
            "gpu-runtime: renderer lock ${label} is not a lowercase SHA-256: ${value}")
    endif()
endfunction()

function(gpu_runtime_read_renderer_lock)
    cmake_parse_arguments(ARG "" "PLATFORM;PREFIX;LOCK" "" ${ARGN})
    if(NOT ARG_PLATFORM OR NOT ARG_PREFIX)
        message(FATAL_ERROR
            "gpu_runtime_read_renderer_lock: PLATFORM and PREFIX are required")
    endif()
    if(NOT ARG_LOCK)
        set(ARG_LOCK "${_GPU_RUNTIME_RENDERER_LOCK_DEFAULT}")
    endif()
    if(NOT EXISTS "${ARG_LOCK}")
        message(FATAL_ERROR "gpu-runtime: renderer lock not found: ${ARG_LOCK}")
    endif()
    # Re-run configure when the lock changes, so a bump is never served by a
    # stale cache.
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${ARG_LOCK}")
    file(READ "${ARG_LOCK}" _json)

    _gpu_runtime_lock_get(_schema "${_json}" schema)
    if(NOT _schema STREQUAL _GPU_RUNTIME_RENDERER_LOCK_SCHEMA)
        message(FATAL_ERROR
            "gpu-runtime: renderer lock schema is ${_schema}, expected "
            "${_GPU_RUNTIME_RENDERER_LOCK_SCHEMA}")
    endif()
    _gpu_runtime_lock_get(_milestone "${_json}" milestone)
    if(NOT _milestone MATCHES "^chrome/m[0-9]+$")
        message(FATAL_ERROR
            "gpu-runtime: renderer lock milestone is not chrome/mNNN: ${_milestone}")
    endif()
    _gpu_runtime_lock_get(_repository "${_json}" builder repository)
    if(NOT _repository MATCHES "^https://github\\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
        message(FATAL_ERROR
            "gpu-runtime: renderer lock builder repository is malformed: ${_repository}")
    endif()

    string(JSON _platform_type ERROR_VARIABLE _error
        TYPE "${_json}" platforms "${ARG_PLATFORM}")
    if(_error OR NOT _platform_type STREQUAL "OBJECT")
        message(FATAL_ERROR
            "gpu-runtime: renderer lock has no platform ${ARG_PLATFORM}")
    endif()
    _gpu_runtime_lock_get(_asset "${_json}" platforms "${ARG_PLATFORM}" asset)
    if(NOT _asset MATCHES "^[A-Za-z0-9_.-]+$")
        message(FATAL_ERROR "gpu-runtime: renderer lock asset name is malformed: ${_asset}")
    endif()
    _gpu_runtime_lock_get(_sha256 "${_json}" platforms "${ARG_PLATFORM}" sha256)
    _gpu_runtime_lock_require_sha256("${_sha256}" "${ARG_PLATFORM} asset digest")
    _gpu_runtime_lock_get(_archive_root "${_json}"
        platforms "${ARG_PLATFORM}" archive_root)

    string(JSON _count ERROR_VARIABLE _error
        LENGTH "${_json}" platforms "${ARG_PLATFORM}" archives)
    if(_error OR _count LESS 1)
        message(FATAL_ERROR
            "gpu-runtime: renderer lock lists no archives for ${ARG_PLATFORM}")
    endif()
    set(_names "")
    math(EXPR _last "${_count} - 1")
    foreach(_index RANGE ${_last})
        string(JSON _name MEMBER "${_json}"
            platforms "${ARG_PLATFORM}" archives ${_index})
        if(NOT _name MATCHES "^lib[A-Za-z0-9_]+\\.a$")
            message(FATAL_ERROR
                "gpu-runtime: renderer lock archive name is malformed: ${_name}")
        endif()
        _gpu_runtime_lock_get(_digest "${_json}"
            platforms "${ARG_PLATFORM}" archives "${_name}")
        _gpu_runtime_lock_require_sha256("${_digest}" "${_name} digest")
        list(APPEND _names "${_name}")
        set(${ARG_PREFIX}_SHA256_${_name} "${_digest}" PARENT_SCOPE)
    endforeach()

    set(${ARG_PREFIX}_MILESTONE "${_milestone}" PARENT_SCOPE)
    set(${ARG_PREFIX}_ASSET "${_asset}" PARENT_SCOPE)
    set(${ARG_PREFIX}_URL
        "${_repository}/releases/download/${_milestone}/${_asset}" PARENT_SCOPE)
    set(${ARG_PREFIX}_SHA256 "${_sha256}" PARENT_SCOPE)
    set(${ARG_PREFIX}_ARCHIVE_ROOT "${_archive_root}" PARENT_SCOPE)
    set(${ARG_PREFIX}_ARCHIVES "${_names}" PARENT_SCOPE)
endfunction()
