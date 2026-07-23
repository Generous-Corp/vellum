add_executable(vellum-gpu-native MACOSX_BUNDLE
    main.mm)

target_compile_features(vellum-gpu-native PRIVATE cxx_std_20)
target_link_libraries(vellum-gpu-native PRIVATE
    Vellum::Gpu
    "-framework Cocoa"
    "-framework Metal"
    "-framework QuartzCore")

set_target_properties(vellum-gpu-native PROPERTIES
    MACOSX_BUNDLE_BUNDLE_NAME "Vellum GPU Proof"
    MACOSX_BUNDLE_GUI_IDENTIFIER "dev.vellum.gpu-proof"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
    MACOSX_BUNDLE_BUNDLE_VERSION "1"
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET 15.0
    BUILD_RPATH "$<TARGET_FILE_DIR:vellum-gpu>")

if(VELLUM_BUILD_TESTS)
    add_test(NAME vellum.gpu-native
        COMMAND $<TARGET_FILE:vellum-gpu-native> --self-test)
    set_tests_properties(vellum.gpu-native PROPERTIES
        ENVIRONMENT "MTL_DEBUG_LAYER=0")
endif()
