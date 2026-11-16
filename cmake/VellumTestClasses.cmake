# Gives every registered test exactly one of three class labels, read from
# scripts/ctest_classes.json:
#
#   pr-fast  no GPU adapter, finishes quickly; the pull-request lane runs
#            `ctest -L pr-fast`
#   gpu      needs a GPU adapter (or Dawn) and runs with the GPU proof
#   slow     needs no adapter but takes long enough to run with the GPU proof
#
# A test that is registered and missing from the file, or listed under two
# classes, fails the configure, so a new test cannot silently fall outside
# every lane. The class is appended to any label the test already carries.

set(_vellum_test_classes pr-fast gpu slow)

function(_vellum_collect_directories directory out_var)
    set(_all "${directory}")
    get_property(_children DIRECTORY "${directory}" PROPERTY SUBDIRECTORIES)
    foreach(_child IN LISTS _children)
        _vellum_collect_directories("${_child}" _nested)
        list(APPEND _all ${_nested})
    endforeach()
    set(${out_var} "${_all}" PARENT_SCOPE)
endfunction()

# vellum_apply_test_classes(<manifest>)
# Call once, after the last add_subdirectory(), from Vellum's top-level directory.
function(vellum_apply_test_classes manifest)
    # Vellum tests disabled: nothing of ours is registered, so nothing to label.
    if(DEFINED VELLUM_BUILD_TESTS AND NOT VELLUM_BUILD_TESTS)
        return()
    endif()
    # The project still supports CMake 3.24; set_property(TEST ... DIRECTORY)
    # needs 3.28, so older versions skip labelling instead of failing configure.
    # VELLUM_TEST_CLASSES_CMAKE_VERSION overrides the version for the regression test.
    if(NOT DEFINED VELLUM_TEST_CLASSES_CMAKE_VERSION)
        set(VELLUM_TEST_CLASSES_CMAKE_VERSION "${CMAKE_VERSION}")
    endif()
    if(VELLUM_TEST_CLASSES_CMAKE_VERSION VERSION_LESS 3.28)
        message(STATUS
            "Vellum test class labels skipped: CMake ${VELLUM_TEST_CLASSES_CMAKE_VERSION} < 3.28")
        return()
    endif()
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${manifest}")
    file(READ "${manifest}" _json)
    foreach(_class IN LISTS _vellum_test_classes)
        string(JSON _count LENGTH "${_json}" classes "${_class}")
        set(_names_${_class} "")
        if(_count GREATER 0)
            math(EXPR _last "${_count} - 1")
            foreach(_index RANGE ${_last})
                string(JSON _name GET "${_json}" classes "${_class}" ${_index})
                list(APPEND _names_${_class} "${_name}")
            endforeach()
        endif()
    endforeach()

    # Only Vellum's own subtree: an embedding project's tests are not ours.
    _vellum_collect_directories("${CMAKE_CURRENT_SOURCE_DIR}" _directories)
    set(_problems "")
    foreach(_directory IN LISTS _directories)
        get_property(_tests DIRECTORY "${_directory}" PROPERTY TESTS)
        foreach(_test IN LISTS _tests)
            set(_found "")
            foreach(_class IN LISTS _vellum_test_classes)
                if(_test IN_LIST _names_${_class})
                    list(APPEND _found "${_class}")
                endif()
            endforeach()
            list(LENGTH _found _found_count)
            if(_found_count EQUAL 1)
                set_property(TEST "${_test}" DIRECTORY "${_directory}"
                    APPEND PROPERTY LABELS "${_found}")
            elseif(_found_count EQUAL 0)
                list(APPEND _problems "  ${_test}: not in ${manifest}")
            else()
                list(JOIN _found "," _joined)
                list(APPEND _problems "  ${_test}: listed under more than one class (${_joined})")
            endif()
        endforeach()
    endforeach()
    if(_problems)
        list(JOIN _problems "\n" _report)
        message(FATAL_ERROR
            "Every test needs exactly one of pr-fast, gpu or slow in "
            "scripts/ctest_classes.json:\n${_report}")
    endif()
endfunction()
