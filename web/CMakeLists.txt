add_executable(vellum-web-core
    src/web_runtime.cpp)

target_link_libraries(vellum-web-core PRIVATE Vellum::Graphics)
target_compile_features(vellum-web-core PRIVATE cxx_std_20)
set_target_properties(vellum-web-core PROPERTIES
    OUTPUT_NAME vellum_web_core
    SUFFIX ".js"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/web-dist")

target_link_options(vellum-web-core PRIVATE
    "-sMODULARIZE=1"
    "-sEXPORT_ES6=1"
    "-sENVIRONMENT=web,node"
    "-sINITIAL_MEMORY=16777216"
    "-sFILESYSTEM=0"
    "-sASSERTIONS=1"
    "-sEXPORTED_FUNCTIONS=['_vellum_web_start','_vellum_web_stop','_vellum_web_begin_frame','_vellum_web_add_rectangle','_vellum_web_add_text','_vellum_web_render','_vellum_web_command_count','_vellum_web_command_digest','_vellum_web_backend_name']"
    "-sEXPORTED_RUNTIME_METHODS=['cwrap']")

file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/web-dist/vellum-ui")
foreach(_vellum_web_asset index.html style.css demo.js)
    configure_file(
        "${CMAKE_CURRENT_SOURCE_DIR}/shell/${_vellum_web_asset}"
        "${CMAKE_BINARY_DIR}/web-dist/${_vellum_web_asset}"
        COPYONLY)
endforeach()
configure_file(
    "${PROJECT_SOURCE_DIR}/packages/vellum-ui/src/runtime.js"
    "${CMAKE_BINARY_DIR}/web-dist/vellum-ui/runtime.js"
    COPYONLY)

add_custom_command(TARGET vellum-web-core POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy_directory
        "${CMAKE_CURRENT_SOURCE_DIR}/shell"
        "$<TARGET_FILE_DIR:vellum-web-core>"
    COMMAND "${CMAKE_COMMAND}" -E make_directory
        "$<TARGET_FILE_DIR:vellum-web-core>/vellum-ui"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${PROJECT_SOURCE_DIR}/packages/vellum-ui/src/runtime.js"
        "$<TARGET_FILE_DIR:vellum-web-core>/vellum-ui/runtime.js"
    VERBATIM)

if(VELLUM_BUILD_TESTS)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME vellum.web.no-engine
        COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/check_wasm_no_engine.py"
            "$<TARGET_FILE_DIR:vellum-web-core>/vellum_web_core.wasm")
    add_test(NAME vellum.web.no-engine-negative-control
        COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/check_wasm_no_engine.py"
            --negative-control)
    add_test(NAME vellum.web.browser-smoke
        COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/run_browser_smoke.py"
            --root "$<TARGET_FILE_DIR:vellum-web-core>")
endif()
