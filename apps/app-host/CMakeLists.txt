add_executable(vellum-app-host
    main.mm
    macos_accessibility.mm
    options.cpp
    text_semantics.mm
    component_registry.cpp)

target_compile_features(vellum-app-host PRIVATE cxx_std_20)
target_compile_options(vellum-app-host PRIVATE -fobjc-arc)
target_link_libraries(vellum-app-host PRIVATE
    Vellum::Authoring
    Vellum::ComponentAbi
    Vellum::Gpu
    "-framework Cocoa"
    "-framework Metal"
    "-framework QuartzCore")

set_target_properties(vellum-app-host PROPERTIES
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET 15.0
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-authoring>;$<TARGET_FILE_DIR:vellum-gpu>"
    INSTALL_RPATH "@executable_path/../lib;@executable_path/../Frameworks")

install(TARGETS vellum-app-host
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")

if(VELLUM_BUILD_TESTS AND VELLUM_AUTHORING_TEST_BUNDLE)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    find_program(VELLUM_NODE_EXECUTABLE NAMES node)
    add_test(NAME vellum.app-host
        COMMAND vellum-app-host
            --bundle "${VELLUM_AUTHORING_TEST_BUNDLE}"
            --self-test
            --press native-increment
            --focus native-title-input
            --input native-title-input "Native board"
            --key native-title-input Enter
            --compose native-title-input "日本語"
            --assert-accessibility native-title-input
                "{\"label\":\"Board title\",\"role\":\"text-field\",\"value\":\"Native board日本語\"}")
    set_tests_properties(vellum.app-host PROPERTIES
        ENVIRONMENT "MTL_DEBUG_LAYER=0")
    add_test(NAME vellum.app-host-state-persistence
        COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/test_state_persistence.py"
            "$<TARGET_FILE:vellum-app-host>"
            "${VELLUM_AUTHORING_TEST_BUNDLE}")
    set_tests_properties(vellum.app-host-state-persistence PROPERTIES
        ENVIRONMENT "MTL_DEBUG_LAYER=0")
    if(VELLUM_NODE_EXECUTABLE)
        add_test(NAME vellum.app-host-text-ime-accessibility
            COMMAND "${Python3_EXECUTABLE}"
                "${CMAKE_CURRENT_SOURCE_DIR}/test_text_semantics.py"
                "$<TARGET_FILE:vellum-app-host>"
                "${PROJECT_SOURCE_DIR}"
                "${VELLUM_NODE_EXECUTABLE}")
        set_tests_properties(vellum.app-host-text-ime-accessibility PROPERTIES
            ENVIRONMENT "MTL_DEBUG_LAYER=0")
    endif()
endif()

if(VELLUM_BUILD_TESTS AND VELLUM_PHASE3_TEST_BUNDLE)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME vellum.app-host-phase3-scenario
        COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/test_phase3_scenario.py"
            "$<TARGET_FILE:vellum-app-host>"
            "${PROJECT_SOURCE_DIR}"
            "${VELLUM_PHASE3_TEST_BUNDLE}")
    set_tests_properties(vellum.app-host-phase3-scenario PROPERTIES
        ENVIRONMENT "MTL_DEBUG_LAYER=0")
endif()

if(VELLUM_BUILD_TESTS)
    add_executable(vellum-macos-accessibility-test
        macos_accessibility.mm
        tests/macos_accessibility_test.mm)
    target_compile_options(vellum-macos-accessibility-test PRIVATE -fobjc-arc)
    target_link_libraries(vellum-macos-accessibility-test PRIVATE
        Vellum::Authoring
        "-framework Cocoa")
    add_test(NAME vellum.macos-accessibility
        COMMAND vellum-macos-accessibility-test)

    add_library(vellum-unterminated-component MODULE
        tests/unterminated_component.cpp)
    target_link_libraries(vellum-unterminated-component PRIVATE Vellum::ComponentAbi)
    set_target_properties(vellum-unterminated-component PROPERTIES
        PREFIX ""
        CXX_VISIBILITY_PRESET hidden)
    add_library(vellum-unterminated-text-component MODULE
        tests/unterminated_text_component.cpp)
    target_link_libraries(vellum-unterminated-text-component PRIVATE Vellum::ComponentAbi)
    set_target_properties(vellum-unterminated-text-component PROPERTIES
        PREFIX ""
        CXX_VISIBILITY_PRESET hidden)

    add_executable(vellum-component-registry-test
        component_registry.cpp
        tests/component_registry_test.cpp)
    target_include_directories(vellum-component-registry-test PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}")
    target_link_libraries(vellum-component-registry-test PRIVATE
        Vellum::ComponentAbi
        Vellum::Graphics
        ${CMAKE_DL_LIBS})
    add_test(NAME vellum.component-registry-bounds
        COMMAND vellum-component-registry-test
            "$<TARGET_FILE:vellum-unterminated-component>"
            "$<TARGET_FILE:vellum-unterminated-text-component>")
endif()
