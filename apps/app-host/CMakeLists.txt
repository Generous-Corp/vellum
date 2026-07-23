add_executable(vellum-app-host
    main.mm)

target_compile_features(vellum-app-host PRIVATE cxx_std_20)
target_compile_options(vellum-app-host PRIVATE -fobjc-arc)
target_link_libraries(vellum-app-host PRIVATE
    Vellum::Authoring
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
    add_test(NAME vellum.app-host
        COMMAND vellum-app-host
            --bundle "${VELLUM_AUTHORING_TEST_BUNDLE}"
            --self-test
            --press native-increment
            --input native-title-input "Native board"
            --key native-title-input Enter)
    set_tests_properties(vellum.app-host PROPERTIES
        ENVIRONMENT "MTL_DEBUG_LAYER=0")
    add_test(NAME vellum.app-host-state-persistence
        COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/test_state_persistence.py"
            "$<TARGET_FILE:vellum-app-host>"
            "${VELLUM_AUTHORING_TEST_BUNDLE}")
    set_tests_properties(vellum.app-host-state-persistence PROPERTIES
        ENVIRONMENT "MTL_DEBUG_LAYER=0")
endif()
