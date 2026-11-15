# VellumV8Provider.cmake: resolve the pinned, sealed V8 provider and expose it as
# the `vellum-v8` imported target.
#
# The provider is the sealed shared libv8 that danielraffel/v8-builder
# publishes for one Chromium milestone. Sealed means its export table holds
# only the v8:: and cppgc:: embedder API; the ICU, Abseil and zlib it links
# stay internal, so they cannot bind to the copies of the same libraries
# inside the Skia/Dawn provider. Pulp consumes the same release through its
# tools/cmake/FindV8.cmake; this module is that resolver for Vellum's one
# platform (macOS arm64), with the pin read from Vellum's dependency lock.
#
#   vellum_v8_resolve_provider(
#       LOCK <provenance/third-party-lock.json>
#       RENDERER_LOCK <gpu-runtime/renderer-lock.json>
#       WORK_DIR <directory for the download and the extracted provider>
#       [ARCHIVE <local copy of the pinned archive>])
#
# The archive is the lock's `V8` row: its SHA-256 must match whether it is
# downloaded or supplied. The extracted manifest must be sealed, name the
# locked V8 version and builder revision, and pair with the exact Skia and
# Dawn commits gpu-runtime/renderer-lock.json pins. Every extracted file is
# recorded with its digest; a later configure re-hashes them and extracts
# again on any difference.
#
# Sets in the caller's scope:
#   VELLUM_V8_VERSION   the V8 runtime version the provider must report
#   VELLUM_V8_LIBRARY   the extracted libv8.dylib
#   VELLUM_V8_MANIFEST  the extracted, validated manifest.json
# and defines the GLOBAL imported target `vellum-v8` (headers, library, and
# the manifest's consumer_defines as INTERFACE_COMPILE_DEFINITIONS).

include_guard(GLOBAL)

include("${CMAKE_CURRENT_LIST_DIR}/VellumV8ConsumerDefines.cmake")

set(_VELLUM_V8_RECEIPT ".vellum-v8-receipt.json")

function(_vellum_v8_json out json)
    string(JSON _value ERROR_VARIABLE _error GET "${json}" ${ARGN})
    if(NOT _error STREQUAL "NOTFOUND")
        message(FATAL_ERROR "Vellum V8: missing field ${ARGN}: ${_error}")
    endif()
    set(${out} "${_value}" PARENT_SCOPE)
endfunction()

# Whether `root` holds exactly the files its receipt records for `asset_sha256`.
function(_vellum_v8_receipt_valid root asset_sha256 result)
    set(${result} FALSE PARENT_SCOPE)
    if(NOT EXISTS "${root}/${_VELLUM_V8_RECEIPT}")
        return()
    endif()
    file(READ "${root}/${_VELLUM_V8_RECEIPT}" _receipt)
    string(JSON _recorded ERROR_VARIABLE _error GET "${_receipt}" asset_sha256)
    if(NOT _error STREQUAL "NOTFOUND" OR NOT _recorded STREQUAL asset_sha256)
        return()
    endif()
    string(JSON _count ERROR_VARIABLE _error LENGTH "${_receipt}" files)
    if(NOT _error STREQUAL "NOTFOUND" OR _count LESS 1)
        return()
    endif()
    file(GLOB_RECURSE _present LIST_DIRECTORIES FALSE RELATIVE "${root}" "${root}/*")
    list(REMOVE_ITEM _present "${_VELLUM_V8_RECEIPT}")
    list(LENGTH _present _present_count)
    if(NOT _present_count EQUAL _count)
        return()
    endif()
    math(EXPR _last "${_count} - 1")
    foreach(_index RANGE ${_last})
        string(JSON _path GET "${_receipt}" files ${_index} path)
        string(JSON _sha GET "${_receipt}" files ${_index} sha256)
        if(NOT EXISTS "${root}/${_path}" OR IS_SYMLINK "${root}/${_path}")
            return()
        endif()
        file(SHA256 "${root}/${_path}" _actual)
        if(NOT _actual STREQUAL _sha)
            return()
        endif()
    endforeach()
    set(${result} TRUE PARENT_SCOPE)
endfunction()

function(_vellum_v8_write_receipt root asset_sha256)
    file(GLOB_RECURSE _files LIST_DIRECTORIES FALSE RELATIVE "${root}" "${root}/*")
    list(SORT _files)
    set(_receipt "{\"schema\":\"vellum.v8-provider-receipt.v1\",\"asset_sha256\":\"${asset_sha256}\",\"files\":[]}")
    set(_index 0)
    foreach(_path IN LISTS _files)
        if(IS_SYMLINK "${root}/${_path}")
            message(FATAL_ERROR "Vellum V8: the pinned archive holds a symbolic link: ${_path}")
        endif()
        file(SHA256 "${root}/${_path}" _sha)
        string(JSON _receipt SET "${_receipt}" files ${_index}
            "{\"path\":\"${_path}\",\"sha256\":\"${_sha}\"}")
        math(EXPR _index "${_index} + 1")
    endforeach()
    file(WRITE "${root}/${_VELLUM_V8_RECEIPT}" "${_receipt}\n")
endfunction()

function(vellum_v8_resolve_provider)
    cmake_parse_arguments(PARSE_ARGV 0 ARG "" "LOCK;RENDERER_LOCK;WORK_DIR;ARCHIVE" "")
    foreach(_required LOCK RENDERER_LOCK WORK_DIR)
        if(NOT ARG_${_required})
            message(FATAL_ERROR "vellum_v8_resolve_provider: ${_required} is required")
        endif()
    endforeach()
    if(NOT APPLE OR NOT (CMAKE_OSX_ARCHITECTURES STREQUAL "arm64"
            OR (NOT CMAKE_OSX_ARCHITECTURES AND CMAKE_SYSTEM_PROCESSOR STREQUAL "arm64")))
        message(FATAL_ERROR
            "Vellum V8: the locked provider is the macOS arm64 build; this "
            "configuration targets ${CMAKE_SYSTEM_NAME} '${CMAKE_OSX_ARCHITECTURES}'")
    endif()

    # The pin: the lock's one row named V8.
    file(READ "${ARG_LOCK}" _lock)
    string(JSON _rows LENGTH "${_lock}" dependencies)
    set(_row "")
    math(EXPR _last "${_rows} - 1")
    foreach(_index RANGE ${_last})
        string(JSON _name GET "${_lock}" dependencies ${_index} name)
        if(_name STREQUAL "V8")
            if(_row)
                message(FATAL_ERROR "Vellum V8: ${ARG_LOCK} has more than one V8 row")
            endif()
            string(JSON _row GET "${_lock}" dependencies ${_index})
        endif()
    endforeach()
    if(NOT _row)
        message(FATAL_ERROR "Vellum V8: ${ARG_LOCK} has no V8 row")
    endif()
    _vellum_v8_json(_url "${_row}" url)
    _vellum_v8_json(_sha256 "${_row}" sha256)
    _vellum_v8_json(_version "${_row}" v8_version)
    _vellum_v8_json(_built_revision "${_row}" built_revision)
    _vellum_v8_json(_paired_skia "${_row}" paired_skia)
    _vellum_v8_json(_paired_dawn "${_row}" paired_dawn)
    _vellum_v8_json(_skia_release "${_row}" skia_release_tag)
    # Fail closed on anything but the builder's own release assets, so no lock
    # field can point the download elsewhere.
    if(NOT _url MATCHES "^https://github\\.com/danielraffel/v8-builder/releases/download/v8-m[0-9]+-[0-9.]+-[0-9a-f]+/v8-mac-arm64-[A-Za-z0-9._-]+\\.zip$"
       OR NOT _sha256 MATCHES "^[0-9a-f]+$" OR NOT _sha256 MATCHES "^.{64}$"
       OR NOT _version MATCHES "^[0-9]+(\\.[0-9]+)+$")
        message(FATAL_ERROR "Vellum V8: the lock's V8 row does not name a v8-builder mac-arm64 asset")
    endif()

    # The provider must pair with the renderer Vellum links.
    file(READ "${ARG_RENDERER_LOCK}" _renderer)
    _vellum_v8_json(_renderer_skia "${_renderer}" sources skia)
    _vellum_v8_json(_renderer_dawn "${_renderer}" sources dawn)
    _vellum_v8_json(_renderer_milestone "${_renderer}" milestone)
    if(NOT _paired_skia STREQUAL _renderer_skia OR NOT _paired_dawn STREQUAL _renderer_dawn
       OR NOT _skia_release STREQUAL _renderer_milestone)
        message(FATAL_ERROR
            "Vellum V8: the locked V8 was validated against Skia ${_paired_skia}, Dawn "
            "${_paired_dawn} (${_skia_release}); gpu-runtime/renderer-lock.json pins "
            "Skia ${_renderer_skia}, Dawn ${_renderer_dawn} (${_renderer_milestone}). "
            "Move V8 with the renderer.")
    endif()

    # The archive: a supplied copy, or the pinned download, checked by digest.
    file(MAKE_DIRECTORY "${ARG_WORK_DIR}")
    get_filename_component(_asset "${_url}" NAME)
    if(ARG_ARCHIVE)
        set(_archive "${ARG_ARCHIVE}")
        if(NOT EXISTS "${_archive}")
            message(FATAL_ERROR "Vellum V8: VELLUM_V8_ARCHIVE does not exist: ${_archive}")
        endif()
    else()
        set(_archive "${ARG_WORK_DIR}/${_asset}")
        set(_have FALSE)
        if(EXISTS "${_archive}")
            file(SHA256 "${_archive}" _existing)
            if(_existing STREQUAL _sha256)
                set(_have TRUE)
            endif()
        endif()
        if(NOT _have)
            message(STATUS "Vellum V8: downloading ${_url}")
            file(DOWNLOAD "${_url}" "${_archive}.part"
                EXPECTED_HASH "SHA256=${_sha256}"
                TLS_VERIFY ON
                STATUS _status)
            list(GET _status 0 _code)
            if(NOT _code EQUAL 0)
                file(REMOVE "${_archive}.part")
                message(FATAL_ERROR "Vellum V8: download failed: ${_status}")
            endif()
            file(RENAME "${_archive}.part" "${_archive}")
        endif()
    endif()
    file(SHA256 "${_archive}" _archive_sha256)
    if(NOT _archive_sha256 STREQUAL _sha256)
        message(FATAL_ERROR
            "Vellum V8: archive digest mismatch; the lock pins ${_sha256}, "
            "${_archive} is ${_archive_sha256}")
    endif()

    set(_root "${ARG_WORK_DIR}/provider")
    _vellum_v8_receipt_valid("${_root}" "${_sha256}" _valid)
    if(NOT _valid)
        file(REMOVE_RECURSE "${_root}")
        file(MAKE_DIRECTORY "${_root}")
        file(ARCHIVE_EXTRACT INPUT "${_archive}" DESTINATION "${_root}")
        _vellum_v8_write_receipt("${_root}" "${_sha256}")
    endif()

    # The sealed manifest inside the pinned archive.
    set(_manifest_path "${_root}/manifest.json")
    if(NOT EXISTS "${_manifest_path}")
        message(FATAL_ERROR "Vellum V8: the pinned archive carries no manifest.json")
    endif()
    file(READ "${_manifest_path}" _manifest)
    _vellum_v8_json(_m_version "${_manifest}" v8_version)
    _vellum_v8_json(_m_platform "${_manifest}" platform)
    _vellum_v8_json(_m_arch "${_manifest}" arch)
    _vellum_v8_json(_m_shared "${_manifest}" shared)
    _vellum_v8_json(_m_lib "${_manifest}" lib)
    _vellum_v8_json(_m_kind "${_manifest}" pair pair_kind)
    _vellum_v8_json(_m_revision "${_manifest}" pair built_revision)
    _vellum_v8_json(_m_skia "${_manifest}" pair built_skia)
    _vellum_v8_json(_m_dawn "${_manifest}" pair built_dawn)
    _vellum_v8_json(_m_release "${_manifest}" pair validated_skia_release)
    if(NOT _m_version STREQUAL _version OR NOT _m_platform STREQUAL "mac"
       OR NOT _m_arch STREQUAL "arm64" OR NOT _m_shared OR NOT _m_lib STREQUAL "lib/libv8.dylib"
       OR NOT _m_kind STREQUAL "chromium-milestone" OR NOT _m_revision STREQUAL _built_revision
       OR NOT _m_skia STREQUAL _renderer_skia OR NOT _m_dawn STREQUAL _renderer_dawn
       OR NOT _m_release STREQUAL _renderer_milestone)
        message(FATAL_ERROR
            "Vellum V8: the archive's manifest does not describe the locked provider "
            "(version ${_m_version}, ${_m_platform}-${_m_arch}, revision ${_m_revision}, "
            "Skia ${_m_skia}, Dawn ${_m_dawn})")
    endif()
    set(_library "${_root}/lib/libv8.dylib")
    if(NOT EXISTS "${_library}" OR NOT EXISTS "${_root}/include/v8.h")
        message(FATAL_ERROR "Vellum V8: the pinned archive lacks lib/libv8.dylib or include/v8.h")
    endif()

    if(NOT TARGET vellum-v8)
        add_library(vellum-v8 SHARED IMPORTED GLOBAL)
        set_target_properties(vellum-v8 PROPERTIES
            IMPORTED_LOCATION "${_library}"
            IMPORTED_SONAME "@rpath/libv8.dylib"
            INTERFACE_INCLUDE_DIRECTORIES "${_root}/include")
        # sealed=true is checked here; a manifest without it fails configure.
        vellum_v8_apply_consumer_defines(vellum-v8 "${_manifest_path}")
    endif()
    message(STATUS "Vellum V8: sealed provider ${_version} at ${_library}")
    set(VELLUM_V8_VERSION "${_version}" PARENT_SCOPE)
    set(VELLUM_V8_LIBRARY "${_library}" PARENT_SCOPE)
    set(VELLUM_V8_MANIFEST "${_manifest_path}" PARENT_SCOPE)
endfunction()
