# Fails when an installed library holds an object built for a newer macOS than
# the deployment target the package records. Include it with
# VELLUM_RMLUI_CHECK_PREFIX (the install prefix) and
# VELLUM_RMLUI_CHECK_PACKAGE_DIR (its lib/cmake/VellumRmlUi) set, or run it with
# -P and the same variables.
if(NOT VELLUM_RMLUI_CHECK_PREFIX OR NOT VELLUM_RMLUI_CHECK_PACKAGE_DIR)
    message(FATAL_ERROR "VELLUM_RMLUI_CHECK_PREFIX and VELLUM_RMLUI_CHECK_PACKAGE_DIR are required")
endif()
file(READ "${VELLUM_RMLUI_CHECK_PACKAGE_DIR}/VellumRmlUiConfig.cmake" _config)
string(REGEX MATCH "set\\(VellumRmlUi_OSX_DEPLOYMENT_TARGET \"([0-9.]+)\"\\)" _ "${_config}")
set(_recorded "${CMAKE_MATCH_1}")
if(NOT _recorded)
    message(FATAL_ERROR "the package records no macOS deployment target")
endif()
file(GLOB_RECURSE _libraries "${VELLUM_RMLUI_CHECK_PREFIX}/*.a" "${VELLUM_RMLUI_CHECK_PREFIX}/*.dylib")
if(NOT _libraries)
    message(FATAL_ERROR "no installed libraries to check the deployment target of")
endif()
foreach(_library IN LISTS _libraries)
    execute_process(COMMAND otool -l "${_library}" OUTPUT_VARIABLE _load RESULT_VARIABLE _rc)
    if(NOT _rc EQUAL 0)
        message(FATAL_ERROR "otool failed on ${_library}")
    endif()
    # LC_BUILD_VERSION carries "minos"; the older LC_VERSION_MIN_MACOSX carries
    # "version" directly after its cmd line.
    string(REGEX MATCHALL "minos [0-9.]+" _minimums "${_load}")
    if(NOT _minimums)
        message(FATAL_ERROR "no build version found in ${_library}")
    endif()
    foreach(_minimum IN LISTS _minimums)
        string(REPLACE "minos " "" _version "${_minimum}")
        if(_version VERSION_GREATER _recorded)
            message(FATAL_ERROR
                "${_library} has an object built for macOS ${_version}, newer than the "
                "recorded deployment target ${_recorded}")
        endif()
    endforeach()
endforeach()
