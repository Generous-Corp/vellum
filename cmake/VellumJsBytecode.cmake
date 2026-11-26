# Build-time QuickJS bytecode for scripted UIs.
#
# vellum_add_js_bytecode(<target>
#     SOURCES <script.js>...
#     [NAMESPACE <ns>]                # default: <target>
#     [WIDGET_BRIDGE_SCRIPT])         # compile as WidgetBridge::load_script() evaluates
#
# Compiles each script to a bytecode blob during the build and embeds it in a
# static library <target>. The generated header <target>_data.hpp declares,
# per script `runtime.js`:
#
#     namespace <ns> {
#         extern const unsigned char runtime_js_vjsbc[];
#         extern const std::size_t   runtime_js_vjsbc_size;
#     }
#
# Hand each blob to register_embedded_script_bytecode() (declared by the view
# unit's js_engine.hpp) before the editor opens. A blob is used only for the exact source it was compiled
# from and only by the engine build that compiled it; otherwise the script
# compiles from source as before.
#
# The private generator executable is built by the consumer against
# Vellum::MirrorView_script, the unit the product links, so its output always
# matches the product's engine. When cross-compiling, the generator cannot run
# on the build machine: the library is still created but holds placeholder
# blobs, which register_embedded_script_bytecode() rejects.
#
# An installed package finds the generator source and the encoder under its
# mirror/share/js-bytecode directory. A build tree sets VELLUM_JS_BYTECODE_GENERATOR_SOURCE and
# VELLUM_JS_BYTECODE_ENCODER, and may add include directories and link
# libraries the generator needs through VELLUM_JS_BYTECODE_EXTRA_INCLUDES and
# VELLUM_JS_BYTECODE_EXTRA_LINK_LIBRARIES.

include_guard(GLOBAL)

# Captured when the package config includes this file, where PACKAGE_PREFIX_DIR
# is the configured install prefix even for a nested library directory.
if(DEFINED PACKAGE_PREFIX_DIR)
    set_property(GLOBAL PROPERTY VELLUM_JS_BYTECODE_SHARE_DIR
        "${PACKAGE_PREFIX_DIR}/mirror/share/js-bytecode")
endif()

function(_vellum_js_bytecode_generator out_var)
    get_property(_vellum_js_bytecode_dir GLOBAL PROPERTY VELLUM_JS_BYTECODE_SHARE_DIR)
    get_property(_generator GLOBAL PROPERTY VELLUM_JS_BYTECODE_GENERATOR_TARGET)
    if(_generator AND TARGET "${_generator}")
        set(${out_var} "${_generator}" PARENT_SCOPE)
        return()
    endif()
    set(_source "${VELLUM_JS_BYTECODE_GENERATOR_SOURCE}")
    if(NOT _source)
        set(_source "${_vellum_js_bytecode_dir}/vellum_js_bytecode_main.cpp")
    endif()
    if(NOT EXISTS "${_source}")
        message(FATAL_ERROR
            "vellum_add_js_bytecode: generator source not found at ${_source}. "
            "Re-install the Vellum SDK: it ships the source under mirror/share/js-bytecode.")
    endif()
    if(NOT TARGET Vellum::MirrorView_script)
        message(FATAL_ERROR
            "vellum_add_js_bytecode: the Vellum view-script unit is not available "
            "(Vellum::MirrorView_script)")
    endif()
    set(_generator "_vellum_js_bytecode_generator")
    if(TARGET "${_generator}")
        string(SHA256 _suffix "${CMAKE_CURRENT_BINARY_DIR};${_source}")
        string(SUBSTRING "${_suffix}" 0 16 _suffix)
        set(_generator "_vellum_js_bytecode_generator_${_suffix}")
        set(_suffix_index 0)
        while(TARGET "${_generator}")
            math(EXPR _suffix_index "${_suffix_index} + 1")
            set(_generator "_vellum_js_bytecode_generator_${_suffix}_${_suffix_index}")
        endwhile()
    endif()
    add_executable("${_generator}" "${_source}")
    target_link_libraries("${_generator}" PRIVATE Vellum::MirrorView_script
        ${VELLUM_JS_BYTECODE_EXTRA_LINK_LIBRARIES})
    if(VELLUM_JS_BYTECODE_EXTRA_INCLUDES)
        target_include_directories("${_generator}" PRIVATE ${VELLUM_JS_BYTECODE_EXTRA_INCLUDES})
    endif()
    target_compile_features("${_generator}" PRIVATE cxx_std_20)
    set_property(GLOBAL PROPERTY VELLUM_JS_BYTECODE_GENERATOR_TARGET "${_generator}")
    set(${out_var} "${_generator}" PARENT_SCOPE)
endfunction()

function(vellum_add_js_bytecode target)
    get_property(_vellum_js_bytecode_dir GLOBAL PROPERTY VELLUM_JS_BYTECODE_SHARE_DIR)
    cmake_parse_arguments(JSBC "WIDGET_BRIDGE_SCRIPT" "NAMESPACE" "SOURCES" ${ARGN})
    if(JSBC_UNPARSED_ARGUMENTS)
        message(FATAL_ERROR
            "vellum_add_js_bytecode(${target}): unknown arguments: ${JSBC_UNPARSED_ARGUMENTS}")
    endif()
    if(NOT JSBC_SOURCES)
        message(FATAL_ERROR "vellum_add_js_bytecode(${target}): SOURCES is required.")
    endif()
    if(NOT JSBC_NAMESPACE)
        set(JSBC_NAMESPACE "${target}")
    endif()

    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    set(_encoder "${VELLUM_JS_BYTECODE_ENCODER}")
    if(NOT _encoder)
        set(_encoder "${_vellum_js_bytecode_dir}/encode_binary_data.py")
    endif()
    if(NOT EXISTS "${_encoder}")
        message(FATAL_ERROR "vellum_add_js_bytecode: encode_binary_data.py not found at ${_encoder}")
    endif()

    set(_generator "")
    if(NOT CMAKE_CROSSCOMPILING)
        _vellum_js_bytecode_generator(_generator)
    else()
        message(STATUS "vellum_add_js_bytecode(${target}): cross-compiling; scripts "
                       "will compile from source at run time")
    endif()

    set(_flags "")
    if(JSBC_WIDGET_BRIDGE_SCRIPT)
        set(_flags --widget-bridge-script)
    endif()

    set(_out_dir "${CMAKE_CURRENT_BINARY_DIR}/${target}_vjsbc")
    file(MAKE_DIRECTORY "${_out_dir}")
    set(_hpp "#pragma once\n#include <cstddef>\n\nnamespace ${JSBC_NAMESPACE} {\n\n")
    set(_cpps "")
    set(_seen_output_keys "")
    set(_seen_vars "")
    foreach(_script IN LISTS JSBC_SOURCES)
        get_filename_component(_abs "${_script}" ABSOLUTE BASE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
        get_filename_component(_name "${_script}" NAME)
        string(TOLOWER "${_name}" _output_key)
        if(_output_key IN_LIST _seen_output_keys)
            message(FATAL_ERROR
                "vellum_add_js_bytecode(${target}): SOURCES contain duplicate generated "
                "output basename '${_name}' (output names must be unique case-insensitively).")
        endif()
        list(APPEND _seen_output_keys "${_output_key}")
        string(MAKE_C_IDENTIFIER "${_name}_vjsbc" _var)
        if(_var IN_LIST _seen_vars)
            message(FATAL_ERROR
                "vellum_add_js_bytecode(${target}): SOURCES contain duplicate generated name "
                "'${_var}' (from '${_name}'); source basenames must be unique after normalization.")
        endif()
        list(APPEND _seen_vars "${_var}")
        set(_blob "${_out_dir}/${_name}.vjsbc")
        set(_cpp "${_out_dir}/${_var}.cpp")
        if(_generator)
            add_custom_command(
                OUTPUT "${_blob}"
                COMMAND $<TARGET_FILE:${_generator}> ${_flags} "${_abs}" "${_blob}"
                DEPENDS "${_abs}" ${_generator}
                COMMENT "Compiling ${_name} to QuickJS bytecode"
                VERBATIM)
        else()
            # One byte, not empty: a zero-length array does not compile everywhere.
            file(WRITE "${_out_dir}/unavailable.vjsbc" "-")
            add_custom_command(
                OUTPUT "${_blob}"
                COMMAND "${CMAKE_COMMAND}" -E copy "${_out_dir}/unavailable.vjsbc" "${_blob}"
                DEPENDS "${_abs}"
                VERBATIM)
        endif()
        add_custom_command(
            OUTPUT "${_cpp}"
            COMMAND "${Python3_EXECUTABLE}" "${_encoder}"
                    --src "${_blob}" --out "${_cpp}"
                    --namespace "${JSBC_NAMESPACE}" --var "${_var}"
            DEPENDS "${_blob}" "${_encoder}"
            COMMENT "Embedding ${_name} bytecode into ${target}"
            VERBATIM)
        list(APPEND _cpps "${_cpp}")
        string(APPEND _hpp "extern const unsigned char ${_var}[];\n")
        string(APPEND _hpp "extern const std::size_t ${_var}_size;\n\n")
    endforeach()
    string(APPEND _hpp "}  // namespace ${JSBC_NAMESPACE}\n")

    set(_hpp_path "${CMAKE_CURRENT_BINARY_DIR}/${target}_data.hpp")
    set(_existing "")
    if(EXISTS "${_hpp_path}")
        file(READ "${_hpp_path}" _existing)
    endif()
    if(NOT "${_existing}" STREQUAL "${_hpp}")
        file(WRITE "${_hpp_path}" "${_hpp}")
    endif()

    add_library(${target} STATIC ${_cpps})
    target_include_directories(${target} PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}>)
    set_target_properties(${target} PROPERTIES LINKER_LANGUAGE CXX)
endfunction()
