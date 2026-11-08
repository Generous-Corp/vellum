# Controls for CheckDeploymentTarget.cmake: builds a prefix whose package
# records macOS 13.3 and whose archive holds one object, then runs the check.
#   VELLUM_RMLUI_CONTROL_FORM=build   the object carries LC_BUILD_VERSION
#   VELLUM_RMLUI_CONTROL_FORM=legacy  the object carries LC_VERSION_MIN_MACOSX
# VELLUM_RMLUI_CONTROL_VERSION is the object's minimum macOS. The registered
# tests match the check's output: a newer object must fail it, an older one in
# either form must pass.
if(NOT VELLUM_RMLUI_CONTROL_DIR OR NOT VELLUM_RMLUI_C_COMPILER OR
   NOT VELLUM_RMLUI_CONTROL_FORM OR NOT VELLUM_RMLUI_CONTROL_VERSION)
    message(FATAL_ERROR "control: directory, compiler, form and version are required")
endif()
set(_prefix "${VELLUM_RMLUI_CONTROL_DIR}/prefix")
file(REMOVE_RECURSE "${VELLUM_RMLUI_CONTROL_DIR}")
file(MAKE_DIRECTORY "${_prefix}/lib/cmake/VellumRmlUi")
file(WRITE "${_prefix}/lib/cmake/VellumRmlUi/VellumRmlUiConfig.cmake"
    "set(VellumRmlUi_OSX_DEPLOYMENT_TARGET \"13.3\")\n")
set(_object "${VELLUM_RMLUI_CONTROL_DIR}/planted.o")
if(VELLUM_RMLUI_CONTROL_FORM STREQUAL "build")
    file(WRITE "${VELLUM_RMLUI_CONTROL_DIR}/planted.c" "int vellum_rmlui_planted_object(void) { return 1; }\n")
    set(_compile "${VELLUM_RMLUI_C_COMPILER}" -c "-mmacosx-version-min=${VELLUM_RMLUI_CONTROL_VERSION}"
        "${VELLUM_RMLUI_CONTROL_DIR}/planted.c" -o "${_object}")
elseif(VELLUM_RMLUI_CONTROL_FORM STREQUAL "legacy")
    # The assembler's .macosx_version_min directive emits LC_VERSION_MIN_MACOSX.
    string(REPLACE "." ", " _version_args "${VELLUM_RMLUI_CONTROL_VERSION}")
    file(WRITE "${VELLUM_RMLUI_CONTROL_DIR}/planted.s"
        ".macosx_version_min ${_version_args}\n.globl _vellum_rmlui_planted_object\n_vellum_rmlui_planted_object:\n")
    set(_compile "${VELLUM_RMLUI_C_COMPILER}" -c "${VELLUM_RMLUI_CONTROL_DIR}/planted.s" -o "${_object}")
else()
    message(FATAL_ERROR "control: unknown form ${VELLUM_RMLUI_CONTROL_FORM}")
endif()
execute_process(COMMAND ${_compile} RESULT_VARIABLE _rc)
if(NOT _rc EQUAL 0)
    message(FATAL_ERROR "control setup: could not compile the planted object")
endif()
execute_process(COMMAND otool -l "${_object}" OUTPUT_VARIABLE _load)
if(VELLUM_RMLUI_CONTROL_FORM STREQUAL "legacy" AND NOT _load MATCHES "LC_VERSION_MIN_MACOSX")
    message(FATAL_ERROR "control setup: the planted object lacks LC_VERSION_MIN_MACOSX")
endif()
if(VELLUM_RMLUI_CONTROL_FORM STREQUAL "build" AND NOT _load MATCHES "LC_BUILD_VERSION")
    message(FATAL_ERROR "control setup: the planted object lacks LC_BUILD_VERSION")
endif()
execute_process(COMMAND ar rcs "${_prefix}/lib/libplanted.a" "${_object}" RESULT_VARIABLE _rc)
if(NOT _rc EQUAL 0)
    message(FATAL_ERROR "control setup: could not archive the planted object")
endif()
set(VELLUM_RMLUI_CHECK_PREFIX "${_prefix}")
set(VELLUM_RMLUI_CHECK_PACKAGE_DIR "${_prefix}/lib/cmake/VellumRmlUi")
include("${CMAKE_CURRENT_LIST_DIR}/CheckDeploymentTarget.cmake")
