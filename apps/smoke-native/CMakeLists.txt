add_executable(vellum-smoke-native MACOSX_BUNDLE
    main.mm)

target_compile_features(vellum-smoke-native PRIVATE cxx_std_20)
target_link_libraries(vellum-smoke-native PRIVATE
    Vellum::Graphics
    "-framework Cocoa"
    "-framework CoreGraphics")

set_target_properties(vellum-smoke-native PROPERTIES
    MACOSX_BUNDLE_BUNDLE_NAME "Vellum Smoke"
    MACOSX_BUNDLE_GUI_IDENTIFIER "dev.vellum.smoke-native"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
    MACOSX_BUNDLE_BUNDLE_VERSION "1")

if(VELLUM_BUILD_TESTS)
    add_test(NAME vellum.smoke-native
        COMMAND $<TARGET_FILE:vellum-smoke-native> --self-test)
endif()
