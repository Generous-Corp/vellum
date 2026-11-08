# Applies this module's patches to the pinned RmlUi source tree. Run as the
# FetchContent PATCH_COMMAND from the source directory. A patch already applied
# (a re-run of the populate step) is skipped; one that applies neither forwards
# nor in reverse fails the configure.
if(NOT DEFINED VELLUM_RMLUI_PATCHES)
    message(FATAL_ERROR "VELLUM_RMLUI_PATCHES was not supplied")
endif()
find_package(Git REQUIRED)
foreach(patch IN LISTS VELLUM_RMLUI_PATCHES)
    execute_process(COMMAND "${GIT_EXECUTABLE}" apply --check --reverse "${patch}"
        RESULT_VARIABLE already_applied OUTPUT_QUIET ERROR_QUIET)
    if(already_applied EQUAL 0)
        continue()
    endif()
    execute_process(COMMAND "${GIT_EXECUTABLE}" apply "${patch}" RESULT_VARIABLE result)
    if(NOT result EQUAL 0)
        message(FATAL_ERROR "RmlUi patch does not apply to the pinned source: ${patch}")
    endif()
endforeach()
