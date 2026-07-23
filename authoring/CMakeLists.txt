add_library(vellum-authoring SHARED
    src/js_application.mm)
add_library(Vellum::Authoring ALIAS vellum-authoring)

set_target_properties(vellum-authoring PROPERTIES
    EXPORT_NAME Authoring
    MACOSX_RPATH ON
    INSTALL_RPATH "@loader_path")

target_include_directories(vellum-authoring
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)
target_compile_features(vellum-authoring PUBLIC cxx_std_20)
target_compile_options(vellum-authoring PRIVATE -fobjc-arc)
target_link_libraries(vellum-authoring
    PUBLIC Vellum::Graphics
    PRIVATE
        "-framework Foundation"
        "-framework JavaScriptCore")
