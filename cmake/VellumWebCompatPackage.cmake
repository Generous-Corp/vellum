# Consumer helper for Vellum's installed web-compat package.
set(VELLUM_WEB_COMPAT_ROOT "${CMAKE_CURRENT_LIST_DIR}")
set(VELLUM_WEB_COMPAT_PRELUDE_DIR "${VELLUM_WEB_COMPAT_ROOT}/preludes")
set(VELLUM_WEB_COMPAT_EMBED_JS "${VELLUM_WEB_COMPAT_ROOT}/embed_js.py")
set(VELLUM_WEB_COMPAT_MANIFEST "${VELLUM_WEB_COMPAT_ROOT}/vellum-web-compat-manifest.json")

function(vellum_embed_web_compat_preludes output_cpp output_header)
    if(NOT IS_ABSOLUTE "${output_cpp}" OR NOT IS_ABSOLUTE "${output_header}")
        message(FATAL_ERROR "vellum_embed_web_compat_preludes requires absolute output paths")
    endif()
    if(NOT EXISTS "${VELLUM_WEB_COMPAT_EMBED_JS}")
        message(FATAL_ERROR "Vellum web-compat embed_js.py is missing: ${VELLUM_WEB_COMPAT_EMBED_JS}")
    endif()
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    if(NOT EXISTS "${VELLUM_WEB_COMPAT_MANIFEST}")
        message(FATAL_ERROR "Vellum web-compat manifest is missing: ${VELLUM_WEB_COMPAT_MANIFEST}")
    endif()
    file(READ "${VELLUM_WEB_COMPAT_MANIFEST}" _vellum_web_compat_manifest)
    string(JSON _vellum_prelude_count LENGTH "${_vellum_web_compat_manifest}" preludes)
    if(_vellum_prelude_count LESS 1)
        message(FATAL_ERROR "Vellum web-compat manifest contains no preludes")
    endif()
    math(EXPR _vellum_prelude_last "${_vellum_prelude_count} - 1")
    set(_vellum_prelude_paths "")
    foreach(_vellum_prelude_index RANGE ${_vellum_prelude_last})
        string(JSON _vellum_prelude_name GET "${_vellum_web_compat_manifest}" preludes ${_vellum_prelude_index} name)
        if(NOT EXISTS "${VELLUM_WEB_COMPAT_PRELUDE_DIR}/${_vellum_prelude_name}")
            message(FATAL_ERROR "Vellum web-compat prelude is missing: ${_vellum_prelude_name}")
        endif()
        list(APPEND _vellum_prelude_paths "${VELLUM_WEB_COMPAT_PRELUDE_DIR}/${_vellum_prelude_name}")
    endforeach()
    file(WRITE "${output_header}"
        "#pragma once\n#include <cstddef>\n#include <string>\n\n"
        "namespace vellum::view::preludes {\n"
        "struct EmbeddedPrelude { const char* file_name; const unsigned char* deflated; std::size_t deflated_size; std::size_t source_size; const char* sha256; };\n"
        "std::string source(const EmbeddedPrelude& prelude);\n")
    foreach(_vellum_prelude_path IN LISTS _vellum_prelude_paths)
        get_filename_component(_vellum_prelude_name "${_vellum_prelude_path}" NAME_WE)
        string(REPLACE "-" "_" _vellum_prelude_symbol "${_vellum_prelude_name}")
        file(APPEND "${output_header}" "extern const EmbeddedPrelude ${_vellum_prelude_symbol};\n")
    endforeach()
    file(APPEND "${output_header}"
        "extern const EmbeddedPrelude* const kAll[];\nextern const std::size_t kAllCount;\n}\n")
    add_custom_command(OUTPUT "${output_cpp}"
        COMMAND "${Python3_EXECUTABLE}" "${VELLUM_WEB_COMPAT_EMBED_JS}"
            "${output_cpp}" ${_vellum_prelude_paths}
        DEPENDS "${VELLUM_WEB_COMPAT_EMBED_JS}" ${_vellum_prelude_paths}
        COMMENT "Embedding Vellum web-compat preludes"
        VERBATIM)
endfunction()
