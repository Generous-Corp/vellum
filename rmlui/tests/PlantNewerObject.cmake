# Control for CheckDeploymentTarget.cmake: builds a prefix whose package records
# macOS 13.3 and whose archive holds one object built for macOS 27.0, then runs
# the check, which must fail. Registered with WILL_FAIL.
if(NOT VELLUM_RMLUI_CONTROL_DIR OR NOT VELLUM_RMLUI_C_COMPILER)
    message(FATAL_ERROR "VELLUM_RMLUI_CONTROL_DIR and VELLUM_RMLUI_C_COMPILER are required")
endif()
set(_prefix "${VELLUM_RMLUI_CONTROL_DIR}/prefix")
file(REMOVE_RECURSE "${VELLUM_RMLUI_CONTROL_DIR}")
file(MAKE_DIRECTORY "${_prefix}/lib/cmake/VellumRmlUi")
file(WRITE "${_prefix}/lib/cmake/VellumRmlUi/VellumRmlUiConfig.cmake"
    "set(VellumRmlUi_OSX_DEPLOYMENT_TARGET \"13.3\")\n")
file(WRITE "${VELLUM_RMLUI_CONTROL_DIR}/newer.c" "int vellum_rmlui_newer_object(void) { return 1; }\n")
execute_process(COMMAND "${VELLUM_RMLUI_C_COMPILER}" -c -mmacosx-version-min=27.0
        "${VELLUM_RMLUI_CONTROL_DIR}/newer.c" -o "${VELLUM_RMLUI_CONTROL_DIR}/newer.o"
    RESULT_VARIABLE _rc)
if(NOT _rc EQUAL 0)
    message(FATAL_ERROR "control setup: could not compile the planted object") # not the expected failure
endif()
execute_process(COMMAND ar rcs "${_prefix}/lib/libplanted.a" "${VELLUM_RMLUI_CONTROL_DIR}/newer.o"
    RESULT_VARIABLE _rc)
if(NOT _rc EQUAL 0)
    message(FATAL_ERROR "control setup: could not archive the planted object")
endif()
set(VELLUM_RMLUI_CHECK_PREFIX "${_prefix}")
set(VELLUM_RMLUI_CHECK_PACKAGE_DIR "${_prefix}/lib/cmake/VellumRmlUi")
include("${CMAKE_CURRENT_LIST_DIR}/CheckDeploymentTarget.cmake")
