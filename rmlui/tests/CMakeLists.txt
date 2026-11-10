set(_vellum_rmlui_fixtures "${CMAKE_CURRENT_SOURCE_DIR}/fixtures")

function(vellum_rmlui_add_test name source)
    add_executable(${name} ${source})
    target_link_libraries(${name} PRIVATE ${ARGN})
    target_compile_definitions(${name} PRIVATE
        VELLUM_RMLUI_TEST_FIXTURES="${_vellum_rmlui_fixtures}")
endfunction()

vellum_rmlui_add_test(vellum-rmlui-backend-test backend_test.cpp VellumRmlUi::Core)

# The link boundary check (cmake/VellumRmlUiLinkBoundary.cmake), on a stand-in
# project: clean passes, and each planted violation fails it with its own
# message, so a check that cannot see a link cannot pass.
foreach(_case IN ITEMS clean private genex alias unlinked_renderer)
    set(_name "vellum.rmlui.link-boundary-${_case}")
    add_test(NAME "${_name}"
        COMMAND "${CMAKE_COMMAND}"
            -S "${CMAKE_CURRENT_SOURCE_DIR}/link_boundary_control"
            -B "${CMAKE_CURRENT_BINARY_DIR}/link-boundary-${_case}"
            "-DVELLUM_RMLUI_MODULE_DIR=${PROJECT_SOURCE_DIR}"
            "-DPLANT=${_case}")
    if(_case STREQUAL "clean")
        set(_expect "rmlui-link-boundary: 3 targets checked; only vellum-rmlui-vellum-renderer links Vellum")
    elseif(_case STREQUAL "private")
        set(_expect "rmlui-link-boundary: vellum-rmlui-native links Vellum::Gpu")
    elseif(_case STREQUAL "genex")
        set(_expect "rmlui-link-boundary: vellum-rmlui-core links [^\n]*Vellum::Gpu")
    elseif(_case STREQUAL "alias")
        set(_expect "rmlui-link-boundary: vellum-rmlui-native links VellumRmlUi::Gpu \\(alias VellumRmlUi::Gpu -> Vellum::Gpu\\)")
    else()
        set(_expect "rmlui-link-boundary: vellum-rmlui-vellum-renderer links no Vellum target")
    endif()
    set_tests_properties("${_name}" PROPERTIES PASS_REGULAR_EXPRESSION "${_expect}")
endforeach()
# Vellum's packaged Inter face, when this module sits inside a Vellum checkout.
set(_vellum_rmlui_test_font "${CMAKE_CURRENT_SOURCE_DIR}/../../assets/fonts/Inter-Regular.ttf")
if(VELLUM_RMLUI_FONTS AND EXISTS "${_vellum_rmlui_test_font}")
    target_compile_definitions(vellum-rmlui-backend-test PRIVATE
        VELLUM_RMLUI_TEST_FONT="${_vellum_rmlui_test_font}")
endif()
add_test(NAME vellum.rmlui.backend COMMAND vellum-rmlui-backend-test)

vellum_rmlui_add_test(vellum-rmlui-image-compare-test image_compare_test.cpp VellumRmlUi::Core)
add_test(NAME vellum.rmlui.image-compare COMMAND vellum-rmlui-image-compare-test)

vellum_rmlui_add_test(vellum-rmlui-midi-learn-test midi_learn_test.cpp VellumRmlUi::Core)
target_compile_definitions(vellum-rmlui-midi-learn-test PRIVATE
    VELLUM_RMLUI_MIDI_LEARN_RCSS="${CMAKE_CURRENT_SOURCE_DIR}/../assets/midi-learn.rcss")
add_test(NAME vellum.rmlui.midi-learn COMMAND vellum-rmlui-midi-learn-test)

vellum_rmlui_add_test(vellum-rmlui-midi-panic-test midi_panic_test.cpp VellumRmlUi::Core)
target_compile_definitions(vellum-rmlui-midi-panic-test PRIVATE
    VELLUM_RMLUI_MIDI_PANIC_RCSS="${CMAKE_CURRENT_SOURCE_DIR}/../assets/midi-panic.rcss")
add_test(NAME vellum.rmlui.midi-panic COMMAND vellum-rmlui-midi-panic-test)

vellum_rmlui_add_test(vellum-rmlui-two-instances-test two_instances_test.cpp VellumRmlUi::Core)
add_test(NAME vellum.rmlui.two-instances COMMAND vellum-rmlui-two-instances-test)

vellum_rmlui_add_test(vellum-rmlui-log-sink-test log_sink_test.cpp VellumRmlUi::Core)
add_test(NAME vellum.rmlui.log-sink COMMAND vellum-rmlui-log-sink-test)

# Control for the open target: Core flattens layers and filters away.
vellum_rmlui_add_test(vellum-rmlui-core-flattening-test core_flattening_test.cpp VellumRmlUi::Core)
add_test(NAME vellum.rmlui.core-flattening COMMAND vellum-rmlui-core-flattening-test)

if(VELLUM_RMLUI_CPU)
    vellum_rmlui_add_test(vellum-rmlui-cpu-render-test cpu_render_test.cpp VellumRmlUi::Cpu)
    add_test(NAME vellum.rmlui.cpu-render COMMAND vellum-rmlui-cpu-render-test)
endif()

if(VELLUM_RMLUI_NATIVE)
    vellum_rmlui_add_test(vellum-rmlui-native-test native_test.cpp VellumRmlUi::Native)
    add_test(NAME vellum.rmlui.native COMMAND vellum-rmlui-native-test)

    # The font engine's text width against CoreText's fractional layout of
    # the same font file. Apple only: the reference and the fonts (Arial,
    # Verdana) are the system's.
    if(APPLE AND VELLUM_RMLUI_FONTS)
        enable_language(OBJCXX)
        vellum_rmlui_add_test(vellum-rmlui-native-text-width-test native_text_width_test.mm
            VellumRmlUi::Native)
        target_compile_options(vellum-rmlui-native-text-width-test PRIVATE -fobjc-arc)
        target_link_libraries(vellum-rmlui-native-text-width-test PRIVATE
            "-framework CoreText" "-framework CoreGraphics" "-framework Foundation")
        add_test(NAME vellum.rmlui.native-text-width COMMAND vellum-rmlui-native-text-width-test)
        set_tests_properties(vellum.rmlui.native-text-width PROPERTIES SKIP_RETURN_CODE 77)
    endif()

    # A click into a text input, with the field's font resolved and without.
    vellum_rmlui_add_test(vellum-rmlui-native-text-input-test native_text_input_test.cpp
        VellumRmlUi::Native)
    if(VELLUM_RMLUI_FONTS AND EXISTS "${_vellum_rmlui_test_font}")
        target_compile_definitions(vellum-rmlui-native-text-input-test PRIVATE
            VELLUM_RMLUI_TEST_FONT="${_vellum_rmlui_test_font}"
            VELLUM_RMLUI_TEST_FONT_FAMILY="Inter")
    else()
        target_compile_definitions(vellum-rmlui-native-text-input-test PRIVATE
            VELLUM_RMLUI_TEST_FONT_FAMILY="")
    endif()
    add_test(NAME vellum.rmlui.native-text-input COMMAND vellum-rmlui-native-text-input-test)
    set_tests_properties(vellum.rmlui.native-text-input PROPERTIES SKIP_RETURN_CODE 77)
endif()

# The Metal renderer drawn headless and compared with the soft renderer. The
# reference and the Metal frames come from separate processes (Core and Native
# each carry their own RmlUi), joined by a ctest fixture. The blend control is
# the same test with a straight-alpha blend compiled in; it must detect it.
if(VELLUM_RMLUI_METAL AND VELLUM_RMLUI_FONTS)
    FetchContent_GetProperties(vellum_rmlui_rmlui SOURCE_DIR _vellum_rmlui_rmlui_src)
    set(_metal_refs "${CMAKE_CURRENT_BINARY_DIR}/metal-references")
    vellum_rmlui_add_test(vellum-rmlui-metal-reference metal/soft_reference.mm
        VellumRmlUi::SoftRenderer "-framework Foundation" "-framework CoreGraphics"
        "-framework ImageIO")
    target_compile_options(vellum-rmlui-metal-reference PRIVATE -fobjc-arc)
    add_test(NAME vellum.rmlui.metal-reference
        COMMAND vellum-rmlui-metal-reference "${_vellum_rmlui_rmlui_src}" "${_vellum_rmlui_fixtures}"
            "${_metal_refs}")
    set_tests_properties(vellum.rmlui.metal-reference PROPERTIES
        FIXTURES_SETUP vellum_rmlui_metal_references)

    vellum_rmlui_add_test(vellum-rmlui-metal-visual-test metal/metal_visual_test.mm
        VellumRmlUi::Native VellumRmlUi::Metal)
    target_compile_options(vellum-rmlui-metal-visual-test PRIVATE -fobjc-arc)
    add_test(NAME vellum.rmlui.metal-visual
        COMMAND vellum-rmlui-metal-visual-test "${_vellum_rmlui_rmlui_src}" "${_vellum_rmlui_fixtures}"
            "${_metal_refs}")
    set_tests_properties(vellum.rmlui.metal-visual PROPERTIES
        FIXTURES_REQUIRED vellum_rmlui_metal_references
        SKIP_RETURN_CODE 77)

    vellum_rmlui_add_test(vellum-rmlui-metal-blend-control metal/metal_visual_test.mm
        VellumRmlUi::Native)
    target_sources(vellum-rmlui-metal-blend-control PRIVATE
        "${PROJECT_SOURCE_DIR}/src/metal_render_interface.mm"
        "${PROJECT_SOURCE_DIR}/src/metal_effects.cpp")
    target_compile_definitions(vellum-rmlui-metal-blend-control PRIVATE
        VELLUM_RMLUI_METAL_TEST_STRAIGHT_ALPHA_BLEND)
    target_compile_options(vellum-rmlui-metal-blend-control PRIVATE $<$<COMPILE_LANGUAGE:OBJCXX>:-fobjc-arc>)
    target_link_libraries(vellum-rmlui-metal-blend-control PRIVATE
        "-framework Metal" "-framework QuartzCore" "-framework Foundation"
        "-framework CoreGraphics" "-framework ImageIO")
    vellum_rmlui_add_test(vellum-rmlui-metal-layers-test metal/metal_layers_test.mm
        VellumRmlUi::Native VellumRmlUi::Metal)
    target_compile_options(vellum-rmlui-metal-layers-test PRIVATE -fobjc-arc)
    add_test(NAME vellum.rmlui.metal-layers
        COMMAND vellum-rmlui-metal-layers-test "${_vellum_rmlui_rmlui_src}" "${_vellum_rmlui_fixtures}")
    set_tests_properties(vellum.rmlui.metal-layers PROPERTIES SKIP_RETURN_CODE 77)

    vellum_rmlui_add_test(vellum-rmlui-metal-filters-test metal/metal_filters_test.mm
        VellumRmlUi::Native VellumRmlUi::Metal)
    target_compile_options(vellum-rmlui-metal-filters-test PRIVATE -fobjc-arc)
    add_test(NAME vellum.rmlui.metal-filters
        COMMAND vellum-rmlui-metal-filters-test "${_vellum_rmlui_rmlui_src}" "${_vellum_rmlui_fixtures}")
    set_tests_properties(vellum.rmlui.metal-filters PROPERTIES SKIP_RETURN_CODE 77)

    vellum_rmlui_add_test(vellum-rmlui-metal-effects-test metal/metal_effects_test.cpp
        VellumRmlUi::Metal)
    target_include_directories(vellum-rmlui-metal-effects-test PRIVATE "${PROJECT_SOURCE_DIR}/src")
    add_test(NAME vellum.rmlui.metal-effects COMMAND vellum-rmlui-metal-effects-test)

    add_test(NAME vellum.rmlui.metal-blend-control
        COMMAND vellum-rmlui-metal-blend-control "${_vellum_rmlui_rmlui_src}" "${_vellum_rmlui_fixtures}"
            "${_metal_refs}")
    set_tests_properties(vellum.rmlui.metal-blend-control PROPERTIES
        FIXTURES_REQUIRED vellum_rmlui_metal_references
        SKIP_RETURN_CODE 77)
endif()

vellum_rmlui_add_test(vellum-rmlui-hot-reload-test hot_reload_test.cpp VellumRmlUi::DevAssets)
add_test(NAME vellum.rmlui.hot-reload COMMAND vellum-rmlui-hot-reload-test)

# The golden image is the font-off rendering; a FreeType build lays out the
# same document identically, since it holds no text.
vellum_rmlui_add_test(vellum-rmlui-soft-render-golden-test soft_render_golden_test.cpp
    VellumRmlUi::SoftRenderer)
add_test(NAME vellum.rmlui.soft-render-golden COMMAND vellum-rmlui-soft-render-golden-test)
set_tests_properties(vellum.rmlui.soft-render-golden PROPERTIES
    WORKING_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}")

if(VELLUM_RMLUI_VELLUM_RENDERER)
    vellum_rmlui_add_test(vellum-rmlui-vellum-renderer-test vellum_renderer_test.cpp
        VellumRmlUi::VellumRenderer VellumRmlUi::SoftRenderer Vellum::DawnHeaders)
    if(APPLE)
        set_target_properties(vellum-rmlui-vellum-renderer-test PROPERTIES
            OSX_ARCHITECTURES arm64
            BUILD_RPATH "$<TARGET_FILE_DIR:Vellum::Gpu>")
    endif()
    add_test(NAME vellum.rmlui.vellum-renderer COMMAND vellum-rmlui-vellum-renderer-test)
endif()

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
    if(APPLE)
        # Controls for the deployment-target check the install tests run. An
        # object newer than the recorded target must fail it in either load
        # command form; an older object in the legacy form must pass it. Each
        # matches the check's own output, so a broken setup cannot pass.
        foreach(_control IN ITEMS "build;27.0;fail" "legacy;27.0;fail" "legacy;10.13;pass")
            list(GET _control 0 _form)
            list(GET _control 1 _version)
            list(GET _control 2 _expect)
            set(_name "vellum.rmlui.deployment-target-${_form}-${_version}")
            add_test(NAME "${_name}"
                COMMAND "${CMAKE_COMMAND}"
                    "-DVELLUM_RMLUI_CONTROL_DIR=${CMAKE_CURRENT_BINARY_DIR}/deployment-target-${_form}-${_version}"
                    "-DVELLUM_RMLUI_C_COMPILER=${CMAKE_C_COMPILER}"
                    "-DVELLUM_RMLUI_CONTROL_FORM=${_form}"
                    "-DVELLUM_RMLUI_CONTROL_VERSION=${_version}"
                    -P "${CMAKE_CURRENT_SOURCE_DIR}/PlantNewerObject.cmake")
            string(REPLACE "." "\\." _version_regex "${_version}")
            if(_expect STREQUAL "fail")
                set_tests_properties("${_name}" PROPERTIES
                    PASS_REGULAR_EXPRESSION "deployment-target-too-new: macOS ${_version_regex} > 13\\.3")
            else()
                set_tests_properties("${_name}" PROPERTIES
                    PASS_REGULAR_EXPRESSION "deployment-target: 1 object build versions checked against macOS 13\\.3")
            endif()
        endforeach()
    endif()
    if(VELLUM_RMLUI_NATIVE)
        add_test(NAME vellum.rmlui.install-consumer-native
            COMMAND "${CMAKE_COMMAND}"
                "-DVELLUM_RMLUI_BUILD_DIR=${PROJECT_BINARY_DIR}"
                "-DVELLUM_RMLUI_SOURCE_DIR=${PROJECT_SOURCE_DIR}"
                "-DVELLUM_RMLUI_INSTALL_LIBDIR=${CMAKE_INSTALL_LIBDIR}"
                "-DVELLUM_RMLUI_CONSUMER=native"
                "-DVELLUM_RMLUI_FONTS=${VELLUM_RMLUI_FONTS}"
                "-DTEST_CONFIG=$<CONFIG>"
                -P "${CMAKE_CURRENT_SOURCE_DIR}/RunInstallConsumer.cmake")
    endif()
endif()
