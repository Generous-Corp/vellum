add_executable(vellum-app-host
    main.mm)

target_compile_features(vellum-app-host PRIVATE cxx_std_20)
target_compile_options(vellum-app-host PRIVATE -fobjc-arc)
# ld64 derives LC_UUID from link-time state, which also changes its automatic
# ad-hoc signature. The installed host is content-addressed, so omit that
# non-semantic identifier and let release packaging add its final signature.
target_link_options(vellum-app-host PRIVATE -Wl,-no_uuid)
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
    add_test(NAME vellum.app-host
        COMMAND vellum-app-host
            --bundle "${VELLUM_AUTHORING_TEST_BUNDLE}"
            --self-test
            --press native-increment)
    set_tests_properties(vellum.app-host PROPERTIES
        ENVIRONMENT "MTL_DEBUG_LAYER=0")
endif()
