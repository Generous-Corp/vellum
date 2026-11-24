# Consumer helper for Vellum's installed web-compat package.
set(VELLUM_WEB_COMPAT_ROOT "${CMAKE_CURRENT_LIST_DIR}")
# lib/cmake/Vellum/web-compat -> the install prefix is four levels up.
get_filename_component(VELLUM_WEB_COMPAT_PREFIX "${VELLUM_WEB_COMPAT_ROOT}/../../../.." ABSOLUTE)
set(VELLUM_WEB_COMPAT_PRELUDE_DIR "${VELLUM_WEB_COMPAT_PREFIX}/mirror/share/web-compat/preludes")
set(VELLUM_WEB_COMPAT_SCRIPT_DIR "${VELLUM_WEB_COMPAT_ROOT}/../scripts")
set(VELLUM_WEB_COMPAT_EMBED_PRELUDES "${VELLUM_WEB_COMPAT_ROOT}/vellum_embed_preludes.py")
set(VELLUM_WEB_COMPAT_MANIFEST "${VELLUM_WEB_COMPAT_ROOT}/vellum-web-compat-manifest.json")

# Fails unless every manifest entry of `section` exists in `directory` with the
# recorded SHA-256. Appends the verified paths to `out_paths` in manifest order.
function(_vellum_web_compat_verify section directory out_paths)
    string(JSON _count LENGTH "${_vellum_web_compat_manifest}" ${section})
    if(_count LESS 1)
        message(FATAL_ERROR "Vellum web-compat manifest lists no ${section}")
    endif()
    math(EXPR _last "${_count} - 1")
    set(_paths "")
    foreach(_index RANGE ${_last})
        string(JSON _name GET "${_vellum_web_compat_manifest}" ${section} ${_index} name)
        string(JSON _sha GET "${_vellum_web_compat_manifest}" ${section} ${_index} sha256)
        set(_path "${directory}/${_name}")
        if(NOT EXISTS "${_path}")
            message(FATAL_ERROR "Vellum web-compat ${section} entry is missing: ${_name}")
        endif()
        file(SHA256 "${_path}" _actual)
        if(NOT _actual STREQUAL _sha)
            message(FATAL_ERROR
                "Vellum web-compat ${section} entry ${_name} does not match the manifest "
                "(installed ${_actual}, manifest ${_sha})")
        endif()
        list(APPEND _paths "${_path}")
    endforeach()
    set(${out_paths} "${_paths}" PARENT_SCOPE)
endfunction()

# vellum_embed_web_compat_preludes(<output.cpp> <output.hpp>
#     NAMESPACE <ns> INFLATE_HEADER <include> INFLATE_FUNCTION <fn>)
#
# Verifies the installed preludes and scripts against the manifest, writes the
# declaration header (only when its content changes), and adds the command that
# generates the definitions, including `source()`. The header is included by
# the generated source under its own file name. NAMESPACE names the namespace
# the declarations and definitions live in. INFLATE_FUNCTION must be callable
# as std::optional<std::string>(const uint8_t*, size_t, size_t) and be declared
# by INFLATE_HEADER (spelled with its <> or "" delimiters). All three are
# required, so a consumer states which namespace and runtime it links.
function(vellum_embed_web_compat_preludes output_cpp output_header)
    cmake_parse_arguments(PARSE_ARGV 2 _arg "" "NAMESPACE;INFLATE_HEADER;INFLATE_FUNCTION" "")
    if(_arg_UNPARSED_ARGUMENTS)
        message(FATAL_ERROR
            "vellum_embed_web_compat_preludes: unknown arguments: ${_arg_UNPARSED_ARGUMENTS}")
    endif()
    foreach(_required_argument NAMESPACE INFLATE_HEADER INFLATE_FUNCTION)
        if(NOT _arg_${_required_argument})
            message(FATAL_ERROR
                "vellum_embed_web_compat_preludes requires ${_required_argument}")
        endif()
    endforeach()
    if(NOT IS_ABSOLUTE "${output_cpp}" OR NOT IS_ABSOLUTE "${output_header}")
        message(FATAL_ERROR "vellum_embed_web_compat_preludes requires absolute output paths")
    endif()
    foreach(_required "${VELLUM_WEB_COMPAT_EMBED_PRELUDES}" "${VELLUM_WEB_COMPAT_MANIFEST}")
        if(NOT EXISTS "${_required}")
            message(FATAL_ERROR "Vellum web-compat package file is missing: ${_required}")
        endif()
    endforeach()
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)

    file(READ "${VELLUM_WEB_COMPAT_MANIFEST}" _vellum_web_compat_manifest)
    _vellum_web_compat_verify(preludes "${VELLUM_WEB_COMPAT_PRELUDE_DIR}" _vellum_prelude_paths)
    _vellum_web_compat_verify(scripts "${VELLUM_WEB_COMPAT_SCRIPT_DIR}" _vellum_script_paths)
    # A changed install re-runs configure, so a stale verification cannot stand.
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${VELLUM_WEB_COMPAT_MANIFEST}" ${_vellum_prelude_paths} ${_vellum_script_paths})

    get_filename_component(_vellum_header_name "${output_header}" NAME)
    set(_vellum_decls "")
    string(APPEND _vellum_decls
        "#pragma once\n// Auto-generated from the Vellum web-compat manifest, do not edit\n\n"
        "#include <cstddef>\n#include <string>\n\nnamespace ${_arg_NAMESPACE} {\n\n"
        "struct EmbeddedPrelude {\n    const char* file_name;\n    const unsigned char* deflated;\n"
        "    std::size_t deflated_size;\n    std::size_t source_size;\n    const char* sha256;\n};\n\n"
        "// Inflates the source text of a prelude on each call; throws std::runtime_error\n"
        "// when the stream is corrupt.\nstd::string source(const EmbeddedPrelude& prelude);\n\n")
    foreach(_vellum_prelude_path IN LISTS _vellum_prelude_paths)
        get_filename_component(_vellum_prelude_name "${_vellum_prelude_path}" NAME_WE)
        string(REPLACE "-" "_" _vellum_prelude_symbol "${_vellum_prelude_name}")
        string(APPEND _vellum_decls "extern const EmbeddedPrelude ${_vellum_prelude_symbol};\n")
    endforeach()
    string(APPEND _vellum_decls
        "\nextern const EmbeddedPrelude* const kAll[];\nextern const std::size_t kAllCount;\n\n"
        "}  // namespace ${_arg_NAMESPACE}\n")
    set(_vellum_existing_decls "")
    if(EXISTS "${output_header}")
        file(READ "${output_header}" _vellum_existing_decls)
    endif()
    if(NOT "${_vellum_existing_decls}" STREQUAL "${_vellum_decls}")
        file(WRITE "${output_header}" "${_vellum_decls}")
    endif()

    add_custom_command(OUTPUT "${output_cpp}"
        COMMAND "${Python3_EXECUTABLE}" "${VELLUM_WEB_COMPAT_EMBED_PRELUDES}"
            --namespace "${_arg_NAMESPACE}"
            --header "${_vellum_header_name}"
            --inflate-header "${_arg_INFLATE_HEADER}"
            --inflate-function "${_arg_INFLATE_FUNCTION}"
            "${output_cpp}" ${_vellum_prelude_paths}
        DEPENDS "${VELLUM_WEB_COMPAT_EMBED_PRELUDES}"
            ${_vellum_prelude_paths}
        COMMENT "Embedding Vellum web-compat preludes"
        VERBATIM)
endfunction()
