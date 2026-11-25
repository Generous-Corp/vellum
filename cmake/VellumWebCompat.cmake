# Harness and host packaging resources are loaded explicitly by their consumers.
# They must not be embedded or evaluated by the native WidgetBridge startup.
set(_vellum_web_compat_source_dir
    "${CMAKE_CURRENT_LIST_DIR}/../framework/core/view/js")
set(_vellum_web_compat_preludes
    css-colors.js
    css-parser.js
    web-compat-element.js
    web-compat-element-events.js
    web-compat-canvas.js
    web-compat-canvas-gpu.js
    web-compat-canvas-matrix.js
    web-compat-canvas-image.js
    web-compat-style-dedup-table.js
    web-compat-style-decl.js
    web-compat-style-decl-layout.js
    web-compat-style-decl-paint.js
    web-compat-style-decl-typography.js
    web-compat-style-decl-transform.js
    web-compat-style-decl-misc.js
    web-compat-style-decl-helpers.js
    web-compat-animation.js
    web-compat-document.js
    web-compat-transient-interaction.js
    web-compat-document-selectors.js
    web-compat-document-gpu-mock.js
    web-compat-dom-ops.js
    web-compat-gpu-buffered.js
    web-compat-observers.js
    web-compat-scheduler.js
    import-runtime.js)
set(_vellum_web_compat_scripts
    web-compat.js
    web-compat-selector.js
    web-compat-runtime-apis.js
    web-compat-three-shim.js)
set(_vellum_web_compat_install_root
    "${CMAKE_INSTALL_LIBDIR}/cmake/Vellum/web-compat")
set(_vellum_web_compat_script_paths "")
foreach(_vellum_web_compat_script IN LISTS _vellum_web_compat_scripts)
    list(APPEND _vellum_web_compat_script_paths
        "${_vellum_web_compat_source_dir}/${_vellum_web_compat_script}")
endforeach()

# Keep the existing four-script location for current consumers.
install(FILES
    ${_vellum_web_compat_script_paths}
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/Vellum/scripts")

# The SDK's versioned web-compat subtree is the stable input package for
# integrations/pulp-vellum. Keep the source filenames and order identical to
# the embedded registry so generated C++ remains deterministic.
set(_vellum_web_compat_prelude_paths "")
foreach(_vellum_web_compat_prelude IN LISTS _vellum_web_compat_preludes)
    list(APPEND _vellum_web_compat_prelude_paths
        "${_vellum_web_compat_source_dir}/${_vellum_web_compat_prelude}")
endforeach()
# The preludes are mirrored framework source and keep its names byte for byte,
# so they install under the mirror scope the SDK payload rules reserve for it.
install(FILES ${_vellum_web_compat_prelude_paths}
    DESTINATION "mirror/share/web-compat/preludes")
include(CMakePackageConfigHelpers)
configure_package_config_file(
    "${CMAKE_CURRENT_LIST_DIR}/VellumWebCompatPackage.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/VellumWebCompatPackage.cmake"
    INSTALL_DESTINATION "${_vellum_web_compat_install_root}"
    NO_SET_AND_CHECK_MACRO
    NO_CHECK_REQUIRED_COMPONENTS_MACRO)
install(FILES
    "${CMAKE_CURRENT_LIST_DIR}/vellum_embed_preludes.py"
    "${CMAKE_CURRENT_BINARY_DIR}/VellumWebCompatPackage.cmake"
    DESTINATION "${_vellum_web_compat_install_root}")

# The manifest records content hashes, so a changed prelude must re-run
# configure or the installed manifest goes stale.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    ${_vellum_web_compat_prelude_paths}
    ${_vellum_web_compat_script_paths}
    "${CMAKE_CURRENT_LIST_DIR}/vellum_embed_preludes.py")

find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(_vellum_web_compat_manifest "{\n  \"schema\": \"vellum.web-compat.v1\",\n  \"version\": \"${PROJECT_VERSION}\",\n  \"preludes\": [\n")
list(LENGTH _vellum_web_compat_preludes _vellum_web_compat_prelude_count)
set(_vellum_web_compat_index 0)
foreach(_vellum_web_compat_prelude IN LISTS _vellum_web_compat_preludes)
    file(SHA256 "${_vellum_web_compat_source_dir}/${_vellum_web_compat_prelude}"
        _vellum_web_compat_sha)
    math(EXPR _vellum_web_compat_index "${_vellum_web_compat_index} + 1")
    set(_vellum_web_compat_comma ",")
    if(_vellum_web_compat_index EQUAL _vellum_web_compat_prelude_count)
        set(_vellum_web_compat_comma "")
    endif()
    string(APPEND _vellum_web_compat_manifest
        "    {\"name\": \"${_vellum_web_compat_prelude}\", \"sha256\": \"${_vellum_web_compat_sha}\"}${_vellum_web_compat_comma}\n")
endforeach()
string(APPEND _vellum_web_compat_manifest "  ],\n  \"scripts\": [\n")
list(LENGTH _vellum_web_compat_scripts _vellum_web_compat_script_count)
set(_vellum_web_compat_index 0)
foreach(_vellum_web_compat_script IN LISTS _vellum_web_compat_scripts)
    file(SHA256 "${_vellum_web_compat_source_dir}/${_vellum_web_compat_script}"
        _vellum_web_compat_sha)
    math(EXPR _vellum_web_compat_index "${_vellum_web_compat_index} + 1")
    set(_vellum_web_compat_comma ",")
    if(_vellum_web_compat_index EQUAL _vellum_web_compat_script_count)
        set(_vellum_web_compat_comma "")
    endif()
    string(APPEND _vellum_web_compat_manifest
        "    {\"name\": \"${_vellum_web_compat_script}\", \"sha256\": \"${_vellum_web_compat_sha}\"}${_vellum_web_compat_comma}\n")
endforeach()
string(APPEND _vellum_web_compat_manifest "  ]\n}\n")
set(_vellum_web_compat_manifest_file
    "${CMAKE_CURRENT_BINARY_DIR}/vellum-web-compat-manifest.json")
set(_vellum_web_compat_existing_manifest "")
if(EXISTS "${_vellum_web_compat_manifest_file}")
    file(READ "${_vellum_web_compat_manifest_file}" _vellum_web_compat_existing_manifest)
endif()
if(NOT "${_vellum_web_compat_existing_manifest}" STREQUAL "${_vellum_web_compat_manifest}")
    file(WRITE "${_vellum_web_compat_manifest_file}" "${_vellum_web_compat_manifest}")
endif()
install(FILES "${_vellum_web_compat_manifest_file}"
    DESTINATION "${_vellum_web_compat_install_root}")
