set(_vellum_rmlui_fixtures "${CMAKE_CURRENT_SOURCE_DIR}/fixtures")

function(vellum_rmlui_add_test name source)
    add_executable(${name} ${source})
    target_link_libraries(${name} PRIVATE ${ARGN})
    target_compile_definitions(${name} PRIVATE
        VELLUM_RMLUI_TEST_FIXTURES="${_vellum_rmlui_fixtures}")
endfunction()

vellum_rmlui_add_test(vellum-rmlui-backend-test backend_test.cpp VellumRmlUi::Core)
# Vellum's packaged Inter face, when this module sits inside a Vellum checkout.
set(_vellum_rmlui_test_font "${CMAKE_CURRENT_SOURCE_DIR}/../../assets/fonts/Inter-Regular.ttf")
if(VELLUM_RMLUI_FONTS AND EXISTS "${_vellum_rmlui_test_font}")
    target_compile_definitions(vellum-rmlui-backend-test PRIVATE
        VELLUM_RMLUI_TEST_FONT="${_vellum_rmlui_test_font}")
endif()
add_test(NAME vellum.rmlui.backend COMMAND vellum-rmlui-backend-test)

vellum_rmlui_add_test(vellum-rmlui-two-instances-test two_instances_test.cpp VellumRmlUi::Core)
add_test(NAME vellum.rmlui.two-instances COMMAND vellum-rmlui-two-instances-test)

vellum_rmlui_add_test(vellum-rmlui-hot-reload-test hot_reload_test.cpp VellumRmlUi::DevAssets)
add_test(NAME vellum.rmlui.hot-reload COMMAND vellum-rmlui-hot-reload-test)

# The golden image is the font-off rendering; a FreeType build lays out the
# same document identically, since it holds no text.
vellum_rmlui_add_test(vellum-rmlui-soft-render-golden-test soft_render_golden_test.cpp
    VellumRmlUi::SoftRenderer)
add_test(NAME vellum.rmlui.soft-render-golden COMMAND vellum-rmlui-soft-render-golden-test)
set_tests_properties(vellum.rmlui.soft-render-golden PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}")

if(VELLUM_RMLUI_LUA)
    vellum_rmlui_add_test(vellum-rmlui-lua-test lua_test.cpp VellumRmlUi::Lua)
    # The sandbox's internal guards are tested directly, against the same
    # pinned Lua the host links.
    target_include_directories(vellum-rmlui-lua-test PRIVATE "${PROJECT_SOURCE_DIR}/src")
    target_link_libraries(vellum-rmlui-lua-test PRIVATE vellum-rmlui-lua54)
    add_test(NAME vellum.rmlui.lua COMMAND vellum-rmlui-lua-test)
    # An unbounded script must end in a budget error; the timeout is an
    # independent outer bound should a guard ever fail to fire.
    set_tests_properties(vellum.rmlui.lua PROPERTIES TIMEOUT 30)
endif()

if(VELLUM_RMLUI_INSTALL AND NOT CMAKE_CROSSCOMPILING)
    add_test(NAME vellum.rmlui.install-consumer
        COMMAND "${CMAKE_COMMAND}"
            "-DVELLUM_RMLUI_BUILD_DIR=${PROJECT_BINARY_DIR}"
            "-DVELLUM_RMLUI_SOURCE_DIR=${PROJECT_SOURCE_DIR}"
            "-DVELLUM_RMLUI_INSTALL_LIBDIR=${CMAKE_INSTALL_LIBDIR}"
            "-DTEST_CONFIG=$<CONFIG>"
            -P "${CMAKE_CURRENT_SOURCE_DIR}/RunInstallConsumer.cmake")
endif()
