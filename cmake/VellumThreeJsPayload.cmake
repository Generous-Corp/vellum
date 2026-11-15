# Refuse a configure whose Three.js runtime payload does not match its pin.
# assets/threejs/payload-manifest.json names each file's SHA-256; a missing or
# changed file stops the configure rather than reaching a build or an SDK.
# VELLUM_THREEJS_DIR is the verified payload directory for tests and consumers.

set(VELLUM_THREEJS_DIR "${CMAKE_CURRENT_LIST_DIR}/../assets/threejs")
get_filename_component(VELLUM_THREEJS_DIR "${VELLUM_THREEJS_DIR}" ABSOLUTE)
set(_vellum_threejs_manifest "${VELLUM_THREEJS_DIR}/payload-manifest.json")
if(NOT EXISTS "${_vellum_threejs_manifest}")
    message(FATAL_ERROR "Vellum: ${_vellum_threejs_manifest} is missing")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_vellum_threejs_manifest}")
file(READ "${_vellum_threejs_manifest}" _vellum_threejs_json)
string(JSON _vellum_threejs_count LENGTH "${_vellum_threejs_json}" files)
math(EXPR _vellum_threejs_last "${_vellum_threejs_count} - 1")
foreach(_vellum_threejs_index RANGE 0 ${_vellum_threejs_last})
    string(JSON _vellum_threejs_file MEMBER "${_vellum_threejs_json}" files ${_vellum_threejs_index})
    string(JSON _vellum_threejs_expected GET "${_vellum_threejs_json}" files "${_vellum_threejs_file}")
    set(_vellum_threejs_path "${VELLUM_THREEJS_DIR}/${_vellum_threejs_file}")
    if(NOT EXISTS "${_vellum_threejs_path}")
        message(FATAL_ERROR "Vellum: the Three.js payload is missing ${_vellum_threejs_file}")
    endif()
    file(SHA256 "${_vellum_threejs_path}" _vellum_threejs_actual)
    if(NOT _vellum_threejs_actual STREQUAL _vellum_threejs_expected)
        message(FATAL_ERROR
            "Vellum: ${_vellum_threejs_file} does not match its pinned SHA-256 "
            "(expected ${_vellum_threejs_expected}, found ${_vellum_threejs_actual})")
    endif()
endforeach()
unset(_vellum_threejs_json)
unset(_vellum_threejs_count)
unset(_vellum_threejs_last)
unset(_vellum_threejs_index)
unset(_vellum_threejs_file)
unset(_vellum_threejs_expected)
unset(_vellum_threejs_actual)
unset(_vellum_threejs_path)
unset(_vellum_threejs_manifest)
