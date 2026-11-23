# Only the two named bridges may link Vellum. The other libraries remain
# usable in standalone RmlUi builds.
#
#   vellum_rmlui_check_link_boundary(TARGETS <target>...)
#
# Reads each target's LINK_LIBRARIES and INTERFACE_LINK_LIBRARIES, including
# entries inside generator expressions, and fails the configure when one names
# Vellum::<x> or vellum-<x> (the module's own vellum-rmlui-* and
# VellumRmlUi::* targets excepted), directly or through an ALIAS target that
# resolves to one. Each bridge that exists must link Vellum, so an empty
# link check cannot pass.
# Findings are printed as status lines prefixed "rmlui-link-boundary:" (never
# wrapped), then the configure fails.

function(_vellum_rmlui_names_vellum out name)
    string(REPLACE "vellum-rmlui-" "" _stripped "${name}")
    string(REPLACE "VellumRmlUi::" "" _stripped "${_stripped}")
    if(_stripped MATCHES "Vellum::[A-Za-z]" OR _stripped MATCHES "(^|[^A-Za-z0-9_])vellum-[A-Za-z]")
        set(${out} TRUE PARENT_SCOPE)
    else()
        set(${out} FALSE PARENT_SCOPE)
    endif()
endfunction()

# Sets <out> to TRUE when <entry> links Vellum, by its own spelling or through
# an ALIAS: every target name in the entry (generator expressions included) is
# resolved to its ALIASED_TARGET, so `VellumRmlUi::Gpu` aliasing `Vellum::Gpu`
# is not hidden by the VellumRmlUi:: exception. <alias_out> names the aliases
# that resolved to Vellum ("<alias> -> <target>"), empty for a direct link.
function(_vellum_rmlui_links_vellum out alias_out entry)
    set(_aliases "")
    _vellum_rmlui_names_vellum(_direct "${entry}")
    string(REGEX MATCHALL "[A-Za-z0-9_.+-]+(::[A-Za-z0-9_.+-]+)*" _names "${entry}")
    foreach(_name IN LISTS _names)
        if(NOT TARGET "${_name}")
            continue()
        endif()
        get_target_property(_aliased "${_name}" ALIASED_TARGET)
        if(NOT _aliased)
            continue()
        endif()
        _vellum_rmlui_names_vellum(_aliased_links "${_aliased}")
        if(_aliased_links)
            list(APPEND _aliases "${_name} -> ${_aliased}")
        endif()
    endforeach()
    if(_direct OR _aliases)
        set(${out} TRUE PARENT_SCOPE)
    else()
        set(${out} FALSE PARENT_SCOPE)
    endif()
    set(${alias_out} "${_aliases}" PARENT_SCOPE)
endfunction()

function(vellum_rmlui_check_link_boundary)
    cmake_parse_arguments(ARG "" "" "TARGETS" ${ARGN})
    # VellumRenderer draws meshes through Vellum's GPU surface.
    # PluginView hosts CPU-rendered documents through Vellum's PluginViewHost.
    set(_bridges vellum-rmlui-vellum-renderer vellum-rmlui-plugin-view)
    set(_checked 0)
    set(_violations "")
    set(_linked_bridges "")
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
            _vellum_rmlui_links_vellum(_links _via "${_entry}")
            if(NOT _links)
                continue()
            endif()
            if(_target IN_LIST _bridges)
                list(APPEND _linked_bridges "${_target}")
            elseif(_via)
                string(REPLACE ";" ", " _via "${_via}")
                list(APPEND _violations "${_target} links ${_entry} (alias ${_via})")
            else()
                list(APPEND _violations "${_target} links ${_entry}")
            endif()
        endforeach()
    endforeach()
    # One unwrapped status line per finding, so tools can match it; then fail.
    foreach(_violation IN LISTS _violations)
        message(STATUS "rmlui-link-boundary: ${_violation}")
    endforeach()
    list(JOIN _bridges " and " _bridge_names)
    if(_violations)
        message(FATAL_ERROR "only ${_bridge_names} may link Vellum (see rmlui-link-boundary above)")
    endif()
    foreach(_bridge IN LISTS _bridges)
        if(TARGET "${_bridge}" AND NOT _bridge IN_LIST _linked_bridges)
            message(STATUS "rmlui-link-boundary: ${_bridge} links no Vellum target")
            message(FATAL_ERROR "the link boundary check read no Vellum link for ${_bridge}")
        endif()
    endforeach()
    message(STATUS "rmlui-link-boundary: ${_checked} targets checked; only ${_bridge_names} may link Vellum")
endfunction()
