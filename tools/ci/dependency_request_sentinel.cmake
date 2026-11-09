# Leaves one entry in the dependency request log, so a configure that requests
# no dependencies at all (Vellum's default build) still proves the recorder
# ran: dependency_request_check.py treats an empty log as a broken instrument.
#
# Inject it after the recorder, in the same list:
#
#   -DCMAKE_PROJECT_TOP_LEVEL_INCLUDES="<repo>/tools/ci/dependency_request_recorder.cmake;<repo>/tools/ci/dependency_request_sentinel.cmake"
#
# and require it: dependency_request_check.py --require recorder-loaded.
# It says the recorder was loaded for this configure, nothing more; that the
# recorder sees a given dependency is the job of a control configure that
# must request it.

if(COMMAND dependency_request_recorder_note)
    dependency_request_recorder_note(SENTINEL recorder-loaded)
endif()
