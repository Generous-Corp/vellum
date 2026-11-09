# The RmlUi module's link boundary: of its library targets, only the one
# named ALLOW (VellumRmlUi::VellumRenderer) may link a Vellum target. Core,
# Native, Metal and the rest depend on RmlUi and the operating system only,
# so any RmlUi user can adopt them without Vellum.
#
#   vellum_rmlui_check_link_boundary(ALLOW <target> TARGETS <target>...)
#
# Reads each target's LINK_LIBRARIES and INTERFACE_LINK_LIBRARIES, including
# entries inside generator expressions, and fails the configure when one names
# Vellum::<x> or vellum-<x> (the module's own vellum-rmlui-* and
# VellumRmlUi::* targets excepted). When the ALLOW target exists it must
# itself link Vellum, so a check that read no link libraries cannot pass.
# Findings are printed as status lines prefixed "rmlui-link-boundary:" (never
# wrapped), then the configure fails.

function(_vellum_rmlui_links_vellum out entry)
    string(REPLACE "vellum-rmlui-" "" _stripped "${entry}")
    string(REPLACE "VellumRmlUi::" "" _stripped "${_stripped}")
    if(_stripped MATCHES "Vellum::[A-Za-z]" OR _stripped MATCHES "(^|[^A-Za-z0-9_])vellum-[A-Za-z]")
        set(${out} TRUE PARENT_SCOPE)
    else()
        set(${out} FALSE PARENT_SCOPE)
    endif()
endfunction()

function(vellum_rmlui_check_link_boundary)
    cmake_parse_arguments(ARG "" "ALLOW" "TARGETS" ${ARGN})
    set(_checked 0)
    set(_violations "")
    set(_allowed_links_vellum FALSE)
    foreach(_target IN LISTS ARG_TARGETS)
        if(NOT TARGET "${_target}")
            continue()
        endif()
        get_target_property(_type "${_target}" TYPE)
        if(NOT _type MATCHES "^(STATIC|SHARED|MODULE|OBJECT|INTERFACE)_LIBRARY$")
            continue()
        endif()
        set(_entries "")
        if(NOT _type STREQUAL "INTERFACE_LIBRARY")
            get_target_property(_link "${_target}" LINK_LIBRARIES)
            if(_link)
                list(APPEND _entries ${_link})
            endif()
        endif()
        get_target_property(_interface "${_target}" INTERFACE_LINK_LIBRARIES)
        if(_interface)
            list(APPEND _entries ${_interface})
        endif()
        math(EXPR _checked "${_checked} + 1")
        foreach(_entry IN LISTS _entries)
            _vellum_rmlui_links_vellum(_links "${_entry}")
            if(NOT _links)
                continue()
            endif()
            if(_target STREQUAL "${ARG_ALLOW}")
                set(_allowed_links_vellum TRUE)
            else()
                list(APPEND _violations "${_target} links ${_entry}")
            endif()
        endforeach()
    endforeach()
    # One unwrapped status line per finding, so tools can match it; then fail.
    foreach(_violation IN LISTS _violations)
        message(STATUS "rmlui-link-boundary: ${_violation}")
    endforeach()
    if(_violations)
        message(FATAL_ERROR "only ${ARG_ALLOW} may link Vellum (see rmlui-link-boundary above)")
    endif()
    if(TARGET "${ARG_ALLOW}" AND NOT _allowed_links_vellum)
        message(STATUS "rmlui-link-boundary: ${ARG_ALLOW} links no Vellum target")
        message(FATAL_ERROR "the link boundary check read no Vellum link; it cannot vouch for the others")
    endif()
    message(STATUS "rmlui-link-boundary: ${_checked} targets checked; only ${ARG_ALLOW} links Vellum")
endfunction()
